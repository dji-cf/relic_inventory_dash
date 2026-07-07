# Deploying to Streamlit-in-Snowflake (SiS)

Runs the dashboard **inside Snowflake** with no Docker, no image repository, and
no compute pool — it executes on a warehouse and is reached through Snowsight.
This is the lighter-weight alternative to the SPCS build (see [`README.md`](README.md));
pick SiS unless you specifically need a custom image, non-Anaconda packages, or a
standalone persistent endpoint.

## Why it works with zero code changes
- **Auth.** `data.py::_spcs_connection_kwargs()` looks for the SPCS login token at
  `/snowflake/session/token`. That file doesn't exist in SiS, so it returns `None`
  and the app falls through to `st.connection("snowflake", type="snowflake")`,
  which resolves to the **in-DB active session**. Nothing to change.
- **Packages.** pandas, altair, and openpyxl are all in the Snowflake Anaconda
  channel; `snowflake-snowpark-python` ships with the runtime. Declared in
  [`../environment.yml`](../environment.yml) (SiS's replacement for `pyproject.toml`
  + `Dockerfile`; `snowflake-connector-python` is not used in SiS).
- **Streamlit APIs.** The app uses `st.segmented_control`, `st.html`, and
  `width="stretch"`. SiS supports Streamlit up to **1.51.0** (Nov 2025 GA), which
  covers all of them — pinned in `environment.yml`.

## Files
- **`../environment.yml`** — Anaconda-channel dependency spec (required by SiS).
- **`../snowflake.yml`** — Snowflake CLI project definition for `snow streamlit deploy`.
- **`setup-sis.sql`** — one-time role/grants + the manual/Git `CREATE STREAMLIT` variants.

## Prerequisites
- A role that can read the relic views and use the warehouse — run **step 0** of
  [`setup-sis.sql`](setup-sis.sql) once.
- For the CLI path: [Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/index)
  (`snow`, v3+) with a configured connection.

## Deploy — pick one

### A. Snowflake CLI (recommended, reproducible)
Run grants (steps 0–1 of `setup-sis.sql`), then from the **project root** (the
folder with `streamlit_app.py` and `snowflake.yml`):
```bash
snow streamlit deploy --replace --open
```
Uploads the artifacts listed in `snowflake.yml` to `@streamlit_stage`, (re)creates
the `VAULT_INV_DASH` STREAMLIT object, and opens its URL. Re-run it after any code
change — that's the whole redeploy loop.

### B. Snowsight upload (no CLI)
Projects » **Streamlit** » **+ Streamlit App** → choose database `VAULT_APPS`,
schema `STREAMLIT`, and warehouse `ANALYTICS_WAREHOUSE`. In the editor, add each
module (`charts.py`, `data.py`, `queries.py`, `style.py`, `transforms.py`) and an
`environment.yml` matching [`../environment.yml`](../environment.yml). Paste
`streamlit_app.py` as the main file and **Run**.

### C. Git integration (deploy from the repo)
Create an API integration + git repository object, then run the `CREATE STREAMLIT
… ROOT_LOCATION='@…/branches/main/vault_inv_dash'` variant in **step 3b** of
`setup-sis.sql`. Best when you want the app to track a branch.

## Sharing
The app runs with **owner's rights** — it reads Snowflake as `VAULT_APP_ROLE`, so
viewers need only `USAGE` on the STREAMLIT object, not on the relic views:
```sql
GRANT USAGE ON STREAMLIT VAULT_APPS.STREAMLIT.VAULT_INV_DASH TO ROLE <viewer_role>;
```

## Cost
No compute pool to keep warm. The app consumes credits on `ANALYTICS_WAREHOUSE`
only while it's actively running queries; the warehouse auto-suspends on its own
idle timeout. Keep `query_warehouse` in `snowflake.yml` pointed at a small
warehouse.

## Notes / gotchas
- **Theme fidelity.** SiS honors only a subset of `.streamlit/config.toml` (theme
  keys) and ignores `[server]`. Most of the look comes from the inline CSS in
  `style.py` (`st.html`), which renders normally — but expect minor drift from a
  local `streamlit run`. Remove `config.toml` from `snowflake.yml` artifacts if it
  ever causes a deploy error.
- **Warehouse must match.** `data.py` issues `USE WAREHOUSE ANALYTICS_WAREHOUSE`
  (guarded by try/except, so a mismatch won't crash — it just silently keeps the
  session's warehouse). There's no `SNOWFLAKE_WAREHOUSE` env var to set in SiS, so
  keep `query_warehouse` in `snowflake.yml`, the grant in `setup-sis.sql`, and that
  hard-coded name all aligned.
- **Pin the Streamlit version.** Leaving `streamlit=1.51.0` pinned prevents a
  surprise upgrade from changing widget behavior mid-flight.
- **No external egress needed.** The app makes no outbound HTTP calls, so no
  external access integration is required. (The Google-Fonts URL in `config.toml`
  is fetched by the browser, not the app.)
