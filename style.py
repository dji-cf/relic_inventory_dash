"""Pixel-faithful chrome: CSS + HTML builders ported from the source dashboard.

All selectors are scoped under a `.fct` wrapper so they never collide with
Streamlit's own DOM. Pivot tables are rendered as HTML to reproduce the
column-group color bands, gold TOTAL column, and per-row percentage bars;
they are mounted through the interactive.table component (shadow DOM), so
their CSS ships separately as TABLE_CSS while the page chrome uses CSS.
Drill-down is driven by row clicks + synced Streamlit selectors.
"""
from __future__ import annotations

from html import escape

import streamlit as st

from transforms import ROLLUP_COLS, fmt, fmtq

# Shared base (fonts + palette vars): needed in the page stylesheet for the
# chrome AND inside each table component, because page styles can't pierce
# the component's shadow root.
_BASE_CSS = """
.fct, .fct * { box-sizing: border-box; font-family: 'Inter', -apple-system, 'Segoe UI', Arial, sans-serif; }
.fct {
  --surface:#fff; --surface2:#f8f9fc; --border:#e2e8f0; --accent:#4d7ab8;
  --whole:#4d7ab8; --nonwhole:#16a34a; --cutsig:#dc2626; --text:#1a2b4a;
  --muted:#6b7fa3; --gold:#b5822a;
}
"""

_CHROME_CSS = """
/* HEADER */
.fct .hdr { background:#1a2b4a; border-radius:10px; padding:14px 22px; margin-bottom:18px;
  box-shadow:0 2px 8px rgba(0,0,0,.25); }
.fct .hdr-top { display:flex; align-items:center; gap:18px; flex-wrap:wrap; }
.fct .logo { font-size:24px; font-weight:700; letter-spacing:1px; color:#fff; line-height:1; }
.fct .logo span { color:#7a99c0; font-size:15px; font-weight:600; }
.fct .hdr-meta { margin-left:auto; display:flex; gap:26px; font-size:12px; color:#7a99c0; }
.fct .hdr-meta b { color:#fff; font-size:14px; display:block; margin-top:2px; }

/* CONTEXT BAR */
.fct .context-bar { display:flex; align-items:center; gap:10px; margin:0 0 14px; }
.fct .context-status, .fct .context-view {
  font-family:'DM Mono',monospace; font-size:12px; font-weight:700; letter-spacing:.8px;
  padding:4px 11px; border-radius:5px; color:#fff; }
.fct .context-status { background:var(--gold); }
.fct .context-status.status-U { background:var(--whole); }
.fct .context-status.status-S { background:var(--nonwhole); }
.fct .context-status.status-O { background:var(--cutsig); }
.fct .context-view { background:#5a7fbf; }
.fct .context-sep { color:var(--muted); }

/* STAT CARDS */
.fct .stat-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:14px; margin-bottom:22px; }
.fct .stat-card { background:var(--surface); border-radius:8px; padding:16px 20px;
  box-shadow:0 1px 4px rgba(0,0,0,.08); border-top:3px solid var(--accent); }
.fct .stat-card.total { border-top-color:var(--gold); background:#fffbf0; }
.fct .stat-card.nonwhole { border-top-color:var(--nonwhole); }
.fct .stat-card.cutsig { border-top-color:var(--cutsig); }
.fct .stat-card .lbl { font-size:10px; font-weight:700; text-transform:uppercase;
  letter-spacing:.7px; color:var(--muted); margin-bottom:6px; }
.fct .stat-card .val { font-size:26px; font-weight:700; line-height:1.1; letter-spacing:-.5px;
  font-variant-numeric:tabular-nums; }
.fct .stat-card .sub { font-size:11px; color:var(--muted); margin-top:3px; font-variant-numeric:tabular-nums; }

/* LEGEND */
.fct .legend { display:flex; gap:20px; flex-wrap:wrap; margin-bottom:14px; }
.fct .legend-item { display:flex; align-items:center; gap:8px; font-size:12px;
  color:var(--muted); font-family:'DM Mono',monospace; }
.fct .legend-dot { width:10px; height:10px; border-radius:2px; }

/* TREND TITLES */
.fct .sec-title { font-size:12px; font-weight:700; letter-spacing:1px; text-transform:uppercase;
  color:var(--muted); margin:8px 0 12px; display:flex; align-items:center; gap:10px; }
.fct .sec-title::after { content:''; flex:1; height:1px; background:var(--border); }

/* Hide the vega-embed actions menu (⋯ Save as SVG/PNG, View Source, …). SiS/Streamlit
   1.51 force-shows it with vega-embed's own CSS disabled (defaultStyle:false), so it
   renders unstyled/oversized. Not needed on this dashboard. Global (not .fct-scoped)
   since the toolbar lives outside the .fct chrome. */
.vega-embed details,
.vega-embed summary,
.vega-embed .vega-actions {
  display: none !important;
}
"""

# Raw CSS (no <style> tag) for the tables mounted via interactive.table —
# injected into each component's shadow root.
TABLE_CSS = _BASE_CSS + """
/* TABLES */
.fct .table-wrap { background:var(--surface); border-radius:8px; overflow-x:auto;
  box-shadow:0 1px 4px rgba(0,0,0,.08); margin-bottom:8px; }
.fct table { width:100%; border-collapse:collapse; }
.fct thead th { background:#1a2b4a; padding:8px 12px; text-align:right; font-size:10px;
  font-weight:700; letter-spacing:.4px; text-transform:uppercase; color:#fff; white-space:nowrap; }
.fct thead th:first-child { text-align:left; background:#000; }
.fct .cg th { font-size:10px; font-weight:800; padding:6px 12px; text-align:center; color:#fff; }
.fct .cg th.th-whole { background:#1f3d6a; }
.fct .cg th.th-nonwhole { background:#166534; }
.fct .cg th.th-cutsig { background:#7f1d1d; }
.fct .cg th:first-child { background:#000; }
.fct .cg th.th-total { background:var(--gold); }
.fct th.th-whole { background:#1f3d6a; }
.fct th.th-nonwhole { background:#166534; }
.fct th.th-cutsig { background:#7f1d1d; }
.fct th.th-total, .fct td.td-total { }
.fct thead th.th-total { background:var(--gold); color:#fff; }
.fct td { padding:9px 12px; text-align:right; font-size:12px; font-variant-numeric:tabular-nums;
  border-bottom:1px solid var(--border); color:var(--text); white-space:nowrap; }
.fct td:first-child { text-align:left; font-size:13px; font-weight:500; max-width:340px;
  overflow:hidden; text-overflow:ellipsis; }
.fct tbody tr:hover { background:var(--surface2); }
.fct .td-whole { color:var(--whole); }
.fct .td-nonwhole { color:var(--nonwhole); }
.fct .td-cutsig { color:var(--cutsig); }
.fct .td-total { font-weight:700; color:#000; font-size:13px; }
.fct .tfoot-row { background:var(--surface2); border-top:2px solid var(--border); }
.fct .tfoot-row td { font-weight:700; font-size:12px; }
.fct .sub-label { color:var(--muted); font-size:10px; font-weight:400; }

/* PERCENTAGE BAR */
.fct .bar { display:flex; gap:2px; height:4px; border-radius:3px; overflow:hidden;
  width:110px; margin-top:5px; }
.fct .bar span { height:100%; }

/* ROW INTERACTIVITY (colors from the source template) */
.fct tbody tr[data-key] { cursor:pointer; }
.fct tbody tr.selected { background:#eff6ff; border-left:3px solid #4d7ab8; }
.fct tbody tr.selected td:first-child { padding-left:13px; }
/* keep the sticky app header + panel heading/search visible on scrollIntoView */
.fct .table-wrap { scroll-margin-top:160px; }
"""

CSS = "<style>" + _BASE_CSS + _CHROME_CSS + "</style>"


def inject_css():
    st.html(CSS)


def open_fct() -> str:
    return '<div class="fct">'


def close_fct() -> str:
    return "</div>"


def _bar(wv, nwv, csv) -> str:
    t = (wv or 0) + (nwv or 0) + (csv or 0)
    if t <= 0:
        return '<div class="bar"></div>'
    wp, np_, cp = wv / t * 100, nwv / t * 100, csv / t * 100
    return (
        '<div class="bar">'
        f'<span style="background:var(--whole);width:{wp:.1f}%"></span>'
        f'<span style="background:var(--nonwhole);width:{np_:.1f}%"></span>'
        f'<span style="background:var(--cutsig);width:{cp:.1f}%"></span>'
        "</div>"
    )


def header_html(cards: dict, as_of_label: str) -> str:
    return (
        '<div class="hdr"><div class="hdr-top">'
        '<div class="logo">FCT Relic <span>/ Inventory Dashboard</span></div>'
        '<div class="hdr-meta">'
        f'<div>AS OF<b>{escape(as_of_label)}</b></div>'
        f'<div>BRANDS<b>{cards["brands"]}</b></div>'
        f'<div>TOTAL VALUE<b>{fmt(cards["total_val"])}</b></div>'
        "</div></div></div>"
    )


def context_bar_html(status: str, view_label: str) -> str:
    labels = {"A": "ALL INVENTORY", "U": "UNSLATED", "S": "SLATED", "O": "OBSOLETE"}
    scls = "" if status == "A" else f" status-{status}"
    return (
        '<div class="context-bar">'
        f'<span class="context-status{scls}">{labels.get(status, "ALL INVENTORY")}</span>'
        '<span class="context-sep">·</span>'
        f'<span class="context-view">{escape(view_label)}</span>'
        "</div>"
    )


def legend_html() -> str:
    return (
        '<div class="legend">'
        '<div class="legend-item"><div class="legend-dot" style="background:var(--whole)"></div>WHOLE (MEM prefix)</div>'
        '<div class="legend-item"><div class="legend-dot" style="background:var(--nonwhole)"></div>NON-WHOLE (other)</div>'
        '<div class="legend-item"><div class="legend-dot" style="background:var(--cutsig)"></div>CUT SIG (form type)</div>'
        "</div>"
    )


def stat_cards_html(c: dict) -> str:
    return (
        '<div class="stat-cards">'
        '<div class="stat-card total"><div class="lbl">TOTAL INVENTORY VALUE</div>'
        f'<div class="val" style="color:var(--gold)">{fmt(c["total_val"])}</div>'
        f'<div class="sub">{c["brands"]} brands &nbsp;·&nbsp; {fmtq(c["total_subj"])} subjects</div></div>'
        '<div class="stat-card"><div class="lbl">◆ WHOLE VALUE</div>'
        f'<div class="val" style="color:var(--whole)">{fmt(c["whole_val"])}</div>'
        f'<div class="sub">{fmtq(c["whole_qty"])} items &nbsp;·&nbsp; {fmtq(c["whole_subj"])} subjects</div></div>'
        '<div class="stat-card nonwhole"><div class="lbl">◆ NON-WHOLE VALUE</div>'
        f'<div class="val" style="color:var(--nonwhole)">{fmt(c["nonwhole_val"])}</div>'
        f'<div class="sub">{fmtq(c["nonwhole_qty"])} items &nbsp;·&nbsp; {fmtq(c["nonwhole_subj"])} subjects</div></div>'
        '<div class="stat-card cutsig"><div class="lbl">◆ CUT SIG VALUE</div>'
        f'<div class="val" style="color:var(--cutsig)">{fmt(c["cutsig_val"])}</div>'
        f'<div class="sub">{fmtq(c["cutsig_qty"])} items &nbsp;·&nbsp; {fmtq(c["cutsig_subj"])} subjects</div></div>'
        "</div>"
    )


_GROUP_HEADER = (
    '<tr class="cg"><th></th>'
    '<th colspan="3" class="th-whole">◆ WHOLE</th>'
    '<th colspan="3" class="th-nonwhole">◆ NON-WHOLE</th>'
    '<th colspan="3" class="th-cutsig">◆ CUT SIG</th>'
    '<th class="th-total"></th></tr>'
)
_COL_HEADER = (
    "<tr><th>{label}</th>"
    '<th class="th-whole">QTY</th><th class="th-whole">SUBJ</th><th class="th-whole">VALUE</th>'
    '<th class="th-nonwhole">QTY</th><th class="th-nonwhole">SUBJ</th><th class="th-nonwhole">VALUE</th>'
    '<th class="th-cutsig">QTY</th><th class="th-cutsig">SUBJ</th><th class="th-cutsig">VALUE</th>'
    '<th class="th-total">TOTAL VALUE</th></tr>'
)


def _row_attrs(key: str, clickable: bool, selected: str | None) -> str:
    """data-key + selected-class attributes for a drillable tbody row."""
    if not clickable:
        return ""
    cls = ' class="selected"' if selected is not None and key == selected else ""
    return f' data-key="{escape(key)}"{cls}'


def pivot_table_html(roll, dim_col: str, dim_label: str,
                     clickable: bool = False, selected: str | None = None) -> str:
    """3-type rollup table with per-row percentage bars and a TOTAL footer."""
    rows = []
    for _, r in roll.iterrows():
        val = str(r[dim_col])
        # the collapsed OTHER row has no drill target
        attrs = _row_attrs(val, clickable and not val.startswith("OTHER ("), selected)
        rows.append(
            f"<tr{attrs}>"
            f'<td><div>{escape(val)}</div>{_bar(r["wv"], r["nwv"], r["csv"])}</td>'
            f'<td class="td-whole">{fmtq(r["wq"])}</td><td class="td-whole">{fmtq(r["ws"])}</td><td class="td-whole">{fmt(r["wv"])}</td>'
            f'<td class="td-nonwhole">{fmtq(r["nwq"])}</td><td class="td-nonwhole">{fmtq(r["nws"])}</td><td class="td-nonwhole">{fmt(r["nwv"])}</td>'
            f'<td class="td-cutsig">{fmtq(r["csq"])}</td><td class="td-cutsig">{fmtq(r["css"])}</td><td class="td-cutsig">{fmt(r["csv"])}</td>'
            f'<td class="td-total">{fmt(r["tv"])}</td></tr>'
        )
    t = {c: roll[c].sum() for c in ROLLUP_COLS}
    foot = (
        '<tr class="tfoot-row"><td>TOTAL</td>'
        f'<td class="td-whole">{fmtq(t["wq"])}</td><td class="td-whole">{fmtq(t["ws"])}</td><td class="td-whole">{fmt(t["wv"])}</td>'
        f'<td class="td-nonwhole">{fmtq(t["nwq"])}</td><td class="td-nonwhole">{fmtq(t["nws"])}</td><td class="td-nonwhole">{fmt(t["nwv"])}</td>'
        f'<td class="td-cutsig">{fmtq(t["csq"])}</td><td class="td-cutsig">{fmtq(t["css"])}</td><td class="td-cutsig">{fmt(t["csv"])}</td>'
        f'<td class="td-total">{fmt(t["tv"])}</td></tr>'
    )
    body = "".join(rows) or '<tr><td colspan="11" style="text-align:center;color:var(--muted);padding:30px">No rows match your filter.</td></tr>'
    return (
        '<div class="table-wrap"><table><thead>'
        + _GROUP_HEADER
        + _COL_HEADER.format(label=escape(dim_label))
        + "</thead><tbody>"
        + body
        + "</tbody><tfoot>"
        + foot
        + "</tfoot></table></div>"
    )


def subject_table_html(subs, show_brand_sublabel: bool = False,
                       clickable: bool = False, selected: str | None = None) -> str:
    """Subject-level drill table (QTY/VALUE per type, no subject counts)."""
    group = (
        '<tr class="cg"><th></th>'
        '<th colspan="2" class="th-whole">◆ WHOLE</th>'
        '<th colspan="2" class="th-nonwhole">◆ NON-WHOLE</th>'
        '<th colspan="2" class="th-cutsig">◆ CUT SIG</th>'
        '<th class="th-total"></th></tr>'
    )
    cols = (
        "<tr><th>SUBJECT</th>"
        '<th class="th-whole">QTY</th><th class="th-whole">VALUE</th>'
        '<th class="th-nonwhole">QTY</th><th class="th-nonwhole">VALUE</th>'
        '<th class="th-cutsig">QTY</th><th class="th-cutsig">VALUE</th>'
        '<th class="th-total">TOTAL VALUE</th></tr>'
    )
    rows = []
    for _, r in subs.iterrows():
        # data-key mirrors the drill_subj option string ("BRAND — SUBJECT")
        attrs = _row_attrs(f'{r["brand"]} — {r["subject_name"]}', clickable, selected)
        name = escape(str(r["subject_name"]))
        if show_brand_sublabel:
            name = f'<div>{name}</div><div class="sub-label">{escape(str(r["brand"]))}</div>'
        rows.append(
            f"<tr{attrs}>"
            f"<td>{name}</td>"
            f'<td class="td-whole">{fmtq(r["wq"])}</td><td class="td-whole">{fmt(r["wv"])}</td>'
            f'<td class="td-nonwhole">{fmtq(r["nwq"])}</td><td class="td-nonwhole">{fmt(r["nwv"])}</td>'
            f'<td class="td-cutsig">{fmtq(r["csq"])}</td><td class="td-cutsig">{fmt(r["csv"])}</td>'
            f'<td class="td-total">{fmt(r["tv"])}</td></tr>'
        )
    t = {c: subs[c].sum() for c in ROLLUP_COLS}
    foot = (
        '<tr class="tfoot-row"><td>TOTAL (filtered)</td>'
        f'<td class="td-whole">{fmtq(t["wq"])}</td><td class="td-whole">{fmt(t["wv"])}</td>'
        f'<td class="td-nonwhole">{fmtq(t["nwq"])}</td><td class="td-nonwhole">{fmt(t["nwv"])}</td>'
        f'<td class="td-cutsig">{fmtq(t["csq"])}</td><td class="td-cutsig">{fmt(t["csv"])}</td>'
        f'<td class="td-total">{fmt(t["tv"])}</td></tr>'
    )
    body = "".join(rows) or '<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:30px">No subjects found.</td></tr>'
    return ('<div class="table-wrap"><table><thead>' + group + cols
            + "</thead><tbody>" + body + "</tbody><tfoot>" + foot + "</tfoot></table></div>")


def item_table_html(items) -> str:
    head = (
        "<tr><th>ITEM #</th><th>TEAM</th><th>FORM TYPE</th><th>USED STATUS</th>"
        '<th>QTY</th><th>UNIT COST</th><th class="th-total">VALUATION</th></tr>'
    )
    rows = []
    for _, r in items.iterrows():
        rows.append(
            "<tr>"
            f'<td>{escape(str(r["item_number"]))}</td>'
            f'<td style="text-align:left">{escape(str(r["team"]) or "—")}</td>'
            f'<td style="text-align:left">{escape(str(r["relic_form_type"]) or "—")}</td>'
            f'<td style="text-align:left">{escape(str(r["item_used_status"]) or "—")}</td>'
            f'<td>{fmtq(r["qty_onhand"])}</td>'
            f'<td>${r["unit_cost"]:,.2f}</td>'
            f'<td class="td-total">{fmt(r["valuation"])}</td></tr>'
        )
    tq = items["qty_onhand"].sum()
    tv = items["valuation"].sum()
    foot = (
        '<tr class="tfoot-row"><td colspan="4">TOTAL (filtered)</td>'
        f'<td>{fmtq(tq)}</td><td></td><td class="td-total">{fmt(tv)}</td></tr>'
    )
    body = "".join(rows) or '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:30px">No items found.</td></tr>'
    return ('<div class="table-wrap"><table><thead>' + head
            + "</thead><tbody>" + body + "</tbody><tfoot>" + foot + "</tfoot></table></div>")
