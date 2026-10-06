# gas-prices

Replication of the four charts in the Daily Signal article
["The Real Story About Gas Prices, National Security, and the Economy"](https://www.dailysignal.com/2026/09/30/real-story-gas-prices/)
(Sanjai Bhagat, 30 Sep 2026), rebuilt from the primary sources the charts cite.

Everything runs inside a [uv](https://docs.astral.sh/uv/)-managed virtual environment with
JupyterLab, polars, numpy and matplotlib. Shell examples below are written for **nushell**.

## What is here

| Path | Purpose |
|---|---|
| `fetch_data.py` | Checks whether local copies of each data source are current and downloads the stale ones. Importable module and CLI. |
| `charts.py` | Shared chart styling, the administration windows, and the `bar_chart` / `line_chart` helpers the notebooks call. |
| `01_gas_price_nominal.ipynb` | Chart 1: average gasoline price by administration, nominal dollars. |
| `02_gas_price_real.ipynb` | Part 1: chart 2, the same in August 2026 dollars (CPI-U deflated, October 2025 gap interpolated). Part 2: own analysis, starting with a weekly real-price series segmented from the base month. |
| `03_lfpr_25_54.ipynb` | Chart 3: labor force participation rate, ages 25-54, 1980 to present. |
| `04_income_increase.ipynb` | Chart 4: real average annual increase in median household income by administration. |
| `data/` | Tidy CSV copies of each source plus `manifest.json` (when fetched, remote Last-Modified, latest period). Created on first run. |
| `data/raw/` | The original EIA `.xls` and Census `.xlsx` downloads. Git-ignored. |
| `output/` | Rendered PNGs, one per chart. Created by the notebook. |
| `pyproject.toml`, `uv.lock` | Project dependencies, pinned. |

## Data sources

| Chart | Series | Publisher | Fetched from |
|---|---|---|---|
| 1. Nominal gas price by administration | U.S. Regular All Formulations Retail Gasoline Prices, monthly, $/gal (`EMM_EPMR_PTE_NUS_DPG`) | EIA | [EIA XLS download](https://www.eia.gov/dnav/pet/hist_xls/EMM_EPMR_PTE_NUS_DPGm.xls) ([series page](https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=EMM_EPMR_PTE_NUS_DPG&f=M)) |
| 2b. Weekly inflation-adjusted price (notebook 2, Part 2) | Same EIA series at weekly frequency (Monday observations), deflated with the CPI-U of each week's month | EIA, BLS | [EIA XLS download](https://www.eia.gov/dnav/pet/hist_xls/EMM_EPMR_PTE_NUS_DPGw.xls) ([series page](https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=EMM_EPMR_PTE_NUS_DPG&f=W)) |
| 2. Inflation-adjusted gas price | Chart 1 deflated with CPI-U All Items, U.S. city average, not seasonally adjusted (`CUUR0000SA0`) | BLS | [BLS Public Data API v2](https://api.bls.gov/publicAPI/v2/timeseries/data/) |
| 3. Labor force participation, ages 25-54 | `LNS11300060`, monthly, seasonally adjusted | FRED (data from BLS) | [FRED CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=LNS11300060), falling back to the BLS API if FRED is unreachable |
| 4. Real household income increase | Table H-6, median household income in current and 2025 dollars | Census Bureau ([P60-289](https://www.census.gov/library/publications/2026/demo/p60-289.html)) | [h06ar.xlsx](https://www2.census.gov/programs-surveys/cps/tables/time-series/historical-income-households/h06ar.xlsx) |

The article's charts credit "www.eia.gov", "www.eia.gov, www.bls.gov", "Federal Reserve Bank of St. Louis" and
"U.S. Census Bureau" respectively. The exact series above were identified by matching the chart titles to the
publishers' series names and by reproducing the article's figures.

## Setup

```nu
cd ~/Documents/repo/gas-prices
uv sync                      # creates .venv and installs everything from uv.lock
```

You can either prefix commands with `uv run`, or activate the environment once per shell:

```nu
overlay use .venv/bin/activate.nu     # activate
# ... work ...
deactivate                            # leave the overlay
```

The rest of this file uses `uv run`, which works without activating.

## Refreshing the data

```nu
uv run python fetch_data.py              # check every source, download what is stale
uv run python fetch_data.py --check      # report only, no downloads
uv run python fetch_data.py --force      # re-download everything
uv run python fetch_data.py eia_gasoline bls_cpi   # only the named sources
```

Source keys: `eia_gasoline`, `eia_gasoline_weekly`, `bls_cpi`, `lfpr_25_54`, `census_h06`.

A source counts as stale when any of these is true:

- there is no local copy;
- the remote file's `Last-Modified` or `ETag` differs from the one recorded at download (EIA and Census files);
- the newest period in the local copy is older than what the publisher should have released by now,
  given that source's usual lag, and the local copy is more than a day old (monthly series).

Example output:

```
eia_gasoline   current    latest=2026-09-01 fetched=2026-10-06T20:52:21+00:00
bls_cpi        current    latest=2026-08-01 fetched=2026-10-06T20:52:22+00:00
lfpr_25_54     current    latest=2026-09-01 fetched=2026-10-06T20:52:43+00:00
census_h06     current    latest=2025       fetched=2026-10-06T20:52:44+00:00
```

Notes:

- The BLS API without a key allows 25 requests per day and 10 years per request. The CPI pull uses 4 requests
  and the participation-rate fallback uses 5. If you hit the limit, register for a free key at
  <https://data.bls.gov/registrationEngine/> and export it before running:
  `$env.BLS_API_KEY = "your-key"`.
- BLS published no CPI for October 2025 (lapse in appropriations). The CSV keeps that row with a null value
  and the BLS footnote; the notebook interpolates it.
- FRED was not reachable from the machine this was built on, so chart 3 was drawn from the BLS API fallback.
  The values are identical because FRED republishes the BLS series.

## Rebuilding the charts

Each chart has its own notebook. Every notebook starts by calling `fetch_data.refresh()` for just the sources it
needs, loads the CSVs with polars, draws the chart through the helpers in `charts.py`, and (for the bar charts)
shows a small table of recomputed versus article values. PNGs land in `output/`.

Interactive:

```nu
uv run jupyter lab                          # then open any of the 0*.ipynb notebooks
uv run jupyter lab 02_gas_price_real.ipynb  # or open one directly
```

Then run all cells (Run menu, "Run All Cells").

Headless, from the shell:

```nu
uv run jupyter nbconvert --to notebook --execute --inplace 01_gas_price_nominal.ipynb

# all four at once
ls 0*.ipynb | each { |nb| uv run jupyter nbconvert --to notebook --execute --inplace $nb.name }
ls output
```

To extend the Trump 2.0 window past August 2026, set `EXTEND_TO_LATEST = True` in the "Administration windows"
cell of notebooks 1 and 2. Notebook 1 then runs through the newest EIA month; notebook 2 through the newest month
with both a gasoline price and a CPI value.

To change the look of every chart at once, edit the tokens and `rcParams` at the top of `charts.py`.

## Using the data in your own code

```python
import fetch_data

fetch_data.refresh()                       # same as the CLI default
gas = fetch_data.load("eia_gasoline")      # polars DataFrame: date, price_usd_per_gal
weekly = fetch_data.load("eia_gasoline_weekly")  # date (Monday), price_usd_per_gal
cpi = fetch_data.load("bls_cpi")           # date, cpi_u_nsa, note
lfpr = fetch_data.load("lfpr_25_54")       # date, lfpr_25_54_pct, note
income = fetch_data.load("census_h06")     # year, footnote, households_thousands, median/mean in current and real dollars
```

Or read the CSVs in `data/` directly from nushell:

```nu
open data/eia_gasoline_monthly.csv | last 12
open data/census_h06_household_income.csv | where year >= 2016 | select year median_real_usd
```

## How the article's numbers were reproduced

- **Charts 1 and 2.** Monthly EIA prices averaged over January 2009 to December 2016 (Obama), January 2017 to
  December 2020 (Trump 1.0), January 2021 to December 2024 (Biden) and January 2025 to August 2026 (Trump 2.0).
  For chart 2 each month's price is multiplied by CPI(August 2026) / CPI(month) before averaging, with the
  unpublished October 2025 CPI linearly interpolated. Both charts reproduce the article's figures exactly.
- **Chart 3.** The full `LNS11300060` series from January 1980.
- **Chart 4.** From Table H-6 real median household income in 2025 dollars:
  (value in the last year of the window minus value in the year before the window) divided by the number of
  years. Reproduces the article's $621, $1,590, $405 and $2,250 exactly.

The visual treatment is deliberately not a pixel copy of the Excel originals: a single blue series color,
thin bars with rounded ends, hairline gridlines and ink-colored labels. Titles, windows, units and sources are
preserved on every chart.

## Adding dependencies

```nu
uv add <package>        # updates pyproject.toml and uv.lock, installs into .venv
```
