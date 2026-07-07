# FCT Relic inventory dashboard — container image for Snowpark Container Services.
#
# Build from the project root (the dir holding streamlit_app.py) so the build
# context includes the flat app modules:
#     docker build --platform linux/amd64 -t <registry>/<db>/<schema>/<repo>/vault-inv-dash:latest .
# SPCS nodes are amd64 — always build with --platform linux/amd64 (matters on an
# Apple-silicon or ARM host).
FROM python:3.11-slim

WORKDIR /app

# Dependencies only, so this layer caches across app-code edits. Kept in sync
# with pyproject.toml [project].dependencies.
RUN pip install --no-cache-dir \
        "streamlit>=1.57" \
        "snowflake-snowpark-python" \
        "snowflake-connector-python" \
        "pandas>=2.0" \
        "altair>=5" \
        "openpyxl>=3.1"

# App modules (streamlit_app.py, queries.py, data.py, transforms.py, style.py,
# charts.py, interactive.py) and the .streamlit theme. .dockerignore keeps out
# .venv/secrets.
COPY . .

EXPOSE 8501

# Bind to 0.0.0.0 for the SPCS ingress proxy; run headless; the proxy terminates
# TLS and handles auth, so CORS/XSRF are disabled to avoid websocket breakage.
ENTRYPOINT ["streamlit", "run", "streamlit_app.py", \
            "--server.port=8501", \
            "--server.address=0.0.0.0", \
            "--server.headless=true", \
            "--server.enableCORS=false", \
            "--server.enableXsrfProtection=false", \
            "--browser.gatherUsageStats=false"]
