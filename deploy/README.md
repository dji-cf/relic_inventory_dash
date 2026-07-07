# Deploying to Snowpark Container Services (SPCS)

Runs the dashboard as a container **inside Snowflake**, on Snowflake-managed
compute, behind a Snowflake-authenticated URL. No OAuth browser flow, no stored
key-pair: SPCS injects a login token that the app reads automatically
(`data.py::_spcs_connection_kwargs`).

## Files
- **`../Dockerfile`** — the image (Streamlit + deps + app modules).
- **`../.dockerignore`** — keeps `.venv`, secrets, and git out of the image.
- **`spec.yaml`** — service specification (container image, port 8501, public endpoint).
- **`setup.sql`** — image repo, compute pool, grants, and `CREATE SERVICE`.

## Prerequisites
- Docker installed and running locally.
- [Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/index) (`snow`) configured with a connection, **or** just Docker + Snowsight.
- A Snowflake role that can create compute pools, image repositories, and services
  (see step 0 in `setup.sql`).

## Steps

### 1. Create the Snowflake infra
In Snowsight, run **steps 0–3** of [`setup.sql`](setup.sql). From the
`SHOW IMAGE REPOSITORIES` output, copy the **`repository_url`**, e.g.
`myorg-myacct.registry.snowflakecomputing.com/vault_apps/spcs/vault_repo`.

### 2. Build the image
From the **project root** (the folder with `streamlit_app.py`):
```bash
docker build --platform linux/amd64 \
  -t <repository_url>/vault-inv-dash:latest .
```
`--platform linux/amd64` is required — SPCS nodes are amd64, and an image built
on Apple silicon/ARM will fail to start otherwise.

### 3. Push to the Snowflake registry
```bash
# Log Docker in to the Snowflake registry (uses your snow CLI connection)
snow spcs image-registry login

docker push <repository_url>/vault-inv-dash:latest
```
No `snow` CLI? Get a token instead:
```bash
snow spcs image-registry token --format=JSON | \
  docker login <registry_host> -u 0sessiontoken --password-stdin
```
(`<registry_host>` is the part of `repository_url` before the first `/`.)

### 4. Create the service
Run **step 5** of [`setup.sql`](setup.sql). It pastes `spec.yaml` inline; if you'd
rather stage the spec file, use steps 4 + the alternate `CREATE SERVICE` instead.

### 5. Open it
Run **step 6**. Wait until `SYSTEM$GET_SERVICE_STATUS` reports **READY** (first
start pulls the image — allow a minute), then open the `ingress_url` from
`SHOW ENDPOINTS`. You'll sign in with your Snowflake login. Grant other viewers
with `GRANT USAGE ON SERVICE ... TO ROLE <viewer_role>`.

## Redeploying a code change
```bash
docker build --platform linux/amd64 -t <repository_url>/vault-inv-dash:latest .
docker push <repository_url>/vault-inv-dash:latest
```
Then re-run step 5 (`DROP SERVICE` + `CREATE SERVICE`) in Snowsight. Tags are not
auto-refreshed, so recreating the service is what pulls `:latest` again — or push
a new tag (`:v2`) and update `spec.yaml`.

## Cost
The **compute pool bills while active**, not the service. `AUTO_SUSPEND_SECS=3600`
suspends it after an hour idle (cold start on next hit). Suspend manually anytime:
`ALTER COMPUTE POOL VAULT_POOL SUSPEND;`.

## Notes / gotchas
- **Auth model.** The service runs with **owner's rights** — it reads Snowflake as
  the role that created it (`VAULT_APP_ROLE` in `setup.sql`). Grant that role
  `SELECT` on the relic views and `USAGE` on the warehouse, or every query 403s.
- **Token lifetime.** The injected login token is short-lived. For a dashboard
  refreshed within a working session this is a non-issue; if you later see auth
  errors on very long-lived tabs, that's the cause — reload re-reads the token.
- **Theme.** `.streamlit/config.toml` still applies. Its Google-Fonts URL is
  fetched by the *browser*, not the container, so no container egress is needed.
- **Streamlit flags.** The Dockerfile disables CORS/XSRF and runs headless because
  the SPCS ingress proxy terminates TLS and handles auth; leaving XSRF on can break
  the websocket behind the proxy.

## Not sure SPCS is worth it?
For an app whose only deps are pandas/altair/openpyxl (all in Snowflake's Anaconda
channel), plain **Streamlit-in-Snowflake** deploys with no Docker and no compute
pool to keep warm. Choose SPCS when you want full control of the image, packages
outside the Anaconda channel, or a persistent standalone service endpoint.

That SiS path is fully set up — see **[`README-sis.md`](README-sis.md)**
(`../environment.yml`, `../snowflake.yml`, `setup-sis.sql`). No app-code changes:
`data.py` resolves to the in-DB session automatically when the SPCS token is absent.
