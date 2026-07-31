# FCT Relic — Inventory Dashboard (Streamlit)

Interactive Streamlit recreation of the `FCT_Relic_Dashboard` HTML template.

## Data model

**Valuation is the authoritative monthly snapshot.**
`ATH_SPEND_RELIC_ITEM_INV_VAL_V` carries one official `ITEM_INV_VALU` per item per
`PERIOD_NAME` (e.g. `May-26`), at item x period grain only. To keep the
per-subinventory (status) and per-program breakdowns, each item's `ITEM_INV_VALU`
is **allocated across its on-hand bins in proportion to net quantity**, so bin
values reconcile to the authoritative per-item value and the portfolio total
reconciles to the period's `TOTAL_INV_VALU`.

On-hand **quantity** per bin is **computed cumulatively**: `SUM(QTY)` over
procurement (`ATH_SPEND_REC_RELIC_RAW_V`, QTY>0) + consumption
(`ATH_SPEND_CON_RELIC_RAW_V`, QTY<0) where `TXN_DATE <= as_of`, **including**
internal transfer rows so the subinventory-bin balances reconcile. `TXN_DATE` is
first-of-month stamped and maps 1:1 to `PERIOD_NAME`. Source columns are TEXT and
are cast in SQL. The period list is sourced from the snapshot view. See
[`queries.py`](queries.py).

**`FCT_INVENTORY_AGING` defines the item universe.** Computed cumulative quantity
can disagree with Oracle about what is really on hand, so `queries.ITEM_AGE_CTE` is
**INNER JOINed** by both the as-of snapshot and the multi-month history: an item that
view doesn't carry is excluded from *every* tab. The gate is per item and
all-or-nothing, so the valuation allocation stays exact for retained items and the
portfolio total still reconciles to the period's `TOTAL_INV_VALU`. Membership is
tested without a date condition, so the snapshot and the history span the same
universe in every month (`data.history_matches_snapshot` relies on this). Note that
the view is a **live** snapshot with no as-of dimension: historical months therefore
exclude items consumed since, and TRENDS restates downward for older months as
inventory is drawn down.

**Aging** is the earliest true `RECEIPT_DATE` from that view (`STREET_DATE` is
deliberately unused). Every age is a **lower bound** — the window opens 2025-05-30,
so an item received earlier reports the window-open date, and `over 2yr` stays empty
until 2027-05-30. A receipt falling later in the as-of month than the first-of-month
`TXN_DATE` stamp clamps to age 0 (`0-90`) rather than being treated as ageless.

### Classification (confirmed against the data)
- **WHOLE** = `ITEM_NUMBER` starts with `MEM`
- **CUT SIG** = `RELIC_FORM_TYPE = 'CUT SIGNATURE'`
- **NON-WHOLE** = everything else
- **Status** from `SUBINVENTORY_CODE`: `REL_SLATE`→Slated, `RELIC_OBSO`→Obsolete, else Unslated
- **Program** (Slated only) = 3rd dot-segment of `LOCATOR_NAME` (`000`/blank → `NO PROGRAM`)

## Run locally

The connection is read from `~/.snowflake/connections.toml`. This project has its
own venv at `vault_inv_dash/.venv` (Python 3.12).

```powershell
# from c:\Users\dji\Exploration\vault_inv_test
& vault_inv_dash\.venv\Scripts\python.exe -m streamlit run vault_inv_dash\streamlit_app.py
```

The app defaults to the `FANATICS_COLLECTIBLES_PROD` connection and the
`ANALYTICS_WAREHOUSE` warehouse, so no env vars are needed. Override either:

```powershell
$env:SNOWFLAKE_DEFAULT_CONNECTION_NAME = "FANATICS_COLLECTIBLES_DEV"
$env:SNOWFLAKE_WAREHOUSE = "MY_WH"
```

First load opens a browser for OAuth sign-in. If you hit the Windows OAuth
`CredWrite` error, keep `client_store_temporary_credential = false` in
`connections.toml`.

### Recreating the venv
```powershell
python -m venv vault_inv_dash\.venv
& vault_inv_dash\.venv\Scripts\python.exe -m pip install -e vault_inv_dash
```

## Deploy (Streamlit in Snowflake, container runtime)

The app deploys as `ORACLE_DATA_PROD.SANDBOX.VAULT_INV_DASH` on the SiS
**container runtime**, which installs `pyproject.toml` deps from PyPI (so it
runs Streamlit >= 1.57, not the ~1.52 warehouse-runtime ceiling). One-time
prerequisite: a PyPI external access integration granted to the deploying role
— see the header comments in [`snowflake.yml`](snowflake.yml).

```powershell
# from vault_inv_dash (needs snow >= 3.14)
snow streamlit deploy --replace --open --role POWER_ANALYST_ORACLE_PROD
```

## Files
- `streamlit_app.py` — layout, view/tab/status routing, drill-downs
- `queries.py` — authoritative-allocated on-hand snapshot SQL + month list
- `data.py` — `st.connection` + cached loaders
- `transforms.py` — pandas rollups / drill-downs / stat cards / movers
- `style.py` — scoped CSS + HTML chrome and pivot tables
- `charts.py` — Altair Trends charts
- `interactive.py` — clickable tables via `st.components.v2`
- `snowflake.yml` — Snowflake CLI deploy definition (container runtime)
- `pyproject.toml` — dependencies for the container runtime and the local venv
- `.streamlit/config.toml` — navy/gold theme
