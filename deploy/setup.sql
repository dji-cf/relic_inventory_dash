-- ============================================================================
-- Snowpark Container Services setup for the FCT Relic inventory dashboard.
-- Run these top-to-bottom in Snowsight. Steps 1–4 are one-time infrastructure;
-- step 5 (re)creates the service and is what you re-run to deploy a new image.
--
-- Object names (VAULT_APPS / SPCS / VAULT_REPO / VAULT_POOL / VAULT_INV_DASH_SVC)
-- are examples — rename freely, but keep them consistent with deploy/spec.yaml
-- and the docker build tag.
-- ============================================================================

-- ── 0. Role & account-level grants ─────────────────────────────────────────
-- The service authenticates to Snowflake AS ITS OWNER ROLE (owner's rights):
-- whatever role you CREATE SERVICE with is the identity the container's OAuth
-- token maps to. That role must be able to read the relic views and use the
-- warehouse. Using a dedicated role keeps the app's access auditable.
USE ROLE ACCOUNTADMIN;

CREATE ROLE IF NOT EXISTS VAULT_APP_ROLE;

-- Creating a PUBLIC endpoint requires this account-level privilege.
GRANT BIND SERVICE ENDPOINT ON ACCOUNT TO ROLE VAULT_APP_ROLE;

-- Let the role create & run the container infra.
GRANT CREATE COMPUTE POOL ON ACCOUNT TO ROLE VAULT_APP_ROLE;
GRANT ROLE VAULT_APP_ROLE TO ROLE SYSADMIN;   -- so SYSADMIN inherits it
GRANT ROLE VAULT_APP_ROLE TO USER CURRENT_USER();

-- Data access the app needs at runtime (adjust DB/schema/warehouse to your env).
GRANT USAGE ON WAREHOUSE ANALYTICS_WAREHOUSE TO ROLE VAULT_APP_ROLE;
-- Grant SELECT on the relic source views referenced in queries.py, e.g.:
--   GRANT USAGE ON DATABASE ORACLE_DATA_PROD TO ROLE VAULT_APP_ROLE;
--   GRANT USAGE ON SCHEMA ORACLE_DATA_PROD.ATHLETE_SPEND TO ROLE VAULT_APP_ROLE;
--   GRANT SELECT ON ALL VIEWS IN SCHEMA ORACLE_DATA_PROD.ATHLETE_SPEND TO ROLE VAULT_APP_ROLE;

USE ROLE VAULT_APP_ROLE;

-- ── 1. Home database / schema for the SPCS objects ─────────────────────────
CREATE DATABASE IF NOT EXISTS VAULT_APPS;
CREATE SCHEMA   IF NOT EXISTS VAULT_APPS.SPCS;
USE SCHEMA VAULT_APPS.SPCS;

-- ── 2. Image repository (Snowflake-hosted Docker registry) ─────────────────
CREATE IMAGE REPOSITORY IF NOT EXISTS VAULT_REPO;
-- Copy the `repository_url` from the output — you docker push to it (step in README).
SHOW IMAGE REPOSITORIES IN SCHEMA VAULT_APPS.SPCS;

-- ── 3. Compute pool that runs the container ────────────────────────────────
-- CPU_X64_XS is the smallest node and is plenty for one Streamlit dashboard.
-- AUTO_SUSPEND stops billing when idle; it cold-starts on the next request.
CREATE COMPUTE POOL IF NOT EXISTS VAULT_POOL
  MIN_NODES = 1
  MAX_NODES = 1
  INSTANCE_FAMILY = CPU_X64_XS
  AUTO_SUSPEND_SECS = 3600;

-- >>> Build & push the image now (see deploy/README.md steps 2–3), then continue. <<<

-- ── 4. (Optional) upload spec.yaml to a stage instead of pasting inline ────
-- CREATE STAGE IF NOT EXISTS VAULT_APPS.SPCS.SPECS;
-- PUT file://deploy/spec.yaml @VAULT_APPS.SPCS.SPECS OVERWRITE=TRUE AUTO_COMPRESS=FALSE;

-- ── 5. Create (or recreate) the service ────────────────────────────────────
-- Re-run this block to deploy a freshly pushed image. DROP first if it exists.
DROP SERVICE IF EXISTS VAULT_INV_DASH_SVC;

CREATE SERVICE VAULT_INV_DASH_SVC
  IN COMPUTE POOL VAULT_POOL
  FROM SPECIFICATION $$
spec:
  containers:
    - name: dashboard
      image: /VAULT_APPS/SPCS/VAULT_REPO/vault-inv-dash:latest
      env:
        SNOWFLAKE_WAREHOUSE: ANALYTICS_WAREHOUSE
      readinessProbe:
        port: 8501
        path: /_stcore/health
  endpoints:
    - name: app
      port: 8501
      public: true
  $$
  QUERY_WAREHOUSE = ANALYTICS_WAREHOUSE;

-- Alternatively, from a staged spec (see step 4):
-- CREATE SERVICE VAULT_INV_DASH_SVC
--   IN COMPUTE POOL VAULT_POOL
--   FROM @VAULT_APPS.SPCS.SPECS SPECIFICATION_FILE = 'spec.yaml'
--   QUERY_WAREHOUSE = ANALYTICS_WAREHOUSE;

-- ── 6. Watch it come up, then grab the URL ─────────────────────────────────
-- Wait until status is READY (first start also pulls the image — give it a minute).
SELECT SYSTEM$GET_SERVICE_STATUS('VAULT_INV_DASH_SVC');

-- Tail container logs if it isn't becoming READY:
-- SELECT SYSTEM$GET_SERVICE_LOGS('VAULT_INV_DASH_SVC', 0, 'dashboard', 200);

-- Open `ingress_url` in a browser (you'll be prompted for Snowflake login).
SHOW ENDPOINTS IN SERVICE VAULT_INV_DASH_SVC;

-- ── Who can open the app ───────────────────────────────────────────────────
-- Anyone who reaches the public endpoint must have USAGE on the service.
-- GRANT USAGE ON SERVICE VAULT_INV_DASH_SVC TO ROLE <viewer_role>;

-- ── Lifecycle ──────────────────────────────────────────────────────────────
-- ALTER COMPUTE POOL VAULT_POOL SUSPEND;   -- stop paying while unused
-- ALTER COMPUTE POOL VAULT_POOL RESUME;
-- DROP SERVICE VAULT_INV_DASH_SVC;
