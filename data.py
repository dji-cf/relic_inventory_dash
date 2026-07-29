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
detect those and connect as the service's owner role.
"""
from __future__ import annotations

import os
from datetime import timedelta

import pandas as pd
import streamlit as st

import queries
import transforms as tx  # shared age buckets; imports only numpy/pandas (no cycle)

_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE") or "ANALYTICS_WAREHOUSE"

# Snowpark Container Services — and the Streamlit-in-Snowflake CONTAINER runtime,
# which is SPCS under the hood — mounts a short-lived OAuth login token here and
# sets SNOWFLAKE_ACCOUNT / SNOWFLAKE_HOST. Its presence means we're running
# INSIDE Snowflake rather than on a laptop.
_SPCS_TOKEN_PATH = "/snowflake/session/token"


def _running_in_snowflake() -> bool:
    """True when hosted by Streamlit-in-Snowflake or a Snowpark Container service."""
    if os.path.exists(_SPCS_TOKEN_PATH):
        return True
    try:
        from snowflake.snowpark.context import get_active_session

        get_active_session()
        return True
    except Exception:
        return False


# Local `streamlit run` ONLY: select the connections.toml entry. When hosted in
# Streamlit-in-Snowflake the runtime provides a connection named 'default';
# overriding SNOWFLAKE_DEFAULT_CONNECTION_NAME there makes the connector search
# for a named connection that doesn't exist ("Default connection ... cannot be
# found, known ones are ['default']"). So set it only when NOT hosted.
if not _running_in_snowflake():
    _CONN_NAME = os.getenv("SNOWFLAKE_DEFAULT_CONNECTION_NAME") or "FANATICS_COLLECTIBLES_PROD"
    os.environ["SNOWFLAKE_DEFAULT_CONNECTION_NAME"] = _CONN_NAME


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
    Hosted in Streamlit-in-Snowflake (warehouse or container runtime), the
    runtime supplies the session via the built-in 'default' connection — call
    st.connection with no extra kwargs and no name override. Only a standalone
    SPCS *service* needs the injected OAuth token.
    """
    if _running_in_snowflake():
        try:
            conn = st.connection("snowflake", type="snowflake")
        except Exception:
            # Standalone SPCS service: no 'default' connection — use the token.
            spcs = _spcs_connection_kwargs()
            conn = (
                st.connection("snowflake", type="snowflake", **spcs)
                if spcs is not None
                else st.connection("snowflake", type="snowflake")
            )
    else:
        conn = st.connection("snowflake", type="snowflake")
    if _WAREHOUSE:
        try:
            conn.query(f"USE WAREHOUSE {_WAREHOUSE}", ttl=0)
        except Exception:
            pass
    return conn


@st.cache_data(ttl=timedelta(hours=2), show_spinner=False)
def available_months() -> list[str]:
    """Distinct as-of month stamps (ascending) for the date selector."""
    df = _connection().query(queries.MONTHS_SQL, ttl=timedelta(hours=2))
    col = df.columns[0]
    return [pd.Timestamp(v).strftime("%Y-%m-%d") for v in df[col].tolist()]


@st.cache_data(ttl=timedelta(hours=2), show_spinner="Loading on-hand snapshot…")
def load_onhand(as_of: str) -> pd.DataFrame:
    """Item + subinventory-bin grain on-hand snapshot as-of ``as_of`` (YYYY-MM-DD).

    Valuation is the authoritative ITEM_INV_VALU for the period, allocated across
    on-hand bins by qty share; qty is computed cumulatively from the raw txn views.
    Returns a tidy DataFrame; all downstream pivots/drill-downs run in pandas.
    """
    df = _connection().query(
        queries.onhand_sql(as_of),  # date embedded in SQL text (see queries.onhand_sql)
        ttl=timedelta(hours=2),
    )
    df.columns = [c.lower() for c in df.columns]
    df["qty_onhand"] = pd.to_numeric(df["qty_onhand"], errors="coerce").fillna(0.0)
    df["valuation"] = pd.to_numeric(df["valuation"], errors="coerce").fillna(0.0)
    # Composite subject key (a subject can appear under multiple brands).
    df["subj_key"] = df["brand"] + " ||| " + df["subject_name"]
    # Aging: age_days is nullable (NULL only for an item with no receipt in FCT
    # view); age_bucket folds any NULL into the oldest bucket so totals reconcile.
    df["age_days"] = pd.to_numeric(df["age_days"], errors="coerce").astype("Int64")
    df["age_date"] = pd.to_datetime(df["age_date"], errors="coerce")
    df["age_bucket"] = tx.age_bucket(df["age_days"])
    return df


@st.cache_data(ttl=timedelta(hours=2), show_spinner="Loading monthly history…")
def load_history() -> pd.DataFrame:
    """Cumulative on-hand balance for every valuation period (month x item x
    subinventory x program grain), for the TRENDS tab and INVENTORY sparklines.

    Loaded lazily -- callers must invoke this ONLY inside the trends view or
    behind the sparkline toggle, never on the INVENTORY first paint. Mirrors
    ``load_onhand``'s post-processing; ``month`` is a YYYY-MM-DD string so it
    lines up with ``available_months()`` / ``ss.as_of`` with no dtype friction.
    """
    df = _connection().query(queries.HISTORY_SQL, ttl=timedelta(hours=2))
    df.columns = [c.lower() for c in df.columns]
    df["qty_onhand"] = pd.to_numeric(df["qty_onhand"], errors="coerce").fillna(0.0)
    df["valuation"] = pd.to_numeric(df["valuation"], errors="coerce").fillna(0.0)
    df["subj_key"] = df["brand"] + " ||| " + df["subject_name"]
    df["month"] = pd.to_datetime(df["month"]).dt.strftime("%Y-%m-%d")
    for c in ("item_number", "brand", "subject_name", "team", "relic_form_type",
              "item_used_status", "subinventory_code", "status", "program", "itype"):
        df[c] = df[c].astype("category")
    return df


def history_matches_snapshot(hist, onhand, as_of, tol: float = 1.0) -> bool:
    """True if ``hist`` reconciles to ``onhand`` at ``as_of`` (valuation total).

    Guards against a stale cached history frame after ``load_onhand`` changes;
    on mismatch the app shows a caption + a "Refresh data" button that calls
    ``st.cache_data.clear()``. ``tol`` is dollars (allocation reconciles exactly).
    """
    h = hist.loc[hist["month"] == as_of, "valuation"].sum()
    o = onhand["valuation"].sum()
    return abs(float(h) - float(o)) <= tol
