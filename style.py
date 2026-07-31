"""Pixel-faithful chrome: CSS + HTML builders ported from the source dashboard.

All selectors are scoped under a `.fct` wrapper so they never collide with
Streamlit's own DOM. Pivot tables are rendered as HTML to reproduce the
column-group color bands, gold TOTAL column, and per-row percentage bars;
they are mounted through the interactive.table component (shadow DOM), so
their CSS ships separately as TABLE_CSS while the page chrome uses CSS.
Drill-down is driven by row clicks + synced Streamlit selectors.

The pivot/subject tables are built from a shared column-spec builder so the
column-set control (STANDARD / +AGE / STATUS + AGE) and the sparkline TREND
column compose without duplicated markup. "TYPES" is the legacy 11-column
layout kept for the nested program breakdown table.
"""
from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from transforms import EXT_COLS, ROLLUP_COLS, STATUS_LABELS, fmt, fmt2, fmtq

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

/* Hide the vega-embed actions menu (⋯ Save as SVG/PNG, View Source, …). Not wanted
   on this dashboard, and Streamlit builds that force-show it do so with vega-embed's
   own CSS disabled (defaultStyle:false), rendering it unstyled/oversized — so hide it
   defensively. Global (not .fct-scoped) since the toolbar lives outside the .fct
   chrome. */
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

/* STATUS / AGE / TREND column bands */
.fct .cg th.th-status, .fct thead th.th-status { background:#3d4f6b; }
.fct .cg th.th-age, .fct thead th.th-age { background:#57534e; }
.fct .cg th.th-trend, .fct thead th.th-trend { background:#334155; }
.fct .td-slated { color:var(--nonwhole); }
.fct .td-unslated { color:var(--whole); }
.fct .td-age { color:var(--muted); }
/* item-panel DESCRIPTION: deliberately NOT truncated — the cell sizes to its
   content and .table-wrap scrolls horizontally. Descriptions run to ~160 chars
   (p90 is 76), so any max-width here clips the long tail, which is exactly what
   this column is meant to avoid. See the ITEM # sticky rule below: it is what
   keeps rows identifiable once the table is scrolled sideways. */
.fct td.td-desc { text-align:left; white-space:nowrap; }
.fct .td-trend { white-space:nowrap; }
.fct .td-trend svg.spark { vertical-align:middle; margin-right:6px; }
.fct .chip { display:inline-block; font-size:10px; font-weight:700; padding:1px 6px;
  border-radius:4px; vertical-align:middle; }
.fct .chip-up { background:#dcfce7; color:#166534; }
.fct .chip-down { background:#fee2e2; color:#7f1d1d; }
.fct .chip-new { background:#e0e7ff; color:#3730a3; }

/* item-panel status text */
.fct .st { font-size:11px; font-weight:700; }
.fct .st-U { color:var(--whole); }
.fct .st-S { color:var(--nonwhole); }
.fct .st-O { color:var(--cutsig); }

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

/* PINNED FIRST COLUMN: the uncapped DESCRIPTION cell (see .td-desc) lets the
   item table grow past its container, and .table-wrap scrolls horizontally.
   Pin the name column so every row stays identifiable when scrolled right.
   z-index stays at 1 — BELOW the sticky thead/tfoot (z-index:2) — so the header
   and TOTAL footer still paint over it at the corners. A sticky cell is
   transparent by default, so the tbody backgrounds below are required or
   scrolling content shows through; .selected comes last to beat :hover on the
   same specificity, matching how the row-level rules already resolve.
   tfoot is deliberately NOT pinned: its first cell is a wide colspan (8 in the
   item table), so pinning it would park a multi-column background at the left
   edge while the columns it spans scroll underneath. The footer numbers stay
   aligned under their own columns instead. */
.fct thead th:first-child,
.fct tbody td:first-child { position:sticky; left:0; z-index:1; }
.fct tbody td:first-child { background:var(--surface); }
.fct tbody tr:hover td:first-child { background:var(--surface2); }
.fct tbody tr.selected td:first-child { background:#eff6ff; }

/* ROWS-PER-VIEW CAP: .scrollable is toggled by interactive.py's JS when a
   max_height is passed. Bound the height, scroll the overflow, and pin the
   header + TOTAL footer so they stay visible while scrolling. The whole thead
   / tfoot stick as a block (handles the two-row pivot/subject group header and
   the single-row item header uniformly — no per-row top offset to tune). */
.fct .table-wrap.scrollable { overflow-y:auto; }
.fct .table-wrap.scrollable thead { position:sticky; top:0; z-index:2; }
.fct .table-wrap.scrollable tfoot { position:sticky; bottom:0; z-index:2; }
"""

# Sidebar sizing for the "Ask the data" assistant (left side — Streamlit default).
# The width is applied ONLY while the sidebar is expanded so collapsing it lets
# the dashboard reclaim the space; a blanket min-width leaves an empty gap on
# collapse. Global (not .fct-scoped).
_SIDEBAR_CSS = """
[data-testid="stSidebar"][aria-expanded="true"] { min-width: 360px; max-width: 420px; }
"""

CSS = "<style>" + _BASE_CSS + _CHROME_CSS + _SIDEBAR_CSS + "</style>"


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
        f'<div>TOTAL VALUE<b>{fmt2(cards["total_val"])}</b></div>'
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
        f'<div class="val" style="color:var(--gold)">{fmt2(c["total_val"])}</div>'
        f'<div class="sub">{c["brands"]} brands &nbsp;·&nbsp; {fmtq(c["total_subj"])} subjects</div></div>'
        '<div class="stat-card"><div class="lbl">◆ WHOLE VALUE</div>'
        f'<div class="val" style="color:var(--whole)">{fmt2(c["whole_val"])}</div>'
        f'<div class="sub">{fmtq(c["whole_items"])} distinct items &nbsp;·&nbsp; {fmtq(c["whole_subj"])} subjects</div></div>'
        '<div class="stat-card nonwhole"><div class="lbl">◆ NON-WHOLE VALUE</div>'
        f'<div class="val" style="color:var(--nonwhole)">{fmt2(c["nonwhole_val"])}</div>'
        f'<div class="sub">{fmtq(c["nonwhole_items"])} distinct items &nbsp;·&nbsp; {fmtq(c["nonwhole_subj"])} subjects</div></div>'
        '<div class="stat-card cutsig"><div class="lbl">◆ CUT SIG VALUE</div>'
        f'<div class="val" style="color:var(--cutsig)">{fmt2(c["cutsig_val"])}</div>'
        f'<div class="sub">{fmtq(c["cutsig_items"])} distinct items &nbsp;·&nbsp; {fmtq(c["cutsig_subj"])} subjects</div></div>'
        "</div>"
    )


def aging_cards_html(a: dict) -> str:
    """Headline aging cards for the AGING tab (reuses the .stat-card chrome)."""
    avg = "—" if a.get("avg_age") is None else f'{a["avg_age"]:.0f} days'
    pct = "—" if a.get("pct_unslated_12") is None else f'{a["pct_unslated_12"]:.0f}%'
    share = (a["total_12"] / a["total_val"] * 100.0) if a.get("total_val") else 0.0
    return (
        '<div class="stat-cards">'
        '<div class="stat-card total"><div class="lbl">VALUE >1YR</div>'
        f'<div class="val" style="color:var(--gold)">{fmt(a["total_12"])}</div>'
        f'<div class="sub">{share:.0f}% of filtered value</div></div>'
        '<div class="stat-card"><div class="lbl">VALUE-WEIGHTED AVG AGE</div>'
        f'<div class="val" style="color:var(--accent)">{avg}</div>'
        '<div class="sub">age is a lower bound (see note)</div></div>'
        '<div class="stat-card cutsig"><div class="lbl">UNSLATED VALUE >1YR</div>'
        f'<div class="val" style="color:var(--cutsig)">{pct}</div>'
        '<div class="sub">share of unslated value</div></div>'
        '<div class="stat-card nonwhole"><div class="lbl">ITEMS >1YR</div>'
        f'<div class="val" style="color:var(--nonwhole)">{fmtq(a["items_12"])}</div>'
        '<div class="sub">distinct item numbers</div></div>'
        "</div>"
    )


# ── sparklines ────────────────────────────────────────────────────────────────
def _sparkline_svg(vals: list, w: int = 96, h: int = 22) -> str:
    """Inline-SVG mini trend line (green when last ≥ first-nonzero, else red).

    Trusted builder output — mounted through interactive.table's innerHTML;
    clicks bubble to the row's tr[data-key] listener, so drilling keeps working.
    """
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    pad = 2.0
    n = len(vals)
    xs = [pad + (w - 2 * pad) * (i / (n - 1) if n > 1 else 0.5) for i in range(n)]
    if hi <= lo:  # flat series -> midline
        ys = [h / 2.0] * n
    else:
        ys = [pad + (h - 2 * pad) * (1 - (v - lo) / (hi - lo)) for v in vals]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    first_nz = next((v for v in vals if v > 0), None)
    up = first_nz is not None and vals[-1] >= first_nz
    color = "var(--nonwhole)" if up else "var(--cutsig)"
    return (
        f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/>'
        f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="2" fill="{color}"/></svg>'
    )


def _trend_cell(entry) -> str:
    """TREND cell: sparkline + % chip. entry is (vals, pct) from tx.sparks_for;
    None (all-zero series / missing key) renders empty."""
    if not entry:
        return ""
    vals, pct = entry
    if pct is None:
        chip = '<span class="chip chip-new">NEW</span>'
    else:
        cls = "chip-up" if pct >= 0 else "chip-down"
        chip = f'<span class="chip {cls}">{pct:+.0f}%</span>'
    return _sparkline_svg(vals) + chip


# ── column-spec table machinery ───────────────────────────────────────────────
# A column group is (group_label, header_css, [(col_header, cell_fn, td_css)]).
# cell_fn(d) formats a cell from either a row Series or the footer totals dict,
# so footer ratios (avg age, %>1yr) derive from the summed additive columns.
_SUMMABLE = set(ROLLUP_COLS + EXT_COLS)


def _fmt_avg_age(d) -> str:
    av = float(d["av"] or 0)
    return f'{float(d["agev"]) / av:.0f} days' if av > 0 else "—"


def _fmt_pct12(d) -> str:
    tv = float(d["tv"] or 0)
    return f'{float(d["v12"]) / tv * 100:.0f}%' if tv > 0 else "—"


def _type_groups(three_stats: bool):
    def cols(p, cls):
        out = [("QTY", lambda d, k=f"{p}q": fmtq(d[k]), cls)]
        if three_stats:
            out.append(("SUBJ", lambda d, k=f"{p}s": fmtq(d[k]), cls))
        out.append(("VALUE", lambda d, k=f"{p}v": fmt(d[k]), cls))
        return out

    return [
        ("◆ WHOLE", "th-whole", cols("w", "td-whole")),
        ("◆ NON-WHOLE", "th-nonwhole", cols("nw", "td-nonwhole")),
        ("◆ CUT SIG", "th-cutsig", cols("cs", "td-cutsig")),
    ]


_TOTAL_GROUP = ("", "th-total", [("TOTAL VALUE", lambda d: fmt(d["tv"]), "td-total")])
_STATUS_GROUP = ("◆ STATUS", "th-status", [
    ("SLATED", lambda d: fmt(d["slv"]), "td-slated"),
    ("UNSLATED", lambda d: fmt(d["uslv"]), "td-unslated"),
])
_AGE_GROUP = ("◆ AGE", "th-age", [
    ("AVG AGE", _fmt_avg_age, "td-age"),
    ("%>1yr VAL", _fmt_pct12, "td-age"),
])


def _col_groups(col_set: str, three_stats: bool):
    """Column groups for a col_set. "TYPES" is the legacy 11-column layout
    (kept for the nested program table); STANDARD adds SLATED/UNSLATED;
    +AGE adds the age ratios; STATUS + AGE drops the type groups entirely."""
    if col_set == "STATUS + AGE":
        return [_TOTAL_GROUP, _STATUS_GROUP, _AGE_GROUP]
    groups = _type_groups(three_stats) + [_TOTAL_GROUP]
    if col_set in ("STANDARD", "+AGE"):
        groups = groups + [_STATUS_GROUP]
    if col_set == "+AGE":
        groups = groups + [_AGE_GROUP]
    return groups


def _table_html(groups, first_header, frame, name_cell, row_attrs, spark_key,
                sparks, foot_label, empty_msg,
                totals_override: dict | None = None) -> str:
    """Shared builder for the pivot + subject drill tables."""
    has_trend = sparks is not None
    ghead = ['<tr class="cg"><th></th>']
    chead = [f"<tr><th>{escape(first_header)}</th>"]
    for glabel, gcss, cols in groups:
        ghead.append(f'<th colspan="{len(cols)}" class="{gcss}">{glabel}</th>')
        for h, _fn, _cls in cols:
            chead.append(f'<th class="{gcss}">{h}</th>')
    if has_trend:
        ghead.append('<th class="th-trend"></th>')
        chead.append('<th class="th-trend">TREND · Δ</th>')
    ghead.append("</tr>")
    chead.append("</tr>")

    ncols = 1 + sum(len(g[2]) for g in groups) + (1 if has_trend else 0)
    body = []
    for _, r in frame.iterrows():
        cells = [f"<td>{name_cell(r)}</td>"]
        for _g, _css, cols in groups:
            for _h, fn, cls in cols:
                cells.append(f'<td class="{cls}">{fn(r)}</td>')
        if has_trend:
            cells.append(f'<td class="td-trend">{_trend_cell(sparks.get(spark_key(r)))}</td>')
        body.append(f"<tr{row_attrs(r)}>" + "".join(cells) + "</tr>")
    if not body:
        body = [f'<tr><td colspan="{ncols}" style="text-align:center;color:var(--muted);'
                f'padding:30px">{empty_msg}</td></tr>']

    totals = {c: frame[c].sum() for c in frame.columns if c in _SUMMABLE}
    # distinct-count columns (ws/nws/css) aren't additive across rows — callers
    # pass true distinct totals to replace the double-counting column sums
    totals.update(totals_override or {})
    fcells = [f"<td>{escape(foot_label)}</td>"]
    for _g, _css, cols in groups:
        for _h, fn, cls in cols:
            fcells.append(f'<td class="{cls}">{fn(totals)}</td>')
    if has_trend:
        fcells.append("<td></td>")
    foot = '<tr class="tfoot-row">' + "".join(fcells) + "</tr>"

    return ('<div class="table-wrap"><table><thead>' + "".join(ghead) + "".join(chead)
            + "</thead><tbody>" + "".join(body) + "</tbody><tfoot>" + foot
            + "</tfoot></table></div>")


def _row_attrs(key: str, clickable: bool, selected: str | None) -> str:
    """data-key + selected-class attributes for a drillable tbody row."""
    if not clickable:
        return ""
    cls = ' class="selected"' if selected is not None and key == selected else ""
    return f' data-key="{escape(key)}"{cls}'


def pivot_table_html(roll, dim_col: str, dim_label: str,
                     clickable: bool = False, selected: str | None = None,
                     col_set: str = "TYPES", sparks: dict | None = None,
                     totals_override: dict | None = None) -> str:
    """3-type rollup table with per-row percentage bars and a TOTAL footer."""
    groups = _col_groups(col_set, three_stats=True)

    def name_cell(r):
        return f'<div>{escape(str(r[dim_col]))}</div>{_bar(r["wv"], r["nwv"], r["csv"])}'

    def row_attrs(r):
        val = str(r[dim_col])
        # the collapsed OTHER row has no drill target
        return _row_attrs(val, clickable and not val.startswith("OTHER ("), selected)

    def spark_key(r):
        return str(r[dim_col])

    return _table_html(groups, dim_label, roll, name_cell, row_attrs, spark_key,
                       sparks, "TOTAL", "No rows match your filter.",
                       totals_override=totals_override)


def subject_table_html(subs, show_brand_sublabel: bool = False,
                       clickable: bool = False, selected: str | None = None,
                       col_set: str = "TYPES", sparks: dict | None = None) -> str:
    """Subject-level drill table (QTY/VALUE per type, no subject counts)."""
    groups = _col_groups(col_set, three_stats=False)

    def name_cell(r):
        name = escape(str(r["subject_name"]))
        if show_brand_sublabel:
            name = f'<div>{name}</div><div class="sub-label">{escape(str(r["brand"]))}</div>'
        return name

    def row_attrs(r):
        # data-key mirrors the drill_subj option string ("BRAND — SUBJECT")
        return _row_attrs(f'{r["brand"]} — {r["subject_name"]}', clickable, selected)

    def spark_key(r):
        return str(r["subj_key"])

    return _table_html(groups, "SUBJECT", subs, name_cell, row_attrs, spark_key,
                       sparks, "TOTAL (filtered)", "No subjects found.")


def item_table_html(items) -> str:
    """Item × status grain detail table (one row per item and status)."""
    headers = ["ITEM #", "DESCRIPTION", "TEAM", "FORM TYPE", "USED STATUS",
               "STATUS", "PROGRAM", "AGE", "QTY", "UNIT COST", "VALUATION"]
    ncols = len(headers)
    head = "<tr>" + "".join(
        f'<th class="th-total">{h}</th>' if h == "VALUATION" else f"<th>{h}</th>"
        for h in headers
    ) + "</tr>"
    rows = []
    for _, r in items.iterrows():
        age = r["age_days"]
        age_txt = "—" if pd.isna(age) else f"{int(age)}d"
        scode = str(r["status"])
        slabel = STATUS_LABELS.get(scode, scode)
        desc = r["item_description"]
        desc_txt = "—" if pd.isna(desc) or not str(desc) else str(desc)
        rows.append(
            "<tr>"
            f'<td>{escape(str(r["item_number"]))}</td>'
            f'<td class="td-desc" title="{escape(desc_txt)}">{escape(desc_txt)}</td>'
            f'<td style="text-align:left">{escape(str(r["team"]) or "—")}</td>'
            f'<td style="text-align:left">{escape(str(r["relic_form_type"]) or "—")}</td>'
            f'<td style="text-align:left">{escape(str(r["item_used_status"]) or "—")}</td>'
            f'<td style="text-align:left"><span class="st st-{escape(scode)}">{slabel}</span></td>'
            f'<td style="text-align:left">{escape(str(r["program"]))}</td>'
            f"<td>{age_txt}</td>"
            f'<td>{fmtq(r["qty_onhand"])}</td>'
            f'<td>${r["unit_cost"]:,.2f}</td>'
            f'<td class="td-total">{fmt(r["valuation"])}</td></tr>'
        )
    tq = items["qty_onhand"].sum()
    tv = items["valuation"].sum()
    foot = (
        f'<tr class="tfoot-row"><td colspan="{ncols - 3}">TOTAL (filtered)</td>'
        f'<td>{fmtq(tq)}</td><td></td><td class="td-total">{fmt(tv)}</td></tr>'
    )
    body = "".join(rows) or (
        f'<tr><td colspan="{ncols}" style="text-align:center;color:var(--muted);'
        'padding:30px">No items found.</td></tr>'
    )
    return ('<div class="table-wrap"><table><thead>' + head
            + "</thead><tbody>" + body + "</tbody><tfoot>" + foot + "</tfoot></table></div>")
