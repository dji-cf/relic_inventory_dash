"""FCT Relic — Inventory Dashboard (Streamlit recreation of the HTML template).

Valuation is the authoritative monthly ITEM_INV_VALU snapshot, allocated across
on-hand subinventory bins by qty share; on-hand qty is computed cumulatively from
the ORACLE_DATA_PROD.ATHLETE_SPEND relic txn views. See queries.py.
"""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import streamlit as st

import charts
import data
import interactive
import style
import transforms as tx

st.set_page_config(page_title="FCT Relic — Inventory Dashboard",
                   page_icon="◆", layout="wide")
style.inject_css()

# ── view config ───────────────────────────────────────────────────────────────
VIEWS = {
    "BY BRAND / SPORT": dict(col="brand", label="BRAND / SPORT", view="BY BRAND / SPORT"),
    "BY FORM TYPE":     dict(col="relic_form_type", label="FORM TYPE", view="BY FORM TYPE"),
    "BY USED STATUS":   dict(col="item_used_status", label="USED STATUS", view="BY USED STATUS"),
    "BY PROGRAM":       dict(col="program", label="PROGRAM", view="BY PROGRAM (SLATED)"),
}
STATUS_OPTS = {"ALL": "A", "UNSLATED": "U", "SLATED": "S", "OBSOLETE": "O"}


def month_label(d: str) -> str:
    return datetime.strptime(d, "%Y-%m-%d").strftime("%b %Y")


def to_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    try:
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="export")
    except Exception:
        return df.to_csv(index=False).encode("utf-8")
    return buf.getvalue()


# ── load months & pick as-of ──────────────────────────────────────────────────
months = data.available_months()
if not months:
    st.error("No inventory history found in the source views.")
    st.stop()
default_idx = months.index("2026-05-01") if "2026-05-01" in months else len(months) - 1

ss = st.session_state
ss.setdefault("as_of", months[default_idx])
ss.setdefault("status", "ALL")
ss.setdefault("view", "BY BRAND / SPORT")
ss.setdefault("tab", "INVENTORY")
ss.setdefault("drill_mode", "ITEMS")
for _k in ("status", "view", "tab", "drill_mode"):     # remember last good selection
    ss.setdefault(f"_{_k}_last", ss[_k])


def _sticky(key: str):
    """Keep exactly one segment selected: restore last value if deselected to None."""
    if ss[key] is None:
        ss[key] = ss[f"_{key}_last"]
    else:
        ss[f"_{key}_last"] = ss[key]


def _reset_drill(dim_too: bool = False):
    """Close the drill panels (mirrors the template's selectBrand/switchTableView)."""
    if dim_too:
        ss["drill_dim"] = "—"
        ss["_prev_drill_dim"] = "—"
    ss["drill_subj"] = "—"
    ss["_prev_drill_subj"] = "—"


def _on_view_change():
    """BY PROGRAM view forces Status=SLATED (mirrors the HTML), restores it on exit."""
    prev = ss["_view_last"]
    _sticky("view")
    if ss["view"] != prev:
        _reset_drill(dim_too=True)  # new dimension: stale drill selections can't apply
    if ss["view"] == "BY PROGRAM":
        ss.setdefault("_status_saved", ss["status"])
        ss["status"] = "SLATED"
    elif "_status_saved" in ss:
        ss["status"] = ss.pop("_status_saved")


# ── top controls ────────────────────────────────────────────────────────────
c1, c2, c3 = st.columns([1.1, 2.4, 1.2])
with c1:
    st.selectbox("As-of date", months, format_func=month_label, key="as_of")
with c2:
    st.segmented_control("View", list(VIEWS), key="view", on_change=_on_view_change)
with c3:
    st.segmented_control("Tab", ["INVENTORY", "TRENDS"], key="tab",
                         on_change=_sticky, args=("tab",))

# Program view is slated-only (mirrors the HTML); _on_view_change forces ss.status="SLATED".
program_view = ss.view == "BY PROGRAM"
st.segmented_control("Status", list(STATUS_OPTS), key="status",
                     disabled=program_view, on_change=_sticky, args=("status",))
status_code = STATUS_OPTS[ss.status]

# ── load snapshot ─────────────────────────────────────────────────────────────
raw = data.load_onhand(ss.as_of)

# ── global filters ────────────────────────────────────────────────────────────
with st.expander("Filters", expanded=False):
    f1, f2, f3, f4 = st.columns(4)
    teams = [""] + sorted(t for t in raw["team"].dropna().unique() if t)
    fts = [""] + sorted(f for f in raw["relic_form_type"].dropna().unique() if f)
    uss = [""] + sorted(u for u in raw["item_used_status"].dropna().unique() if u)
    brands = [""] + sorted(b for b in raw["brand"].dropna().unique() if b)
    for _k, _opts in (("g_team", teams), ("g_ft", fts), ("g_us", uss), ("g_brand", brands)):
        if ss.get(_k) not in _opts:  # drop a selection that no longer exists this month
            ss[_k] = ""
    g_team = f1.selectbox("Team", teams, format_func=lambda x: x or "All Teams", key="g_team")
    g_ft = f2.selectbox("Form Type", fts, format_func=lambda x: x or "All Form Types", key="g_ft")
    g_us = f3.selectbox("Used Status", uss, format_func=lambda x: x or "All Used Status", key="g_us")
    g_brand = f4.selectbox("Brand", brands, format_func=lambda x: x or "All Brands", key="g_brand")

flt = tx.apply_filters(raw, status=status_code, team=g_team,
                       formtype=g_ft, usedstatus=g_us, brand=g_brand)

cards = tx.stat_cards(flt)
cfg = VIEWS[ss.view]

# ── chrome ────────────────────────────────────────────────────────────────────
st.html(style.open_fct() + style.header_html(cards, month_label(ss.as_of))
        + style.stat_cards_html(cards) + style.close_fct())


# ═══════════════════════════════════════════════════════════════════════════════
def render_inventory():
    st.html(style.open_fct() + style.context_bar_html(status_code, cfg["view"])
            + style.legend_html() + style.close_fct())

    dim = cfg["col"]
    src = flt[flt["program"].notna()] if program_view else flt
    roll = tx.rollup(src, dim)
    if ss.view == "BY FORM TYPE":
        roll = tx.collapse_other(roll, dim)

    # search + export
    s1, s2 = st.columns([3, 1])
    q = s1.text_input("Filter " + cfg["label"].lower(), key="pivot_search",
                      placeholder=f"Filter {cfg['label'].lower()}…",
                      label_visibility="collapsed")
    if q:
        roll = roll[roll[dim].astype(str).str.contains(q, case=False, na=False)]
    s2.download_button("⬇ Export XLS", to_excel(roll),
                       file_name=f"relic_{dim}_{ss.as_of}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       width="stretch")

    # ── main pivot: click a row (or use the selectbox below) to drill ──
    options = ["—"] + [o for o in roll[dim].astype(str).tolist()
                       if not o.startswith("OTHER (")]
    current = ss.get("drill_dim", "—")
    if current != "—" and current not in options:
        options.append(current)  # search can hide the selected row; keep the selectbox valid

    clicked = interactive.table(
        style.pivot_table_html(roll, dim, cfg["label"], clickable=True,
                               selected=None if current == "—" else current),
        key="pivot_tbl",
    )
    if clicked is not None and (clicked == current or clicked in options):
        # re-clicking the selected row closes the panel (template toggle behavior)
        ss["drill_dim"] = "—" if clicked == current else clicked
        _reset_drill()
        st.rerun()

    sel = st.selectbox(f"Drill into {cfg['label'].lower()}", options, key="drill_dim")
    scroll_subjects = sel != "—" and sel != ss.get("_prev_drill_dim")
    if sel != ss.get("_prev_drill_dim"):
        ss["_prev_drill_dim"] = sel
        _reset_drill()  # dimension changed (click or selectbox): close the child drill
    if sel == "—":
        return

    sub_src = src[src[dim].astype(str) == sel]
    subs = tx.subject_rollup(sub_src)
    show_brand = ss.view != "BY BRAND / SPORT"

    if status_code == "S":
        st.segmented_control("Subject drill", ["ITEMS", "BY PROGRAM"], key="drill_mode",
                             on_change=_sticky, args=("drill_mode",))

    st.markdown(f"#### {sel} &nbsp; <span style='color:#6b7fa3;font-size:14px'>"
                f"{len(subs)} subjects</span>", unsafe_allow_html=True)
    sq = st.text_input("Filter subjects", key="subj_search",
                       placeholder="Filter subjects…", label_visibility="collapsed")
    subs_view = subs
    if sq:
        subs_view = subs[subs["subject_name"].str.contains(sq, case=False, na=False)
                         | subs["brand"].str.contains(sq, case=False, na=False)]

    # ── subjects: click a row (or use the selectbox below) to drill ──
    subj_opts = ["—"] + [f"{r.brand} — {r.subject_name}" for r in subs_view.itertuples()]
    subj_current = ss.get("drill_subj", "—")
    if subj_current != "—" and subj_current not in subj_opts:
        subj_opts.append(subj_current)

    subj_clicked = interactive.table(
        style.subject_table_html(subs_view, show_brand, clickable=True,
                                 selected=None if subj_current == "—" else subj_current),
        key="subj_tbl", scroll=scroll_subjects,
    )
    if subj_clicked is not None and (subj_clicked == subj_current or subj_clicked in subj_opts):
        ss["drill_subj"] = "—" if subj_clicked == subj_current else subj_clicked
        st.rerun()

    subj_sel = st.selectbox("Drill into subject", subj_opts, key="drill_subj")
    scroll_items = subj_sel != "—" and subj_sel != ss.get("_prev_drill_subj")
    ss["_prev_drill_subj"] = subj_sel
    if subj_sel == "—":
        return
    brand_v, subj_v = subj_sel.split(" — ", 1)

    if status_code == "S" and ss.drill_mode == "BY PROGRAM":
        prog = tx.program_breakdown(flt, brand_v, subj_v)
        st.markdown(f"#### {subj_v} — by program")
        interactive.table(style.pivot_table_html(prog, "program", "PROGRAM"),
                          key="prog_tbl", scroll=scroll_items)
    else:
        items = tx.items_for(flt, brand_v, subj_v)
        i1, i2 = st.columns([3, 1])
        i1.markdown(f"#### {subj_v} &nbsp; <span style='color:#6b7fa3;font-size:14px'>"
                    f"{len(items)} items</span>", unsafe_allow_html=True)
        iq = i1.text_input("Filter items", key="item_search",
                           placeholder="Filter by item #…", label_visibility="collapsed")
        if iq:
            items = items[items["item_number"].str.contains(iq, case=False, na=False)]
        i2.download_button("⬇ Export XLS", to_excel(items),
                           file_name=f"relic_items_{brand_v}_{subj_v}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           width="stretch")
        interactive.table(style.item_table_html(items), key="item_tbl", scroll=scroll_items)


def render_trends():
    st.html(style.open_fct() + '<div class="sec-title">SNAPSHOT COMPARISON</div>' + style.close_fct())
    t1, t2 = st.columns(2)
    a = t1.selectbox("Snapshot A", months, index=0, format_func=month_label, key="cmp_a")
    b = t2.selectbox("Snapshot B", months, index=len(months) - 1,
                     format_func=month_label, key="cmp_b")
    la, lb = month_label(a), month_label(b)

    da = tx.apply_filters(data.load_onhand(a), status=status_code,
                          team=g_team, formtype=g_ft, usedstatus=g_us, brand=g_brand)
    db = tx.apply_filters(data.load_onhand(b), status=status_code,
                          team=g_team, formtype=g_ft, usedstatus=g_us, brand=g_brand)
    ta, tb = tx.portfolio_totals(da), tx.portfolio_totals(db)

    m1, m2, m3 = st.columns(3)
    m1.metric(la, tx.fmt(ta["total"]))
    delta = tb["total"] - ta["total"]
    m2.metric(lb, tx.fmt(tb["total"]))
    pct = (delta / ta["total"] * 100) if ta["total"] else 0
    m3.metric("Change", tx.fmt(delta), f"{pct:+.1f}%")

    labels = [la, lb] if a != b else [la]
    st.html(style.open_fct() + '<div class="sec-title">TOTAL PORTFOLIO VALUE</div>' + style.close_fct())
    st.altair_chart(charts.total_value_chart(labels, [ta["total"], tb["total"]][:len(labels)]))

    st.html(style.open_fct() + '<div class="sec-title">VALUE MIX</div>' + style.close_fct())
    mix = []
    for lbl, tot in [(la, ta), (lb, tb)][:len(labels)]:
        for typ, key in [("WHOLE", "whole"), ("NON-WHOLE", "nonwhole"), ("CUT SIG", "cutsig")]:
            mix.append({"snapshot": lbl, "type": typ, "value": tot[key]})
    st.altair_chart(charts.value_mix_chart(labels, mix))

    st.html(style.open_fct() + '<div class="sec-title">BRAND COMPARISON (TOP 10)</div>' + style.close_fct())
    st.altair_chart(charts.brand_compare_chart(tx.brand_compare(da, db), labels))

    if a != b:
        gainers, losers = tx.subject_movers(da, db)
        st.html(style.open_fct() + f'<div class="sec-title">SUBJECT MOVERS ({la} → {lb})</div>' + style.close_fct())
        g1, g2 = st.columns(2)
        g1.markdown("**▲ Top gainers**")
        g1.dataframe(_movers_view(gainers), hide_index=True, width="stretch")
        g2.markdown("**▼ Top losers**")
        g2.dataframe(_movers_view(losers), hide_index=True, width="stretch")


def _movers_view(m):
    out = m.copy()
    out["Subject"] = out["brand"] + " — " + out["subject_name"]
    out["Change"] = out["delta"]
    return out[["Subject", "Change"]]


if ss.tab == "TRENDS":
    render_trends()
else:
    render_inventory()
