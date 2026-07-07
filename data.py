"""Snowflake connection + cached data loaders for the Relic inventory dashboard.

Local runs read the connection from ~/.snowflake/connections.toml. The connection
is selected by SNOWFLAKE_DEFAULT_CONNECTION_NAME; if unset it defaults to
FANATICS_COLLECTIBLES_PROD so `streamlit run` works with no extra env setup.
Override either value at launch, e.g.:

    SNOWFLAKE_DEFAULT_CONNECTION_NAME=FANATICS_COLLECTIBLES_DEV \\
        streamlit run vault_inv_dash/streamlit_app.py

When deployed to Snowpark Container Services (SPCS), there is no connections.toml
and no browser for OAuth. Snowflake instead injects an OAuth login token at
/snowflake/session/token plus SNOWFLAKE_ACCOUNT / SNOWFLAKE_HOST env vars; we
detect those and connect as the service's owner role. See deploy/README.md.
"""
from __future__ import annotations

import os
from datetime import timedelta

import pandas as pd
import streamlit as st

import queries

# Default to the PROD connection when no env override is provided. Exported back
# into the environment so snowflake.connector's no-arg connect() picks it up
# locally. (In Streamlit-in-Snowflake this is ignored — the in-DB session wins.)
_CONN_NAME = os.getenv("SNOWFLAKE_DEFAULT_CONNECTION_NAME") or "FANATICS_COLLECTIBLES_PROD"
os.environ["SNOWFLAKE_DEFAULT_CONNECTION_NAME"] = _CONN_NAME
_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE") or "ANALYTICS_WAREHOUSE"

# Snowpark Container Services mounts a short-lived OAuth login token here and sets
# SNOWFLAKE_ACCOUNT / SNOWFLAKE_HOST in the environment. Its presence is how we
# distinguish an in-container run from a local `streamlit run`.
_SPCS_TOKEN_PATH = "/snowflake/session/token"


def _spcs_connection_kwargs() -> dict | None:
    """Connector kwargs for a Snowpark Container Services run, or None if local.

    Returns None when the SPCS login token is absent so callers fall back to the
    connections.toml / in-DB session behaviour.
    """
    try:
        with open(_SPCS_TOKEN_PATH) as f:
            token = f.read()
    except OSError:
        return None
    return {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "host": os.environ["SNOWFLAKE_HOST"],
        "authenticator": "oauth",
        "token": token,
        "warehouse": _WAREHOUSE,
    }


@st.cache_resource(show_spinner=False)
def _connection():
    """One pooled Snowflake connection for the app session.

    Locally, naming the connection "snowflake" with no extra kwargs makes
    Streamlit call the connector with no args, which loads the connection
    selected by SNOWFLAKE_DEFAULT_CONNECTION_NAME from ~/.snowflake/connections.toml
    and resolves to the in-DB session when running in Streamlit-in-Snowflake.
    In Snowpark Container Services we instead pass the injected OAuth token.
    """
    spcs = _spcs_connection_kwargs()
    if spcs is not None:
        conn = st.connection("snowflake", type="snowflake", **spcs)
    else:
        conn = st.connection("snowflake", type="snowflake")
    if _WAREHOUSE:
        try:
            conn.query(f"USE WAREHOUSE {_WAREHOUSE}", ttl=0)
        except Exception:
            pass
    return conn


@st.cache_data(ttl=timedelta(hours=6), show_spinner=False)
def available_months() -> list[str]:
    """Distinct as-of month stamps (ascending) for the date selector."""
    df = _connection().query(queries.MONTHS_SQL, ttl=timedelta(hours=6))
    col = df.columns[0]
    return [pd.Timestamp(v).strftime("%Y-%m-%d") for v in df[col].tolist()]


@st.cache_data(ttl=timedelta(hours=6), show_spinner="Loading on-hand snapshot…")
def load_onhand(as_of: str) -> pd.DataFrame:
    """Item + subinventory-bin grain on-hand snapshot as-of ``as_of`` (YYYY-MM-DD).

    Valuation is the authoritative ITEM_INV_VALU for the period, allocated across
    on-hand bins by qty share; qty is computed cumulatively from the raw txn views.
    Returns a tidy DataFrame; all downstream pivots/drill-downs run in pandas.
    """
    df = _connection().query(
        queries.onhand_sql(as_of),  # date embedded in SQL text (see queries.onhand_sql)
        ttl=timedelta(hours=6),
    )
    df.columns = [c.lower() for c in df.columns]
    df["qty_onhand"] = pd.to_numeric(df["qty_onhand"], errors="coerce").fillna(0.0)
    df["valuation"] = pd.to_numeric(df["valuation"], errors="coerce").fillna(0.0)
    # Composite subject key (a subject can appear under multiple brands).
    df["subj_key"] = df["brand"] + " ||| " + df["subject_name"]
    return df
