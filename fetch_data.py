"""Check the currency of local copies of the article's data sources and download stale ones.

Sources (as cited on the charts in
https://www.dailysignal.com/2026/09/30/real-story-gas-prices/):

  eia_gasoline  EIA  "U.S. Regular All Formulations Retail Gasoline Prices", monthly $/gal
  eia_gasoline_weekly  EIA  the same series at weekly frequency (Monday observations), $/gal
  bls_cpi       BLS  CPI-U All Items, U.S. city average, not seasonally adjusted (CUUR0000SA0)
  lfpr_25_54    FRED/BLS  Labor force participation rate, 25-54 years (LNS11300060)
  census_h06    Census  Table H-6, median and mean household income, current and real dollars

Usage from a shell:
    uv run python fetch_data.py              # refresh anything stale
    uv run python fetch_data.py --check      # report only, download nothing
    uv run python fetch_data.py --force      # re-download everything
    uv run python fetch_data.py eia_gasoline bls_cpi   # only these sources

Usage from the notebook:
    import fetch_data
    fetch_data.refresh()                     # same as the CLI default
    gas = fetch_data.load("eia_gasoline")    # tidy polars DataFrame

A source is considered stale when any of these hold:
  * no local copy exists,
  * the remote file's Last-Modified / ETag differs from the one recorded at download time,
  * the latest period in the local copy is older than the period that should be
    published by now (given the source's typical release lag), and the local copy
    was fetched more than a day ago.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx
import polars as pl

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
MANIFEST = DATA_DIR / "manifest.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36 gas-prices-replication"
    )
}
TIMEOUT = httpx.Timeout(60.0, connect=15.0)

EIA_XLS_URL = "https://www.eia.gov/dnav/pet/hist_xls/EMM_EPMR_PTE_NUS_DPGm.xls"
EIA_PAGE_URL = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=EMM_EPMR_PTE_NUS_DPG&f=M"
EIA_WEEKLY_XLS_URL = "https://www.eia.gov/dnav/pet/hist_xls/EMM_EPMR_PTE_NUS_DPGw.xls"
EIA_WEEKLY_PAGE_URL = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=EMM_EPMR_PTE_NUS_DPG&f=W"
BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
CENSUS_H06_URL = (
    "https://www2.census.gov/programs-surveys/cps/tables/time-series/"
    "historical-income-households/h06ar.xlsx"
)


# --------------------------------------------------------------------------- helpers


def _today() -> dt.date:
    return dt.date.today()


def _month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def expected_latest_month(lag_days: int, today: dt.date | None = None) -> dt.date:
    """First day of the most recent month whose data should be published by now."""
    today = today or _today()
    return _month_start(today - dt.timedelta(days=lag_days))


def _head(url: str) -> dict[str, str | None]:
    """Return remote Last-Modified / ETag, or empty values if the server can't be reached."""
    try:
        r = httpx.head(url, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
        r.raise_for_status()
        return {"last_modified": r.headers.get("last-modified"), "etag": r.headers.get("etag")}
    except httpx.HTTPError:
        return {"last_modified": None, "etag": None}


def _download(url: str, dest: Path) -> dict[str, str | None]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
        return {"last_modified": r.headers.get("last-modified"), "etag": r.headers.get("etag")}


def _bls_series(series_id: str, start_year: int, end_year: int) -> pl.DataFrame:
    """Fetch a monthly BLS series via the public API v2.

    Without a key the API allows 10 years per request and 25 requests per day, so the
    range is chunked. Set BLS_API_KEY in the environment to raise those limits.
    Unpublished months (e.g. October 2025, the lapse in appropriations) come back
    as "-" and are kept as null values with the footnote text.
    """
    key = os.environ.get("BLS_API_KEY")
    rows: list[tuple[dt.date, float | None, str | None]] = []
    for a in range(start_year, end_year + 1, 10):
        b = min(a + 9, end_year)
        payload: dict = {"seriesid": [series_id], "startyear": str(a), "endyear": str(b)}
        if key:
            payload["registrationkey"] = key
        r = httpx.post(BLS_API_URL, json=payload, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(f"BLS API error for {series_id}: {body.get('message')}")
        for d in body["Results"]["series"][0]["data"]:
            period = d["period"]
            if not period.startswith("M") or period == "M13":
                continue  # skip annual averages
            date = dt.date(int(d["year"]), int(period[1:]), 1)
            try:
                value: float | None = float(d["value"])
                note = None
            except ValueError:
                value = None
                note = "; ".join(fn.get("text", "") for fn in d.get("footnotes", []) if fn) or d["value"]
            rows.append((date, value, note))
    return (
        pl.DataFrame(rows, schema={"date": pl.Date, "value": pl.Float64, "note": pl.Utf8}, orient="row")
        .unique(subset="date")
        .sort("date")
    )


def _fred_series(series_id: str) -> pl.DataFrame:
    url = FRED_CSV_URL.format(series=series_id)
    r = httpx.get(url, headers=HEADERS, timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=True)
    r.raise_for_status()
    df = pl.read_csv(r.content, null_values=["."])
    date_col, val_col = df.columns[0], df.columns[1]
    return (
        df.rename({date_col: "date", val_col: "value"})
        .with_columns(pl.col("date").str.to_date(), pl.col("value").cast(pl.Float64), pl.lit(None, pl.Utf8).alias("note"))
        .sort("date")
    )


# --------------------------------------------------------------------------- sources


@dataclass
class Status:
    key: str
    stale: bool
    reasons: list[str] = field(default_factory=list)
    local_latest: str | None = None
    expected_latest: str | None = None
    fetched_at: str | None = None
    refreshed: bool = False
    error: str | None = None

    def __str__(self) -> str:
        state = "REFRESHED" if self.refreshed else ("STALE" if self.stale else "current")
        if self.error:
            state = f"ERROR ({self.error})"
        why = f" [{'; '.join(self.reasons)}]" if self.reasons and not self.refreshed else ""
        return f"{self.key:<14} {state:<10} latest={self.local_latest or '-':<10} fetched={self.fetched_at or '-'}{why}"


@dataclass
class Source:
    key: str
    name: str
    citation: str
    local: str  # tidy CSV filename under data/
    fetch: Callable[["Source"], tuple[pl.DataFrame, dict]]  # returns (tidy df, extra manifest info)
    period_col: str = "date"
    lag_days: int | None = None  # typical publication lag for the newest period; None = no period check
    head_url: str | None = None  # URL whose Last-Modified / ETag signals a new release

    @property
    def path(self) -> Path:
        return DATA_DIR / self.local


def _fetch_eia_xls(url: str, raw_name: str, truncate: str | None) -> tuple[pl.DataFrame, dict]:
    """Download one of EIA's history workbooks and tidy its "Data 1" sheet to (date, price)."""
    import fastexcel

    raw = RAW_DIR / raw_name
    hdr = _download(url, raw)
    sheet = fastexcel.read_excel(raw).load_sheet("Data 1", header_row=2).to_polars()
    sheet.columns = ["date", "price_usd_per_gal"]
    date = pl.col("date").cast(pl.Date)
    df = (
        sheet.with_columns(
            date.dt.truncate(truncate) if truncate else date,
            pl.col("price_usd_per_gal").cast(pl.Float64, strict=False),
        )
        .drop_nulls()
        .sort("date")
    )
    return df, {"url": url, "series": "EMM_EPMR_PTE_NUS_DPG", "raw": str(raw.relative_to(ROOT)), **hdr}


def fetch_eia(src: Source) -> tuple[pl.DataFrame, dict]:
    return _fetch_eia_xls(EIA_XLS_URL, "EMM_EPMR_PTE_NUS_DPGm.xls", "1mo")


def fetch_eia_weekly(src: Source) -> tuple[pl.DataFrame, dict]:
    """Weekly prices are point observations taken on Mondays; the date is kept as published."""
    return _fetch_eia_xls(EIA_WEEKLY_XLS_URL, "EMM_EPMR_PTE_NUS_DPGw.xls", None)


def fetch_bls_cpi(src: Source) -> tuple[pl.DataFrame, dict]:
    df = _bls_series("CUUR0000SA0", 1990, _today().year).rename({"value": "cpi_u_nsa"})
    return df, {"url": BLS_API_URL, "series": "CUUR0000SA0"}


def fetch_lfpr(src: Source) -> tuple[pl.DataFrame, dict]:
    """FRED is the cited source; fall back to the BLS API (the originating agency) if FRED is unreachable."""
    try:
        df = _fred_series("LNS11300060")
        info = {"url": FRED_CSV_URL.format(series="LNS11300060"), "series": "LNS11300060", "provider": "FRED"}
    except httpx.HTTPError as exc:
        print(f"  FRED unreachable ({type(exc).__name__}); falling back to the BLS API", file=sys.stderr)
        df = _bls_series("LNS11300060", 1980, _today().year)
        info = {"url": BLS_API_URL, "series": "LNS11300060", "provider": "BLS API (FRED fallback)"}
    df = df.filter(pl.col("date") >= dt.date(1980, 1, 1)).rename({"value": "lfpr_25_54_pct"})
    return df, info


_YEAR_RE = re.compile(r"^\s*(\d{4})\s*(?:\((\d+)\))?\s*$")


def fetch_census_h06(src: Source) -> tuple[pl.DataFrame, dict]:
    import fastexcel

    raw = RAW_DIR / "h06ar.xlsx"
    hdr = _download(CENSUS_H06_URL, raw)
    sheet = fastexcel.read_excel(raw).load_sheet(0, header_row=None).to_polars()
    col0 = sheet[:, 0].to_list()

    # Locate the "United States" block and the "<year> dollars" header to learn the base year.
    start = next(i for i, v in enumerate(col0) if isinstance(v, str) and v.strip() == "United States")
    base_year = None
    for i in range(start, start + 4):
        for cell in sheet.row(i):
            if isinstance(cell, str) and (m := re.search(r"(\d{4})\s+dollars", cell)):
                base_year = int(m.group(1))
                break
        if base_year:
            break

    rows = []
    started = False
    for i in range(start, len(col0)):
        v = col0[i]
        m = _YEAR_RE.match(v) if isinstance(v, str) else None
        if m:
            started = True
            r = sheet.row(i)
            rows.append(
                (
                    int(m.group(1)),
                    int(m.group(2)) if m.group(2) else None,
                    *[float(str(x).replace(",", "")) if x not in (None, "") else None for x in r[1:6]],
                )
            )
        elif started:
            break  # end of the United States block

    df = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "footnote": pl.Int32,
            "households_thousands": pl.Float64,
            "median_current_usd": pl.Float64,
            "median_real_usd": pl.Float64,
            "mean_current_usd": pl.Float64,
            "mean_real_usd": pl.Float64,
        },
        orient="row",
    ).sort("year", "footnote", descending=[False, True])
    return df, {"url": CENSUS_H06_URL, "raw": str(raw.relative_to(ROOT)), "real_dollars_base_year": base_year, **hdr}


SOURCES: dict[str, Source] = {
    s.key: s
    for s in [
        Source(
            key="eia_gasoline",
            name="EIA U.S. Regular All Formulations Retail Gasoline Prices (monthly, $/gal)",
            citation=EIA_PAGE_URL,
            local="eia_gasoline_monthly.csv",
            fetch=fetch_eia,
            lag_days=10,  # a month's average is posted within days of month end
            head_url=EIA_XLS_URL,
        ),
        Source(
            key="eia_gasoline_weekly",
            name="EIA U.S. Regular All Formulations Retail Gasoline Prices (weekly, $/gal)",
            citation=EIA_WEEKLY_PAGE_URL,
            local="eia_gasoline_weekly.csv",
            fetch=fetch_eia_weekly,
            lag_days=10,  # posted every Monday afternoon; Last-Modified catches each release
            head_url=EIA_WEEKLY_XLS_URL,
        ),
        Source(
            key="bls_cpi",
            name="BLS CPI-U All Items, U.S. city average, NSA (CUUR0000SA0)",
            citation="https://data.bls.gov/timeseries/CUUR0000SA0",
            local="bls_cpi_u_nsa.csv",
            fetch=fetch_bls_cpi,
            lag_days=45,  # CPI for month M is released around the 10th-15th of M+1
        ),
        Source(
            key="lfpr_25_54",
            name="Labor force participation rate, 25-54 years, SA (LNS11300060)",
            citation="https://fred.stlouisfed.org/series/LNS11300060",
            local="lfpr_25_54.csv",
            fetch=fetch_lfpr,
            lag_days=14,  # Employment Situation comes out the first Friday of M+1
        ),
        Source(
            key="census_h06",
            name="Census Table H-6, household income, all races (real and current dollars)",
            citation="https://www.census.gov/library/publications/2026/demo/p60-289.html",
            local="census_h06_household_income.csv",
            fetch=fetch_census_h06,
            period_col="year",
            lag_days=None,  # annual, released each September; rely on Last-Modified
            head_url=CENSUS_H06_URL,
        ),
    ]
}


# --------------------------------------------------------------------------- manifest + status


def _read_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {}


def _write_manifest(m: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")


def _local_latest(src: Source) -> str | None:
    if not src.path.exists():
        return None
    df = pl.read_csv(src.path, try_parse_dates=True)
    if src.period_col not in df.columns or df.is_empty():
        return None
    # Only count periods that actually have a value (ignore trailing unpublished months).
    value_cols = [c for c in df.columns if c not in (src.period_col, "footnote", "note")]
    df = df.filter(pl.any_horizontal([pl.col(c).is_not_null() for c in value_cols]))
    return str(df[src.period_col].max())


def check(key: str, today: dt.date | None = None) -> Status:
    src = SOURCES[key]
    entry = _read_manifest().get(key, {})
    st = Status(key=key, stale=False, fetched_at=entry.get("fetched_at"))
    st.local_latest = _local_latest(src)

    if st.local_latest is None:
        st.stale = True
        st.reasons.append("no local copy")
        return st

    if src.head_url:
        remote = _head(src.head_url)
        if remote["last_modified"] and remote["last_modified"] != entry.get("last_modified"):
            st.stale = True
            st.reasons.append(f"remote modified {remote['last_modified']}")
        elif remote["etag"] and entry.get("etag") and remote["etag"] != entry.get("etag"):
            st.stale = True
            st.reasons.append("remote ETag changed")

    if src.lag_days is not None:
        expected = expected_latest_month(src.lag_days, today)
        st.expected_latest = str(expected)
        fetched = dt.datetime.fromisoformat(entry["fetched_at"]) if entry.get("fetched_at") else None
        age_ok = fetched is None or (dt.datetime.now(dt.timezone.utc) - fetched) > dt.timedelta(days=1)
        if dt.date.fromisoformat(st.local_latest) < expected and age_ok:
            st.stale = True
            st.reasons.append(f"latest {st.local_latest} < expected {expected}")
    return st


def download(key: str) -> Status:
    src = SOURCES[key]
    st = Status(key=key, stale=True)
    try:
        df, info = src.fetch(src)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        df.write_csv(src.path)
        m = _read_manifest()
        m[key] = {
            "name": src.name,
            "citation": src.citation,
            "local": str(src.path.relative_to(ROOT)),
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "rows": df.height,
            "latest_period": str(df[src.period_col].max()),
            **{k: v for k, v in info.items() if v is not None},
        }
        _write_manifest(m)
        st.refreshed = True
        st.stale = False
        st.local_latest = m[key]["latest_period"]
        st.fetched_at = m[key]["fetched_at"]
    except Exception as exc:  # keep going for the other sources
        st.error = f"{type(exc).__name__}: {exc}"
    return st


def refresh(keys: list[str] | None = None, force: bool = False, check_only: bool = False, quiet: bool = False) -> dict[str, Status]:
    """Check every source (or the given keys) and download the stale ones."""
    results: dict[str, Status] = {}
    for key in keys or list(SOURCES):
        st = check(key)
        if (st.stale or force) and not check_only:
            if not quiet:
                print(f"{key:<14} downloading ... ({'forced' if force and not st.stale else '; '.join(st.reasons)})")
            st = download(key)
        results[key] = st
        if not quiet:
            print(st)
    return results


def load(key: str) -> pl.DataFrame:
    """Load a tidy local copy, downloading it first if it does not exist."""
    src = SOURCES[key]
    if not src.path.exists():
        st = download(key)
        if st.error:
            raise RuntimeError(f"could not fetch {key}: {st.error}")
    return pl.read_csv(src.path, try_parse_dates=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("keys", nargs="*", choices=[*SOURCES, []], help="sources to process (default: all)")
    ap.add_argument("--force", action="store_true", help="re-download even if current")
    ap.add_argument("--check", action="store_true", help="only report currency, download nothing")
    args = ap.parse_args(argv)
    results = refresh(args.keys or None, force=args.force, check_only=args.check)
    return 1 if any(s.error for s in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
