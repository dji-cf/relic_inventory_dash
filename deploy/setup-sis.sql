-- ============================================================================
-- Streamlit-in-Snowflake (SiS) setup for the FCT Relic inventory dashboard.
-- Warehouse runtime: no Docker, no image repo, no compute pool.
--
-- Run the grants (steps 0-1) in Snowsight ONCE. Then deploy the app one of three
-- ways (steps 2 / 3 / 3b) — see deploy/README-sis.md. Object names mirror the
-- SPCS build's VAULT_APPS database so everything lives together; rename freely.
-- ============================================================================

-- ── 0. Data-access grants ──────────────────────────────────────────────────
-- The app runs the same SQL as the SPCS build (queries.py) but AS THE ROLE THAT
-- OWNS the Streamlit object (owner's rights once shared). That role must be able
-- to read the relic source views and use the query warehouse, or every query 403s.
USE ROLE ACCOUNTADMIN;              -- or any role that can grant on these objects

-- Reuse the SPCS app role, or repoint these at whatever role will own the app.
CREATE ROLE IF NOT EXISTS VAULT_APP_ROLE;
GRANT ROLE VAULT_APP_ROLE TO USER CURRENT_USER();

GRANT USAGE  ON WAREHOUSE ANALYTICS_WAREHOUSE                        TO ROLE VAULT_APP_ROLE;
GRANT USAGE  ON DATABASE  ORACLE_DATA_PROD                          TO ROLE VAULT_APP_ROLE;
GRANT USAGE  ON SCHEMA    ORACLE_DATA_PROD.ATHLETE_SPEND            TO ROLE VAULT_APP_ROLE;
GRANT SELECT ON ALL VIEWS IN SCHEMA ORACLE_DATA_PROD.ATHLETE_SPEND TO ROLE VAULT_APP_ROLE;
-- queries.py reads exactly two views:
--   ATH_SPEND_REC_RELIC_RAW_V  (procurement)  and  ATH_SPEND_CON_RELIC_RAW_V (consumption)

-- ── 1. Home database/schema for the STREAMLIT object ────────────────────────
-- The STREAMLIT object and its file stage live here.
GRANT CREATE DATABASE ON ACCOUNT TO ROLE VAULT_APP_ROLE;
USE ROLE VAULT_APP_ROLE;
CREATE DATABASE IF NOT EXISTS VAULT_APPS;
CREATE SCHEMA   IF NOT EXISTS VAULT_APPS.STREAMLIT;
USE SCHEMA VAULT_APPS.STREAMLIT;

-- ── 2. Deploy with the Snowflake CLI (recommended) ──────────────────────────
-- Nothing to run here. From the project root (folder with snowflake.yml):
--   snow streamlit deploy --replace --open
-- It uploads the artifacts to @streamlit_stage, (re)creates the STREAMLIT object,
-- and opens its URL. Re-run after any code change.

-- ── 3. …or create the object manually from a stage (NO CLI needed) ──────────
CREATE STAGE IF NOT EXISTS VAULT_APPS.STREAMLIT.STREAMLIT_STAGE
  DIRECTORY = (ENABLE = TRUE);
-- Upload the files WITHOUT snow/snowsql via the Snowsight UI:
--   Data » Databases » VAULT_APPS » STREAMLIT » Stages » STREAMLIT_STAGE » + Files
--   Add: streamlit_app.py charts.py data.py queries.py style.py transforms.py environment.yml
--   (put config.toml under a `.streamlit/` path, or omit it)
-- If you DO have a CLI, the equivalent is:
--   PUT file://streamlit_app.py @VAULT_APPS.STREAMLIT.STREAMLIT_STAGE OVERWRITE=TRUE AUTO_COMPRESS=FALSE;  (repeat per file)
-- Then create the app object:
-- CREATE OR REPLACE STREAMLIT VAULT_APPS.STREAMLIT.VAULT_INV_DASH
--   ROOT_LOCATION   = '@VAULT_APPS.STREAMLIT.STREAMLIT_STAGE'
--   MAIN_FILE       = 'streamlit_app.py'
--   QUERY_WAREHOUSE = ANALYTICS_WAREHOUSE
--   TITLE           = 'FCT Relic — Inventory Dashboard';

-- ── 3b. …or deploy straight from Git (no local upload) ──────────────────────
-- Requires an API integration + git repository object pointing at your repo first.
-- CREATE OR REPLACE STREAMLIT VAULT_APPS.STREAMLIT.VAULT_INV_DASH
--   ROOT_LOCATION   = '@VAULT_APPS.STREAMLIT.<git_repo>/branches/main/vault_inv_dash'
--   MAIN_FILE       = 'streamlit_app.py'
--   QUERY_WAREHOUSE = ANALYTICS_WAREHOUSE
--   TITLE           = 'FCT Relic — Inventory Dashboard';

-- ── 4. Who can open the app ─────────────────────────────────────────────────
-- Viewers need USAGE on the STREAMLIT object. With owner's rights (default) they
-- do NOT need their own grants on the relic views — the app reads as VAULT_APP_ROLE.
-- GRANT USAGE ON STREAMLIT VAULT_APPS.STREAMLIT.VAULT_INV_DASH TO ROLE <viewer_role>;

-- ── Open it ─────────────────────────────────────────────────────────────────
-- SHOW STREAMLITS IN SCHEMA VAULT_APPS.STREAMLIT;   -- URL is in the output,
-- or find it under Projects » Streamlit in Snowsight.
