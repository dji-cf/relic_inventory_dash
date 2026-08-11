"""FCT Relic — Inventory Dashboard (Streamlit recreation of the HTML template).

Valuation is the authoritative monthly ITEM_INV_VALU snapshot, allocated across
on-hand subinventory bins by qty share; on-hand qty is computed cumulatively from
the ORACLE_DATA_PROD.ATHLETE_SPEND relic txn views. See queries.py.

Tabs: INVENTORY (drill-down pivots with slated/unslated + aging columns and
optional 15-month sparklines), TRENDS (multi-month build-vs-shrink views over
the full history), AGING (headline aging cards + configurable stale report).
"""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import streamlit as st

import agent
import charts
import data
import interactive
import style
import transforms as tx

st.set_page_config(page_title="FCT Relic — Inventory Dashboard",
                   page_icon="◆", layout="wide")
style.inject_css()
with st.sidebar:  # fragment can't open st.sidebar itself; wrap the call here
    agent.render_assistant()  # sidebar "Ask the data" Cortex Agent panel

# ── view config ───────────────────────────────────────────────────────────────
VIEWS = {
    "BY BRAND / SPORT": dict(col="brand", label="BRAND / SPORT", view="BY BRAND / SPORT"),
    "BY FORM TYPE":     dict(col="relic_form_type", label="FORM TYPE", view="BY FORM TYPE"),
    "BY USED STATUS":   dict(col="item_used_status", label="USED STATUS", view="BY USED STATUS"),
    "BY PROGRAM":       dict(col="program", label="PROGRAM", view="BY PROGRAM (SLATED)"),
    "BY AGE":           dict(col="age_bucket", label="AGE BUCKET", view="BY AGE"),
}
STATUS_OPTS = {"ALL": "A", "UNSLATED": "U", "SLATED": "S", "OBSOLETE": "O"}
COL_SETS = ["STANDARD", "+AGE", "STATUS + AGE"]
TREND_DIMS = {"BRAND": "brand", "SUBJECT": "subj_key",
              "FORM TYPE": "relic_form_type", "STATUS": "status"}
_STATUS_CODES = {v: k for k, v in tx.STATUS_LABELS.items()}


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
ss = st.session_state
ss.setdefault("as_of", months[-1])  # default to the most recent month
ss.setdefault("status", "ALL")
ss.setdefault("view", "BY BRAND / SPORT")
ss.setdefault("tab", "INVENTORY")
ss.setdefault("drill_mode", "ITEMS")
ss.setdefault("pivot_cols", "STANDARD")
ss.setdefault("trend_dim", "BRAND")
ss.setdefault("trend_metric", "VALUE")
ss.setdefault("stale_status", "UNSLATED")
for _k in ("status", "view", "tab", "drill_mode", "pivot_cols",
           "trend_dim", "trend_metric", "stale_status"):  # remember last good selection
    ss.setdefault(f"_{_k}_last", ss[_k])
for _k in ("pivot_rows", "subj_rows", "prog_rows", "item_rows",
           "movers_rows", "stale_rows"):  # per-table rows-per-view default
    ss.setdefault(_k, 25)
# Click-to-sort state, one entry per custom table: None (default ranking) or
# (sort_key, "asc"|"desc"). Set by header clicks; see _apply_sort.
for _k in ("sort_pivot", "sort_subj", "sort_prog", "sort_item"):
    ss.setdefault(_k, None)


# ── click-to-sort ─────────────────────────────────────────────────────────────
def _toggle_sort(state_key: str, clicked: str, frame, **resolve):
    """Fold a header click into the table's sort state, then rerun.

    Same column -> flip direction. New column -> start in the direction that
    reads naturally for its type: numbers largest-first, text A-Z (resolved from
    the real dtype, so a formatted $ column still sorts numerically).
    """
    cur = ss.get(state_key)
    if cur and cur[0] == clicked:
        ss[state_key] = (clicked, "asc" if cur[1] == "desc" else "desc")
    else:
        s = tx.sort_series(frame, clicked, **resolve)
        if s is None:
            return
        ss[state_key] = (clicked, "desc" if tx.default_descending(s) else "asc")
    st.rerun()


def _apply_sort(state_key: str, frame, *, pin_other_col=None, **resolve):
    """Sort ``frame`` by the table's state, dropping a stale/unresolvable key.

    A sorted column can vanish between runs — switching View changes the pivot
    dimension, the Columns control removes STATUS/AGE, and turning off 15-MO
    TREND removes the trend column. In those cases clear the state so the table
    falls back to its default ranking instead of silently ignoring the arrow.
    """
    spec = ss.get(state_key)
    if not spec:
        return frame
    if tx.sort_series(frame, spec[0], **resolve) is None:
        ss[state_key] = None
        return frame
    return tx.sort_frame(frame, spec, pin_other_col=pin_other_col, **resolve)


# ── rows-per-view (per-table height cap + scroll) ─────────────────────────────
ROWS_OPTS = [10, 25, 50, "All"]
_ROW_PX, _HEAD_PX = 42, 64          # approx custom-table row / header+footer px


def _rows_select(container, key):
    """A compact per-table 'rows per view' selectbox (10/25/50/All)."""
    return container.selectbox("Rows per view", ROWS_OPTS, key=key,
                               label_visibility="collapsed",
                               help="Rows shown before the table scrolls")


def _cap_px(rows):
    """Custom HTML tables: max-height px for the scroll container (None = uncapped)."""
    return None if rows == "All" else int(_HEAD_PX + rows * _ROW_PX)


def _df_height(rows, total):
    """Native st.dataframe: fixed height px sized to the selection (no empty space)."""
    n = total if rows == "All" else min(rows, total)
    return int(38 + max(1, n) * 35)


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
c1, c2, c3, c4 = st.columns([1.1, 2.6, 1.4, 0.4])
with c1:
    st.selectbox("As-of date", months, format_func=month_label, key="as_of")
with c2:
    st.segmented_control("View", list(VIEWS), key="view", on_change=_on_view_change)
with c3:
    st.segmented_control("Tab", ["INVENTORY", "TRENDS", "AGING"], key="tab",
                         on_change=_sticky, args=("tab",))
with c4:
    if st.button("↻ Refresh", help="Clear cached data and reload from Snowflake"):
        st.cache_data.clear()
        st.rerun()

# Program view is slated-only (mirrors the HTML); _on_view_change forces ss.status="SLATED".
# The AGING tab always spans all statuses, so the control is disabled there too.
program_view = ss.view == "BY PROGRAM"
aging_tab = ss.tab == "AGING"
st.segmented_control("Status", list(STATUS_OPTS), key="status",
                     disabled=program_view or aging_tab,
                     on_change=_sticky, args=("status",))
status_code = "A" if aging_tab else STATUS_OPTS[ss.status]


# ── load snapshot ─────────────────────────────────────────────────────────────
raw, pulled_at = data.load_onhand(ss.as_of)

# ── global filters ────────────────────────────────────────────────────────────
with st.expander("Filters", expanded=False):
    f1, f2, f3, f4, f5 = st.columns(5)
    teams = [""] + sorted(t for t in raw["team"].dropna().unique() if t)
    fts = [""] + sorted(f for f in raw["relic_form_type"].dropna().unique() if f)
    uss = [""] + sorted(u for u in raw["item_used_status"].dropna().unique() if u)
    brands = [""] + sorted(b for b in raw["brand"].dropna().unique() if b)
    subinvs = [""] + sorted(s for s in raw["subinventory_code"].dropna().unique() if s)
    for _k, _opts in (("g_team", teams), ("g_ft", fts), ("g_us", uss),
                      ("g_brand", brands), ("g_subinv", subinvs)):
        if ss.get(_k) not in _opts:  # drop a selection that no longer exists this month
            ss[_k] = ""
    g_team = f1.selectbox("Team", teams, format_func=lambda x: x or "All Teams", key="g_team")
    g_ft = f2.selectbox("Form Type", fts, format_func=lambda x: x or "All Form Types", key="g_ft")
    g_us = f3.selectbox("Used Status", uss, format_func=lambda x: x or "All Used Status", key="g_us")
    g_brand = f4.selectbox("Brand", brands, format_func=lambda x: x or "All Brands", key="g_brand")
    g_subinv = f5.selectbox("Sub-Inventory", subinvs,
                            format_func=lambda x: x or "All Sub-Inventories", key="g_subinv")

flt = tx.apply_filters(raw, status=status_code, team=g_team,
                       formtype=g_ft, usedstatus=g_us, brand=g_brand,
                       subinventory=g_subinv)

cards = tx.stat_cards(flt)
cfg = VIEWS[ss.view]

# ── chrome ────────────────────────────────────────────────────────────────────
st.html(style.open_fct() + style.header_html(cards, month_label(ss.as_of), pulled_at)
        + style.stat_cards_html(cards) + style.close_fct())


def _history_checked():
    """Load the cached multi-month history and verify it agrees with the as-of
    snapshot (both unfiltered); on mismatch offer a cache refresh."""
    hist = data.load_history()
    if not data.history_matches_snapshot(hist, raw, ss.as_of):
        st.caption("⚠ Cached history is out of sync with the on-hand snapshot.")
        if st.button("Refresh data", key="refresh_history"):
            st.cache_data.clear()
            st.rerun()
    return hist


# ═══════════════════════════════════════════════════════════════════════════════
def render_inventory():
    st.html(style.open_fct() + style.context_bar_html(status_code, cfg["view"])
            + style.legend_html() + style.close_fct())

    dim = cfg["col"]
    src = flt[flt["program"].notna()] if program_view else flt
    roll = tx.rollup(src, dim)
    folded_vals: list[str] = []
    if ss.view == "BY FORM TYPE":
        folded_vals = roll.loc[roll["tv"] < tx.FT_OTHER_THRESHOLD,
                               dim].astype(str).tolist()
        roll = tx.collapse_other(roll, dim)
    if ss.view == "BY AGE":
        # age_bucket is an ordered categorical: sort by bucket, keep empty
        # buckets visible as zero rows so the age profile is complete
        roll = roll.sort_values(dim).reset_index(drop=True)

    # search + column set + sparkline toggle + export + rows-per-view
    s1, s2, s3, s4, s5 = st.columns([1.8, 1.4, 0.75, 0.75, 0.9])
    q = s1.text_input("Filter " + cfg["label"].lower(), key="pivot_search",
                      placeholder=f"Filter {cfg['label'].lower()}…",
                      label_visibility="collapsed")
    s2.segmented_control("Columns", COL_SETS, key="pivot_cols",
                         on_change=_sticky, args=("pivot_cols",),
                         label_visibility="collapsed")
    s3.toggle("15-MO TREND", key="show_sparks")
    if q:
        roll = roll[roll[dim].astype(str).str.contains(q, case=False, na=False)]

    # 15-month sparklines: lazy — history is loaded only when the toggle is on.
    # Window is clipped to months <= as-of so the last point matches the table.
    # Resolved BEFORE sorting because the TREND column sorts by its % change.
    hist_flt = None
    sparks = None
    spark_months = [m for m in months if m <= ss.as_of]
    if ss.get("show_sparks"):
        if ss.view == "BY AGE":
            st.caption("15-mo trend isn't available for age buckets "
                       "(bucket cohorts shift month to month).")
        else:
            hist_flt = tx.apply_filters(_history_checked(), status=status_code,
                                        team=g_team, formtype=g_ft,
                                        usedstatus=g_us, brand=g_brand,
                                        subinventory=g_subinv)
            if program_view:
                hist_flt = hist_flt[hist_flt["program"].notna()]
            sparks = tx.sparks_for(hist_flt, dim, spark_months)

    # Click-to-sort. Applied before the export so the XLS matches the screen;
    # the collapsed OTHER bucket stays pinned last whichever way we sort.
    _pivot_spark_key = (lambda r: str(r[dim]))
    roll = _apply_sort("sort_pivot", roll, pin_other_col=dim,
                       sparks=sparks, spark_key=_pivot_spark_key)

    s4.download_button("⬇ Export XLS", to_excel(roll),
                       file_name=f"relic_{dim}_{ss.as_of}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       width="stretch")
    _rows_select(s5, "pivot_rows")

    # ── main pivot: click a row (or use the selectbox below) to drill ──
    options = ["—"] + [o for o in roll[dim].astype(str).tolist()
                       if not o.startswith("OTHER (")]
    current = ss.get("drill_dim", "—")
    if current != "—" and current not in options:
        options.append(current)  # search can hide the selected row; keep the selectbox valid

    # the footer's SUBJ columns are distinct counts, so they can't be summed
    # down the column: recount over the source rows behind the visible rows
    vis = set(roll[dim].astype(str))
    keep = {v for v in vis if not v.startswith("OTHER (")}
    if vis - keep:  # the collapsed OTHER row is visible: include what it folds
        keep |= set(folded_vals)
    subj_totals = tx.subject_count_totals(src[src[dim].astype(str).isin(keep)])

    ev = interactive.table(
        style.pivot_table_html(roll, dim, cfg["label"], clickable=True,
                               selected=None if current == "—" else current,
                               col_set=ss.pivot_cols, sparks=sparks,
                               totals_override=subj_totals,
                               sort=ss.get("sort_pivot")),
        key="pivot_tbl", max_height=_cap_px(ss.pivot_rows),
    )
    if ev.sort:
        _toggle_sort("sort_pivot", ev.sort, roll,
                     sparks=sparks, spark_key=_pivot_spark_key)
    clicked = ev.select
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

    # union count + per-type counts (subjects with items in several types are in
    # each type's count but once in the union, so the parts can exceed the total)
    st.markdown(f"#### {sel} &nbsp; <span style='color:#6b7fa3;font-size:14px'>"
                f"{len(subs)} subjects · {int(subs['ws'].sum())} whole / "
                f"{int(subs['nws'].sum())} non-whole / "
                f"{int(subs['css'].sum())} cut sig</span>", unsafe_allow_html=True)
    sc1, sc2 = st.columns([4, 1])
    sq = sc1.text_input("Filter subjects", key="subj_search",
                        placeholder="Filter subjects…", label_visibility="collapsed")
    _rows_select(sc2, "subj_rows")
    subs_view = subs
    if sq:
        subs_view = subs[subs["subject_name"].str.contains(sq, case=False, na=False)
                         | subs["brand"].str.contains(sq, case=False, na=False)]

    subj_sparks = None
    if hist_flt is not None:
        subj_sparks = tx.sparks_for(hist_flt[hist_flt[dim].astype(str) == sel],
                                    "subj_key", spark_months)

    _subj_spark_key = (lambda r: str(r["subj_key"]))
    subs_view = _apply_sort("sort_subj", subs_view,
                            sparks=subj_sparks, spark_key=_subj_spark_key)

    # ── subjects: click a row (or use the selectbox below) to drill ──
    subj_opts = ["—"] + [f"{r.brand} — {r.subject_name}" for r in subs_view.itertuples()]
    subj_current = ss.get("drill_subj", "—")
    if subj_current != "—" and subj_current not in subj_opts:
        subj_opts.append(subj_current)

    subj_ev = interactive.table(
        style.subject_table_html(subs_view, show_brand, clickable=True,
                                 selected=None if subj_current == "—" else subj_current,
                                 col_set=ss.pivot_cols, sparks=subj_sparks,
                                 sort=ss.get("sort_subj")),
        key="subj_tbl", scroll=scroll_subjects, max_height=_cap_px(ss.subj_rows),
    )
    if subj_ev.sort:
        _toggle_sort("sort_subj", subj_ev.sort, subs_view,
                     sparks=subj_sparks, spark_key=_subj_spark_key)
    subj_clicked = subj_ev.select
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
        # nested program table keeps the legacy TYPES layout (rows are slated-only,
        # so STATUS/AGE columns would be redundant there)
        prog = tx.program_breakdown(sub_src, brand_v, subj_v)
        prog_src = sub_src[(sub_src["brand"] == brand_v)
                           & (sub_src["subject_name"] == subj_v)
                           & (sub_src["status"] == "S")]
        pc1, pc2 = st.columns([4, 1])
        pc1.markdown(f"#### {subj_v} — by program")
        _rows_select(pc2, "prog_rows")
        prog = _apply_sort("sort_prog", prog)
        prog_ev = interactive.table(style.pivot_table_html(
                              prog, "program", "PROGRAM",
                              totals_override=tx.subject_count_totals(prog_src),
                              sort=ss.get("sort_prog")),
                          key="prog_tbl", scroll=scroll_items,
                          max_height=_cap_px(ss.prog_rows))
        if prog_ev.sort:
            _toggle_sort("sort_prog", prog_ev.sort, prog)
    else:
        items = tx.items_for(sub_src, brand_v, subj_v)
        i1, i2, i3 = st.columns([3, 0.8, 1])
        n_items = items["item_number"].nunique()
        i1.markdown(f"#### {subj_v} &nbsp; <span style='color:#6b7fa3;font-size:14px'>"
                    f"{n_items} items · {len(items)} rows</span>", unsafe_allow_html=True)
        iq = i1.text_input("Filter items", key="item_search",
                           placeholder="Filter by item #, bin, or sub-inv…",
                           label_visibility="collapsed")
        _rows_select(i2, "item_rows")
        if iq:
            items = items[
                items["item_number"].str.contains(iq, case=False, na=False)
                | items["bin_location"].astype(str).str.contains(iq, case=False, na=False)
                | items["subinventory_code"].astype(str).str.contains(iq, case=False, na=False)
            ]
        # Click-to-sort. An explicit column sort intentionally breaks the default
        # "rows for one item stay adjacent" grouping — that is the point of it.
        items = _apply_sort("sort_item", items)
        export_df = (
            items.assign(subject=subj_v, brand=brand_v,
                         status=items["status"].map(tx.STATUS_LABELS))
            [["item_number", "item_description", "subject", "brand", "team",
              "relic_form_type", "item_used_status", "subinventory_code",
              "bin_location", "qty_onhand", "valuation",
              "status", "program", "age_days"]]
            .rename(columns={
                "item_number": "Item #", "item_description": "Description",
                "subject": "Subject", "brand": "Brand",
                "team": "Team", "relic_form_type": "Form Type",
                "item_used_status": "Used Status",
                "subinventory_code": "Sub-Inventory", "bin_location": "Bin Location",
                "qty_onhand": "Qty On Hand",
                "valuation": "Valuation (USD)", "status": "Status",
                "program": "Program", "age_days": "Age (days)"})
        )
        i3.download_button("⬇ Export XLS", to_excel(export_df),
                           file_name=f"relic_items_{brand_v}_{subj_v}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           width="stretch")
        item_ev = interactive.table(
            style.item_table_html(items, sort=ss.get("sort_item")),
            key="item_tbl", scroll=scroll_items,
            max_height=_cap_px(ss.item_rows))
        if item_ev.sort:
            _toggle_sort("sort_item", item_ev.sort, items)


def render_trends():
    hist_all = _history_checked()

    t1, t2, t3 = st.columns([2.4, 1.2, 0.7])
    t1.segmented_control("Dimension", list(TREND_DIMS), key="trend_dim",
                         on_change=_sticky, args=("trend_dim",))
    t2.segmented_control("Metric", ["VALUE", "QTY"], key="trend_metric",
                         on_change=_sticky, args=("trend_metric",))
    t3.selectbox("Top N", [8, 12, 20], index=1, key="trend_topn")

    dim = TREND_DIMS[ss.trend_dim]
    is_value = ss.trend_metric == "VALUE"
    metric = "valuation" if is_value else "qty_onhand"
    fmtv = tx.fmt if is_value else tx.fmtq

    # the STATUS dimension needs all statuses present or the map degenerates
    status_dim = dim == "status"
    eff_status = "A" if status_dim else status_code
    hist = tx.apply_filters(hist_all, status=eff_status, team=g_team,
                            formtype=g_ft, usedstatus=g_us, brand=g_brand,
                            subinventory=g_subinv)
    if status_dim and status_code != "A":
        st.caption("Status filter is ignored for the STATUS dimension.")

    long = tx.history_long(hist, dim, metric, months)
    if status_dim:
        long[dim] = long[dim].map(tx.STATUS_LABELS).fillna(long[dim])
    elif dim == "subj_key":
        long[dim] = long[dim].str.replace(" ||| ", " — ", regex=False)
    long = long.rename(columns={dim: "cat"})

    lbl = {m: month_label(m) for m in months}
    month_lbls = [lbl[m] for m in months]
    anchor = ss.as_of

    # ── portfolio tiles: level at as-of + trailing deltas ──
    per_month = long.groupby("month")["value"].sum().reindex(months, fill_value=0.0)
    cur = float(per_month.get(anchor, 0.0))
    idx = months.index(anchor)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"TOTAL ({month_label(anchor)})", fmtv(cur))
    for col, k in ((m2, 1), (m3, 3), (m4, 6)):
        j = idx - k
        if j < 0:
            col.metric(f"Δ {k} MO", "—")
        else:
            base = float(per_month.iloc[j])
            delta = cur - base
            pct = (delta / base * 100.0) if base else 0.0
            col.metric(f"Δ {k} MO", fmtv(delta), f"{pct:+.1f}%")

    folded, cat_order = tx.top_n_other(long, "cat", int(ss.get("trend_topn", 12)), anchor)
    folded = folded.copy()
    folded["mlabel"] = folded["month"].map(lbl)

    st.html(style.open_fct() + '<div class="sec-title">MONTHLY MOVEMENT (MoM Δ — GREEN BUILDING / RED SHRINKING)</div>' + style.close_fct())
    mom = tx.mom_change(folded, "cat")
    if mom.empty:
        st.caption("Not enough history for month-over-month changes.")
    else:
        cap = max(1.0, float(mom["delta"].abs().quantile(0.95)))
        st.altair_chart(charts.mom_heatmap(mom, month_lbls, cat_order,
                                           ss.trend_metric, cap))

    st.html(style.open_fct() + f'<div class="sec-title">TOP {int(ss.get("trend_topn", 12))} TREND — {ss.trend_dim}</div>' + style.close_fct())
    st.altair_chart(charts.trend_lines_chart(
        folded, month_lbls, cat_order,
        "Value ($)" if is_value else "Quantity", is_value))

    st.html(style.open_fct() + f'<div class="sec-title">MOVERS — Δ vs {month_label(anchor)}</div>' + style.close_fct())
    movers = tx.trailing_deltas(long, "cat", months, anchor)
    mc1, mc2 = st.columns([4, 1])
    mq = mc1.text_input("Filter categories", key="movers_search",
                        placeholder="Filter…", label_visibility="collapsed")
    _rows_select(mc2, "movers_rows")
    mv = movers
    if mq:
        mv = movers[movers["cat"].str.contains(mq, case=False, na=False)]
    num = "$%.0f" if is_value else "%.0f"
    st.dataframe(
        mv, hide_index=True, width="stretch",
        height=_df_height(ss.movers_rows, len(mv)),
        column_config={
            "cat": st.column_config.TextColumn(ss.trend_dim.title()),
            "current": st.column_config.NumberColumn(f"Current ({month_label(anchor)})", format=num),
            "d1": st.column_config.NumberColumn("Δ1 mo", format=num),
            "d3": st.column_config.NumberColumn("Δ3 mo", format=num),
            "d6": st.column_config.NumberColumn("Δ6 mo", format=num),
            "p1": st.column_config.NumberColumn("Δ1 %", format="%.1f%%"),
            "p3": st.column_config.NumberColumn("Δ3 %", format="%.1f%%"),
            "p6": st.column_config.NumberColumn("Δ6 %", format="%.1f%%"),
        },
    )
    st.download_button("⬇ Export movers", to_excel(mv),
                       file_name=f"relic_trend_movers_{dim}_{ss.as_of}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    st.html(style.open_fct() + '<div class="sec-title">VALUE MIX (ALL MONTHS)</div>' + style.close_fct())
    mix = (hist.groupby(["month", "itype"], observed=True)["valuation"]
           .sum().reset_index())
    mix["snapshot"] = mix["month"].astype(str).map(lbl)
    mix_rows = (mix.rename(columns={"itype": "type", "valuation": "value"})
                [["snapshot", "type", "value"]])
    st.altair_chart(charts.value_mix_chart(month_lbls, mix_rows.to_dict("records")))


def render_aging():
    # aging always spans all statuses — the global Status control is disabled on
    # this tab (the stale report below has its own status filter)
    df = tx.apply_filters(raw, status="A", team=g_team, formtype=g_ft,
                          usedstatus=g_us, brand=g_brand, subinventory=g_subinv)
    st.caption("Aging spans all statuses; the stale report has its own status filter. "
               "Age basis: earliest true RECEIPT_DATE from FCT_INVENTORY_AGING, which "
               "also defines the on-hand universe for the whole dashboard — items it "
               "doesn't carry are excluded everywhere. Ages are LOWER BOUNDS: the "
               "aging window opens 2025-05-30, so anything received earlier reports "
               "that date, and a receipt later in the as-of month counts as 0 days.")
    st.html(style.open_fct() + style.aging_cards_html(tx.aging_stats(df)) + style.close_fct())

    st.html(style.open_fct() + '<div class="sec-title">AGE PROFILE — VALUE BY BUCKET AND STATUS</div>' + style.close_fct())
    prof = (df.groupby(["age_bucket", "status"], dropna=False, observed=False)
            ["valuation"].sum().reset_index())
    prof["status"] = prof["status"].map(tx.STATUS_LABELS).fillna(prof["status"])
    prof = prof.rename(columns={"age_bucket": "bucket", "valuation": "value"})
    prof["bucket"] = prof["bucket"].astype(str)
    st.altair_chart(charts.age_profile_chart(prof, tx.AGE_LABELS))

    st.html(style.open_fct() + '<div class="sec-title">STALE INVENTORY REPORT</div>' + style.close_fct())
    r1, r2, r3 = st.columns([2, 1.6, 0.9])
    thr = r1.slider("Stale threshold (days)", 90, 730, value=365, key="stale_days")
    r2.segmented_control("Report status", ["UNSLATED", "SLATED", "OBSOLETE", "ALL"],
                         key="stale_status", on_change=_sticky, args=("stale_status",))
    _rows_select(r3, "stale_rows")
    st.caption("Defaults (365 days / unslated) are placeholders — the stale rule is "
               "TBD with the business and may differ by case.")
    sel = ss.stale_status
    statuses = {"U", "S", "O"} if sel == "ALL" else {_STATUS_CODES[sel]}
    rep = tx.stale_items(df, thr, statuses)
    disp = (rep.assign(status=rep["status"].map(tx.STATUS_LABELS))
            .rename(columns={
                "item_number": "Item #", "brand": "Brand", "subject_name": "Subject",
                "team": "Team", "relic_form_type": "Form Type",
                "item_used_status": "Used Status", "status": "Status",
                "program": "Program", "age_days": "Age (days)",
                "qty_onhand": "Qty", "valuation": "Valuation (USD)"}))
    st.dataframe(
        disp, hide_index=True, width="stretch",
        height=_df_height(ss.stale_rows, len(disp)),
        column_config={
            "Age (days)": st.column_config.NumberColumn(format="%d"),
            "Qty": st.column_config.NumberColumn(format="%.0f"),
            "Valuation (USD)": st.column_config.NumberColumn(format="$%.0f"),
        },
    )
    st.download_button("⬇ Export stale report", to_excel(disp),
                       file_name=f"relic_stale_{thr}mo_{ss.as_of}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if ss.tab == "TRENDS":
    render_trends()
elif ss.tab == "AGING":
    render_aging()
else:
    render_inventory()
