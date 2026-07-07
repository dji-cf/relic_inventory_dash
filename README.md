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

## Files
- `streamlit_app.py` — layout, view/tab/status routing, drill-downs
- `queries.py` — authoritative-allocated on-hand snapshot SQL + month list
- `data.py` — `st.connection` + cached loaders
- `transforms.py` — pandas rollups / drill-downs / stat cards / movers
- `style.py` — scoped CSS + HTML chrome and pivot tables
- `charts.py` — Altair Trends charts
- `.streamlit/config.toml` — navy/gold theme
