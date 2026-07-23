"""Sidebar Cortex Agent assistant for the Relic inventory dashboard.

Adds an "Ask the data" panel to the sidebar: a picker over the Cortex Agents in
ORACLE_DATA_PROD.SANDBOX (the schema the app deploys into) plus a multi-turn
chat that calls the Cortex Agent Run REST API.

The REST call has three dispatch paths, mirroring data.py's runtime detection:
  1. Streamlit-in-Snowflake — the built-in ``_snowflake`` module issues the
     request over Snowflake's internal channel (no External Access Integration
     required; it is not outbound egress).
  2. Standalone SPCS service — no ``_snowflake`` module, but the injected OAuth
     login token at ``data._SPCS_TOKEN_PATH`` + SNOWFLAKE_HOST let us POST with
     a Bearer token.
  3. Local ``streamlit run`` — pull host + session token off the cached
     connector connection and POST with the ``Snowflake Token`` scheme.

We request ``stream: false`` so the API returns a single aggregated JSON
``response`` object (a ``content[]`` array) instead of Server-Sent Events, which
keeps parsing simple. Multi-turn context comes for free: with ``thread_id``
omitted, ``messages`` carries the full conversation history each turn.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import timedelta

import pandas as pd
import streamlit as st

import data

# Where to look for agents / which agent path to call. Defaults match the
# deploy target in snowflake.yml; override locally with env vars if needed.
_AGENT_DB = os.getenv("CORTEX_AGENT_DB") or "ORACLE_DATA_PROD"
_AGENT_SCHEMA = os.getenv("CORTEX_AGENT_SCHEMA") or "SANDBOX"
_AGENT_TIMEOUT_S = 120

# Plausible high-level stages cycled in the "thinking" buffer while the (blocking)
# agent call runs. These are indicative, not the agent's real-time events — the
# non-streaming API returns one aggregated response, so we can't show true steps.
_THINKING_STAGES = [
    "Reading your question…",
    "Consulting the semantic model…",
    "Drafting SQL…",
    "Querying Snowflake…",
    "Summarizing the results…",
]
_STAGE_INTERVAL_S = 1.3


# ── agent listing ─────────────────────────────────────────────────────────────
def _run_show(sql: str) -> list[dict]:
    """Run a SHOW/metadata query and return rows as lowercased-key dicts.

    ``st.connection().query()`` routes through ``fetch_pandas_all()``, which
    raises on SHOW commands (results aren't Arrow-formatted). We use the Snowpark
    session instead — portable across local ``streamlit run`` and SiS — and fall
    back to a raw connector cursor if the session isn't available.
    """
    conn = data._connection()
    sess = getattr(conn, "session", None)
    if callable(sess):
        sess = sess()
    if sess is not None:
        rows = sess.sql(sql).collect()
        return [{k.lower(): v for k, v in r.as_dict().items()} for r in rows]
    cur = conn.raw_connection.cursor()
    try:
        cur.execute(sql)
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        cur.close()


@st.cache_data(ttl=timedelta(minutes=30), show_spinner=False)
def list_agents() -> list[tuple[str, str]]:
    """(display_label, agent_name) for every agent in the app schema.

    Reads ``profile.display_name`` when present, else falls back to the object
    name. Errors are NOT caught here: ``st.cache_data`` does not memoize
    exceptions, so letting them propagate keeps a transient failure (e.g. an
    expired local OAuth session token) from poisoning the cache with an empty
    list for the full TTL. The caller surfaces the error and offers a retry. A
    genuinely empty schema still returns [] and caches harmlessly.
    """
    rows = _run_show(f"SHOW AGENTS IN SCHEMA {_AGENT_DB}.{_AGENT_SCHEMA}")

    out: list[tuple[str, str]] = []
    for row in rows:
        name = row.get("name")
        if not name:
            continue
        label = name
        profile = row.get("profile")
        if profile:
            try:
                disp = json.loads(profile).get("display_name")
                if disp:
                    label = disp
            except (ValueError, TypeError):
                pass
        out.append((label, name))
    out.sort(key=lambda t: t[0].lower())
    return out


# ── REST client ─────────────────────────────────────────────────────────────
def _agent_path(agent_name: str) -> str:
    return (f"/api/v2/databases/{_AGENT_DB}/schemas/{_AGENT_SCHEMA}"
            f"/agents/{agent_name}:run")


def run_agent(agent_name: str, messages: list[dict]) -> dict:
    """POST the conversation to the Agent Run API; return the parsed response.

    ``messages`` is the full history (user + assistant turns) since we don't use
    server-side threads. Returns the aggregated ``response`` dict on success, or
    ``{"__error__": "<message>"}`` on any failure so callers can render it.
    """
    # Send ONLY the API-recognized fields. Messages in the caller's history
    # carry an extra "_reply" (with a pandas DataFrame for local rendering);
    # including it would break JSON serialization on multi-turn follow-ups.
    api_messages = [{"role": m["role"], "content": m.get("content", [])} for m in messages]
    body = {"messages": api_messages, "stream": False}
    path = _agent_path(agent_name)

    # 1. Streamlit-in-Snowflake: internal request via the _snowflake module.
    try:
        import _snowflake  # only present inside SiS
    except ImportError:
        _snowflake = None

    if _snowflake is not None:
        try:
            resp = _snowflake.send_snow_api_request(
                "POST", path, {}, {}, body, None, _AGENT_TIMEOUT_S * 1000,
            )
            status = resp.get("status")
            content = resp.get("content")
            if status is not None and not (200 <= int(status) < 300):
                return {"__error__": f"Agent API returned status {status}: {content}"}
            parsed = json.loads(content) if isinstance(content, str) else content
            return parsed if isinstance(parsed, dict) else {"__error__": str(parsed)}
        except Exception as exc:  # noqa: BLE001 — surface to the sidebar
            return {"__error__": f"Agent request failed: {exc}"}

    # 2/3. Not in SiS — use requests against the account host.
    import requests  # ships transitively with snowflake-connector-python

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    spcs = data._spcs_connection_kwargs()
    if spcs is not None:
        # Standalone SPCS service: OAuth login token + injected host.
        host = spcs["host"]
        headers["Authorization"] = f"Bearer {spcs['token']}"
        headers["X-Snowflake-Authorization-Token-Type"] = "OAUTH"
    else:
        # Local streamlit run: session token off the raw connector.
        try:
            raw = data._connection().raw_connection
            host = raw.host
            headers["Authorization"] = f'Snowflake Token="{raw.rest.token}"'
        except Exception as exc:  # noqa: BLE001
            return {"__error__": f"Could not resolve Snowflake session token: {exc}"}

    url = f"https://{host}{path}"
    try:
        r = requests.post(url, json=body, headers=headers, timeout=_AGENT_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        return {"__error__": f"Agent request failed: {exc}"}
    if r.status_code >= 300:
        return {"__error__": f"Agent API returned {r.status_code}: {r.text[:500]}"}
    try:
        parsed = r.json()
    except ValueError:
        return {"__error__": f"Non-JSON response from agent API: {r.text[:500]}"}
    # A 200 with error code 390112 means an expired local session token.
    if isinstance(parsed, dict) and str(parsed.get("code")) == "390112":
        return {"__error__": "Session token expired — rerun the app to reconnect."}
    return parsed


def _run_agent_with_status(agent_name: str, messages: list[dict]) -> dict:
    """Call run_agent while animating a compact status label.

    The API is non-streaming (one blocking call), so to animate we run the call
    on a worker thread and cycle stage labels from the main thread. run_agent
    reaches data._connection() (an ``st.cache_resource`` wrapping st.connection),
    which needs a Streamlit ScriptRunContext — so we hand the current context to
    the worker via add_script_run_ctx. If no context is available, fall back to a
    plain spinner.
    """
    try:
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
        ctx = get_script_run_ctx()
    except Exception:  # noqa: BLE001 — any import/context issue → simple fallback
        ctx = None

    if ctx is None:
        with st.spinner(_THINKING_STAGES[0]):
            return run_agent(agent_name, messages)

    box: dict = {}

    def _work() -> None:
        box["resp"] = run_agent(agent_name, messages)

    worker = threading.Thread(target=_work, daemon=True)
    add_script_run_ctx(worker, ctx)

    with st.status(_THINKING_STAGES[0], expanded=False) as status:
        worker.start()
        i = 0
        while worker.is_alive():
            worker.join(timeout=_STAGE_INTERVAL_S)
            if worker.is_alive():
                i = min(i + 1, len(_THINKING_STAGES) - 1)  # advance, clamp at last
                status.update(label=_THINKING_STAGES[i])
        status.update(label="Done", state="complete")
    return box.get("resp", {"__error__": "Agent thread returned no response."})


# ── response parsing ──────────────────────────────────────────────────────────
def _result_set_to_df(result_set: dict) -> pd.DataFrame | None:
    """Build a DataFrame from an Agent API ResultSet (rowType names + data)."""
    try:
        meta = result_set.get("resultSetMetaData", {})
        cols = [c["name"] for c in meta.get("rowType", [])]
        rows = result_set.get("data", [])
        if not cols:
            return None
        return pd.DataFrame(rows, columns=cols)
    except Exception:  # noqa: BLE001
        return None


def extract_reply(response: dict) -> dict:
    """Flatten the agent ``response`` into what the sidebar renders.

    Returns {text, sql, table (DataFrame|None), chart_spec (str|None),
    error (str|None)}. Unknown content types are ignored so new event types
    don't break rendering.

    ``text`` is only the agent's final answer: ``thinking`` blocks and the
    planning narration the agent emits between tool calls are dropped, so the
    user sees the answer rather than the model's reasoning.
    """
    if "__error__" in response:
        return {"text": "", "sql": None, "table": None,
                "chart_spec": None, "error": response["__error__"]}

    texts: list[str] = []
    sql: str | None = None
    table: pd.DataFrame | None = None
    chart_spec: str | None = None

    for item in response.get("content", []) or []:
        itype = item.get("type")

        if itype == "text":
            # Only the text emitted AFTER the last tool call is the final
            # answer. Text between/before tool calls is planning narration, and
            # the tool_use/tool_result branches below clear `texts` so it never
            # reaches the user.
            t = item.get("text", "")
            if t:
                texts.append(t)

        elif itype == "thinking":
            # Reasoning tokens — internal chain-of-thought, never surfaced.
            continue

        elif itype == "tool_use":
            texts.clear()  # drop any pre-tool narration
            tu = item.get("tool_use", item)
            inp = tu.get("input", {}) if isinstance(tu, dict) else {}
            if isinstance(inp, dict) and inp.get("sql"):
                sql = inp["sql"]

        elif itype == "tool_result":
            texts.clear()  # drop any narration gathered before the result
            tr = item.get("tool_result", item)
            for c in (tr.get("content", []) if isinstance(tr, dict) else []):
                if c.get("type") == "json":
                    j = c.get("json", {})
                    if isinstance(j, dict):
                        if j.get("sql") and not sql:
                            sql = j["sql"]
                        rs = j.get("result_set")
                        if rs and table is None:
                            table = _result_set_to_df(rs)

        elif itype == "table":
            tb = item.get("table", item)
            rs = tb.get("result_set") if isinstance(tb, dict) else None
            if rs and table is None:
                table = _result_set_to_df(rs)

        elif itype == "chart":
            ch = item.get("chart", item)
            spec = ch.get("chart_spec") if isinstance(ch, dict) else None
            if spec and not chart_spec:
                chart_spec = spec

    return {"text": "\n\n".join(texts).strip(), "sql": sql,
            "table": table, "chart_spec": chart_spec, "error": None}


# ── sidebar chat UI ───────────────────────────────────────────────────────────
def _render_assistant_reply(reply: dict) -> None:
    """Render one assistant turn: answer + a collapsed Details expander."""
    if reply.get("error"):
        st.error(reply["error"])
        return
    if reply.get("text"):
        st.markdown(reply["text"])
    has_details = reply.get("sql") or reply.get("table") is not None or reply.get("chart_spec")
    if has_details:
        with st.expander("Details", expanded=False):
            if reply.get("sql"):
                st.code(reply["sql"], language="sql")
            if reply.get("table") is not None:
                st.dataframe(reply["table"], hide_index=True, width="stretch")
            if reply.get("chart_spec"):
                try:
                    st.vega_lite_chart(json.loads(reply["chart_spec"]), use_container_width=True)
                except Exception:  # noqa: BLE001
                    pass


@st.fragment
def render_assistant() -> None:
    """Render the full agent assistant into the sidebar. Call once per run.

    Decorated as a fragment so submitting a question reruns ONLY this sidebar
    block — the dashboard stays put (no app-wide dim/refresh, and the expensive
    inventory data loads don't re-run on every question).
    """
    ss = st.session_state
    # Sidebar placement + width live in style.py (_SIDEBAR_CSS), scoped so
    # collapsing the sidebar lets the dashboard reclaim the space.
    # NB: the st.sidebar context is opened by the CALLER (a fragment can't call
    # st.sidebar itself), so elements here land in the sidebar.
    st.markdown("### Ask the data")

    try:
        agents = list_agents()
    except Exception as exc:  # noqa: BLE001 — surface, don't hide (see list_agents)
        st.error(f"Couldn't load agents from {_AGENT_DB}.{_AGENT_SCHEMA}: {exc}")
        st.caption("A local OAuth session token may have expired — reconnect, then retry.")
        if st.button("Retry", key="agent_retry"):
            list_agents.clear()
            st.rerun(scope="fragment")
        return
    if not agents:
        st.info(f"No Cortex Agents found in {_AGENT_DB}.{_AGENT_SCHEMA}.")
        if st.button("Refresh", key="agent_refresh"):
            list_agents.clear()
            st.rerun(scope="fragment")
        return

    labels = [a[0] for a in agents]
    by_label = {a[0]: a[1] for a in agents}
    ss.setdefault("agent_pick", labels[0])
    if ss["agent_pick"] not in by_label:  # agent list changed between runs
        ss["agent_pick"] = labels[0]

    prev_pick = ss["agent_pick"]
    pick = st.selectbox("Agent", labels, key="agent_pick")
    # Switching agents starts a fresh thread.
    if pick != prev_pick:
        ss["agent_msgs"] = []

    ss.setdefault("agent_msgs", [])

    cols = st.columns([3, 1])
    cols[0].caption(f"{_AGENT_DB}.{_AGENT_SCHEMA}")
    if cols[1].button("Clear", key="agent_clear", help="Clear this conversation"):
        ss["agent_msgs"] = []
        st.rerun(scope="fragment")

    # Replay conversation history.
    for msg in ss["agent_msgs"]:
        role = msg.get("role", "assistant")
        with st.chat_message(role):
            if role == "user":
                st.markdown(_message_text(msg))
            else:
                _render_assistant_reply(msg.get("_reply", {"text": _message_text(msg)}))

    prompt = st.chat_input("Ask about the inventory data…")
    if prompt:
        ss["agent_msgs"].append(
            {"role": "user", "content": [{"type": "text", "text": prompt}]}
        )
        response = _run_agent_with_status(by_label[pick], ss["agent_msgs"])
        reply = extract_reply(response)
        # Persist a compact assistant message (text back into the thread so
        # follow-ups keep context) plus the parsed reply for rich rendering.
        ss["agent_msgs"].append({
            "role": "assistant",
            "content": [{"type": "text", "text": reply.get("text", "")}],
            "_reply": reply,
        })
        st.rerun(scope="fragment")


def _message_text(msg: dict) -> str:
    """Pull the plain text out of a message's content array."""
    parts = [c.get("text", "") for c in msg.get("content", []) if c.get("type") == "text"]
    return "\n\n".join(p for p in parts if p)
