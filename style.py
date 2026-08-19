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

import os
from datetime import datetime, timezone
from html import escape

import pandas as pd
import streamlit as st

from transforms import (EXT_COLS, ROLLUP_COLS, SORT_AVG_AGE, SORT_PCT12,
                        SORT_TREND, STATUS_LABELS, fmt, fmt2, fmtq)

# ---------------------------------------------------------------- label casing
# Brand values are stored in Oracle in ALL CAPS and are case-folded to a single
# canonical UPPER form in queries.py, so the only place they can be made readable
# is at render time. This is DISPLAY-ONLY: drill data-keys, filter values, the
# session-state selections and the Excel export all keep the canonical value, so
# nothing that compares or round-trips a brand is affected by what you see here.
#
# str.title() is unusable for this data -- it would render MLB as "Mlb", UFC as
# "Ufc" and NCAA as "Ncaa". Tokens are therefore checked against an acronym
# allowlist first, and any token that already carries lowercase is left untouched
# so hand-cased names ("SpongeBob") survive a round trip unchanged.
_ACRONYMS = {
    "MLB", "MLS", "NFL", "NBA", "WNBA", "NHL", "NCAA", "UFC", "WWE", "AEW",
    "UEFA", "EPL", "NASCAR", "PGA", "LPGA", "ATP", "WTA", "MMA", "XFL", "CFL",
    "AFL", "NRL", "IPL", "NPB", "KBO", "OTE", "NIL", "F1", "USA", "AAA", "II",
    "III", "IV", "TV", "&", "-",
    # short internal brand codes that are not words
    "CHP", "DIS", "GPK", "MCD", "MRV", "STW", "TEN", "TWD", "VFR",
}

# Names whose correct casing is not derivable by rule.
_CASE_OVERRIDES = {"MCDONALDS": "McDonalds", "SPONGEBOB": "SpongeBob"}


def _cap_word(w: str) -> str:
    """Capitalise a single word, respecting internal hyphens and apostrophes.

    Splits on ' and - so "O'NEAL" becomes "O'Neal" and "ALL-STAR" becomes
    "All-Star". A one-letter tail after an apostrophe stays lowercase, which is
    what keeps possessives readable ("MEN'S" -> "Men's", not "Men'S").
    """
    out = w.lower()
    for sep in ("'", "-"):
        parts = out.split(sep)
        out = sep.join(p if i and len(p) == 1 else p[:1].upper() + p[1:]
                       for i, p in enumerate(parts))
    return out


def title_case(value) -> str:
    """Render an ALL-CAPS stored label as readable title case.

    Idempotent and safe on already-cased input, so it can be applied at any
    render site without having to know whether the value came from Oracle raw or
    from a previously formatted string.
    """
    s = str(value)
    words = []
    for tok in s.split():
        core = tok.strip(".,()[]/")
        pad = (tok[:len(tok) - len(tok.lstrip(".,()[]/"))],
               tok[len(tok.rstrip(".,()[]/")):])
        key = core.upper()
        if key in _CASE_OVERRIDES:
            core = _CASE_OVERRIDES[key]
        elif key in _ACRONYMS or not core:
            core = core.upper() if core else core
        elif any(c.islower() for c in core):
            pass                      # already hand-cased -- leave it alone
        else:
            core = _cap_word(core)
        words.append(pad[0] + core + pad[1])
    return " ".join(words)


# Frame columns whose values are rendered through title_case(). Brand is the only
# approved one today; adding "team" or "relic_form_type" here is all it would take
# to extend the same treatment, since every render site consults this set.
_TITLECASE_COLS = {"brand"}

# Freshness tooltips read best in the reader's own wall-clock time. The zone is
# PINNED rather than taken from the host clock: the SiS container runs in UTC, so
# a host-derived zone would silently differ between local dev and deployed. Set
# DASHBOARD_TZ to any IANA name to override.
#
# zoneinfo is stdlib, but the tz DATABASE it reads is not guaranteed to exist in a
# minimal container image, so a missing/unknown zone degrades to a UTC-only
# tooltip rather than taking the header down. `tzdata` is pinned in pyproject.toml
# to make the named-zone branch reliable on the SiS container runtime.
try:
    from zoneinfo import ZoneInfo

    _LOCAL_TZ = ZoneInfo(os.getenv("DASHBOARD_TZ") or "America/New_York")
except Exception:  # any lookup failure means "no local zone" -> UTC-only tooltip
    _LOCAL_TZ = None

# ONE font stack for every surface. The dashboard renders across four independent
# surfaces — the .fct chrome, the shadow-DOM tables (TABLE_CSS), Streamlit's own
# widgets/markdown/dataframes (theme font), and Altair chart text — and each used
# to resolve its own family, which is what made the UI look like several fonts.
#
# The FALLBACK CHAIN matters as much as the family: Streamlit-in-Snowflake runs
# under a Content Security Policy that restricts external resources, so the Inter
# webfont requested by .streamlit/config.toml may not load in the deployed app.
# Every surface therefore shares this exact stack, so if Inter is unavailable they
# all degrade to the SAME system font instead of diverging.
#
# charts.py imports FONT for the Altair config; do not fork this list.
FONT = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif"

# Shared base (fonts + palette vars): needed in the page stylesheet for the
# chrome AND inside each table component, because page styles can't pierce
# the component's shadow root.
_BASE_CSS = """
.fct, .fct * { box-sizing: border-box; font-family: __FONT__; }
.fct {
  --surface:#fff; --surface2:#f8f9fc; --border:#e2e8f0; --accent:#4d7ab8;
  --whole:#4d7ab8; --nonwhole:#16a34a; --cutsig:#dc2626; --text:#1a2b4a;
  --muted:#6b7fa3; --gold:#b5822a;
}
""".replace("__FONT__", FONT)

_CHROME_CSS = """
/* HEADER */
.fct .hdr { background:#1a2b4a; border-radius:10px; padding:14px 22px; margin-bottom:18px;
  box-shadow:0 2px 8px rgba(0,0,0,.25); }
.fct .hdr-top { display:flex; align-items:center; gap:18px; flex-wrap:wrap; }
/* App title lockup. The 1px tracking here is deliberate branding on the title
   itself; the subtitle is distinguished by size and colour, not weight. */
.fct .logo { font-size:24px; font-weight:400; letter-spacing:1px; color:#fff; line-height:1; }
.fct .logo span { color:#7a99c0; font-size:15px; font-weight:400; letter-spacing:.2px; }
/* wrap + row-gap so the 4th field (DATA PULLED) drops to a second line on a
   narrow viewport instead of overflowing the header card. */
.fct .hdr-meta { margin-left:auto; display:flex; gap:26px; row-gap:10px; flex-wrap:wrap;
  font-size:10px; font-weight:400; letter-spacing:.6px; color:#7a99c0; }
.fct .hdr-meta b { color:#fff; font-size:14px; font-weight:400; letter-spacing:0;
  display:block; margin-top:2px; }
/* DATA PULLED carries a tooltip with the absolute timestamp; the dotted underline
   advertises that there is something to hover. Keep the inherited display:block so
   the value stacks under its label like every other field — width:fit-content is
   what makes the rule hug the text instead of spanning the whole column. */
.fct .hdr-meta .fresh { cursor:help; }
.fct .hdr-meta .fresh b { width:fit-content; border-bottom:1px dotted #4d6a91; }

/* CONTEXT BAR */
.fct .context-bar { display:flex; align-items:center; gap:10px; margin:0 0 14px; }
.fct .context-status, .fct .context-view {
  font-size:11px; font-weight:400; letter-spacing:.3px;
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
/* TYPOGRAPHIC SCALE FOR LABELS.
   Labels are NORMAL weight (400). Successive passes at 700 and then 600 both read
   as uniformly heavy, because nearly every string on this page is a label of some
   kind — eyebrows, chips, section rules, column headers — so any weight they all
   share becomes the page's baseline and stops signalling anything.

   Bold (700) is therefore reserved for numbers that matter: stat-card figures, the
   gold TOTAL column, table footers, status chips. Weight carries exactly one
   meaning — 400 = a label, 700 = a value — and labels are separated from each
   other by SIZE and COLOUR alone:

     .panel-title  15px  — drill panel heading (the entity you drilled into)
     .sec-title    12px  — section heading with the trailing rule
     thead th      11px  — table column header
     .lbl          10px  — eyebrow label on cards / header meta

   The one exception is td:first-child (500) — a row label needs to sit slightly
   above the numeric cells beside it without becoming a second bold tier.

   All sentence case; nothing is uppercase. */
.fct .stat-card .lbl { font-size:10px; font-weight:400;
  letter-spacing:.6px; color:var(--muted); margin-bottom:6px; }
.fct .stat-card .val { font-size:26px; font-weight:700; line-height:1.1; letter-spacing:-.5px;
  font-variant-numeric:tabular-nums; }
.fct .stat-card .sub { font-size:11px; color:var(--muted); margin-top:3px; font-variant-numeric:tabular-nums; }

/* LEGEND */
.fct .legend { display:flex; gap:20px; flex-wrap:wrap; margin-bottom:14px; }
.fct .legend-item { display:flex; align-items:center; gap:8px; font-size:12px;
  color:var(--muted); }
.fct .legend-dot { width:10px; height:10px; border-radius:2px; }

/* TREND TITLES */
.fct .sec-title { font-size:12px; font-weight:400; letter-spacing:.3px;
  color:var(--muted); margin:8px 0 12px; display:flex; align-items:center; gap:10px; }
.fct .sec-title::after { content:''; flex:1; height:1px; background:var(--border); }

/* Drill panel heading. Replaces a Streamlit markdown "####", which was styled by
   Streamlit's own heading font/weight (600) and was the single biggest outlier
   among the heading surfaces. */
.fct .panel-title { font-size:15px; font-weight:400; letter-spacing:.2px;
  color:var(--text); margin:14px 0 10px; }
.fct .panel-title .panel-sub { font-size:12px; font-weight:400; color:var(--muted);
  letter-spacing:0; margin-left:10px; }

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
.fct thead th { background:#1a2b4a; padding:8px 10px; text-align:center; font-size:11px;
  font-weight:400; letter-spacing:.3px; color:#fff; white-space:nowrap; }
.fct thead th:first-child { text-align:left; background:#000; }
/* Group band header (◆ Whole / ◆ Non-whole / ◆ Cut sig). Deliberately matched to
   the row-label cell (td:first-child, 13px/500 with normal tracking) so the two
   things the eye uses to orient itself in the table -- the band it is under and
   the row it is on -- are set identically. The extra top padding lifts the band
   off the top edge of the table; it is the first row, so it has no neighbour
   above to give it breathing room. */
.fct .cg th { font-size:13px; font-weight:500; padding:16px 10px 7px;
  text-align:center; color:#fff; }
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
.fct td { padding:9px 10px; text-align:center; font-size:12px; font-variant-numeric:tabular-nums;
  border-bottom:1px solid var(--border); color:var(--text); white-space:nowrap; }
.fct td:first-child { text-align:left; font-size:13px; font-weight:500;
  overflow:hidden; text-overflow:ellipsis; }

/* UNIFORM DATA-COLUMN WIDTH.
   Every data column is the same width with its content centered in header AND
   cell (the centering is on the shared th/td rules above; the leading name
   column opts back out to left, since it is the row label).

   table-layout:fixed is what makes the widths EXACT. Under the default `auto`
   layout a column always grows to fit its content, so a `width` is only a hint
   and columns drift apart by content — which is the inconsistency being fixed.
   Fixed layout applies ONLY to .t-grid (pivot / subject / program); the item
   table needs content-based sizing for its long DESCRIPTION and stays on auto.

   The widths live on <colgroup><col> rather than th/td because under fixed
   layout the browser derives column widths from the FIRST ROW, and that row here
   is the grouped colour-band header full of colspans, which would compute
   unpredictably. Explicit <col> elements are authoritative and colspan-proof.

   col-data is sized for the widest value a cell must hold (money like
   "$1,284,300") rather than the widest column as currently rendered, because a
   uniform width multiplies across up to 15 columns and widens the whole grid. */
.fct .t-grid { table-layout:fixed; }
.fct .t-grid col.col-name { width:260px; }
.fct .t-grid col.col-data { width:104px; }

/* Item table: auto layout so text columns still size to content, but its four
   numeric columns share one width and every header centers like .t-grid. */
.fct .t-item { table-layout:auto; }
.fct .t-item th.c-num, .fct .t-item td.c-num { width:104px; min-width:104px; text-align:center; }
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

/* CLICK-TO-SORT HEADERS: only cells carrying data-sort are interactive, and
   data-sort is emitted exclusively inside <thead> so header clicks can never be
   confused with the tbody row clicks that drive drill-down. The sort itself runs
   in pandas (see transforms.sort_frame) — these are just the affordances. */
.fct thead th[data-sort] { cursor:pointer; user-select:none; }
.fct thead th[data-sort]:hover { background:#2b4470; }
.fct thead th:first-child[data-sort]:hover { background:#222; }
.fct thead th.th-total[data-sort]:hover { background:#c9923a; }
.fct .sort-arw { font-size:9px; margin-left:3px; opacity:.95; }

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
[data-testid="stSidebar"][aria-expanded="true"] { min-width: 340px; max-width: 420px; }
"""

# Force Streamlit's OWN chrome (widgets, markdown, metrics, dataframes) onto the
# same stack as .fct and the charts. The theme in .streamlit/config.toml already
# asks for Inter, but it resolves its own fallbacks — so if the Inter webfont is
# blocked by the Streamlit-in-Snowflake CSP the widgets would land on a different
# system font than the tables. Restating the full stack here keeps all four
# surfaces identical in both the loaded and the blocked case.
_APP_FONT_CSS = """
[data-testid="stAppViewContainer"], [data-testid="stSidebar"],
[data-testid="stAppViewContainer"] button, [data-testid="stSidebar"] button,
[data-testid="stAppViewContainer"] input, [data-testid="stAppViewContainer"] select,
[data-testid="stAppViewContainer"] textarea {
  font-family: __FONT__;
}
""".replace("__FONT__", FONT)

CSS = "<style>" + _BASE_CSS + _CHROME_CSS + _SIDEBAR_CSS + _APP_FONT_CSS + "</style>"


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


def _rel_age(secs: float) -> str:
    """Coarse human age for a data-pull stamp ("12 min ago").

    Deliberately low-resolution: the point is answering "is this stale?" at a
    glance, not stopwatch precision. Rounds to the unit shown, and never says
    "0 min ago" — anything under 45s reads as "just now".
    """
    if secs < 45:
        return "just now"
    mins = secs / 60
    if mins < 60:
        return f"{int(round(mins)) or 1} min ago"
    hrs, rem_min = divmod(int(round(mins)), 60)
    if hrs < 24:
        return f"{hrs} hr ago" if rem_min == 0 else f"{hrs} hr {rem_min} min ago"
    days = int(round(hrs / 24))
    return "1 day ago" if days == 1 else f"{days} days ago"


def freshness_label(pulled_at: datetime, now: datetime | None = None) -> tuple[str, str]:
    """``(relative, absolute)`` strings for a UTC data-pull instant.

    The absolute form always carries an explicit zone, and appends UTC whenever a
    local zone is available, so a screenshot of the tooltip is never ambiguous.
    ``now`` is injectable for tests.
    """
    now = now or datetime.now(timezone.utc)
    # A stamp very slightly in the future (clock skew between the app host and
    # wherever `now` came from) must not render as a negative age.
    rel = _rel_age(max(0.0, (now - pulled_at).total_seconds()))
    utc_txt = pulled_at.strftime("%b %d, %Y %H:%M UTC")
    if _LOCAL_TZ is None:
        return rel, utc_txt
    local = pulled_at.astimezone(_LOCAL_TZ)
    # Build 12-hour time by hand: the %-I / %#I no-pad flags are platform-specific.
    hour12 = local.strftime("%I").lstrip("0") or "12"
    stamp = f"{local.strftime('%b %d, %Y')} {hour12}:{local.strftime('%M %p')}"
    return rel, f"{stamp} {local.tzname()} ({utc_txt})"


def header_html(cards: dict, as_of_label: str, pulled_at: datetime | None = None) -> str:
    # DATA PULLED sits next to AS OF so the two time facts read together, but they
    # answer different questions: AS OF is the monthly valuation PERIOD (the source
    # has no finer grain), while DATA PULLED is when this app last hit Snowflake.
    fresh = ""
    if pulled_at is not None:
        rel, abs_txt = freshness_label(pulled_at)
        fresh = (f'<div class="fresh" title="Last query against Snowflake: '
                 f'{escape(abs_txt)}">Data pulled<b>{escape(rel)}</b></div>')
    return (
        '<div class="hdr"><div class="hdr-top">'
        '<div class="logo">FCT Relic <span>- Inventory Dashboard</span></div>'
        '<div class="hdr-meta">'
        f'<div>As of<b>{escape(as_of_label)}</b></div>'
        f'{fresh}'
        f'<div>Brands<b>{fmtq(cards["brands"])}</b></div>'
        f'<div>Total value<b>{fmt2(cards["total_val"])}</b></div>'
        "</div></div></div>"
    )


def context_bar_html(status: str, view_label: str) -> str:
    labels = {"A": "All inventory", "U": "Unslated", "S": "Slated", "O": "Obsolete"}
    scls = "" if status == "A" else f" status-{status}"
    return (
        '<div class="context-bar">'
        f'<span class="context-status{scls}">{labels.get(status, "All inventory")}</span>'
        '<span class="context-sep">·</span>'
        f'<span class="context-view">{escape(view_label)}</span>'
        "</div>"
    )


def panel_title_html(title: str, sub: str | None = None) -> str:
    """Drill panel heading + optional muted sub-text (counts).

    Used instead of a Streamlit markdown "####" so the heading is styled by our
    own scale rather than Streamlit's heading font, which made it the biggest
    outlier among the heading surfaces.
    """
    subtxt = f'<span class="panel-sub">{escape(sub)}</span>' if sub else ""
    return f'<div class="panel-title">{escape(title)}{subtxt}</div>'


def legend_html() -> str:
    return (
        '<div class="legend">'
        '<div class="legend-item"><div class="legend-dot" style="background:var(--whole)"></div>Whole (MEM prefix)</div>'
        '<div class="legend-item"><div class="legend-dot" style="background:var(--nonwhole)"></div>Non-whole (other)</div>'
        '<div class="legend-item"><div class="legend-dot" style="background:var(--cutsig)"></div>Cut sig (type)</div>'
        "</div>"
    )


def stat_cards_html(c: dict) -> str:
    return (
        '<div class="stat-cards">'
        '<div class="stat-card total"><div class="lbl">Total inventory value</div>'
        f'<div class="val" style="color:var(--gold)">{fmt2(c["total_val"])}</div>'
        f'<div class="sub">{fmtq(c["brands"])} brands &nbsp;·&nbsp; {fmtq(c["total_subj"])} subjects</div></div>'
        '<div class="stat-card"><div class="lbl">◆ Whole value</div>'
        f'<div class="val" style="color:var(--whole)">{fmt2(c["whole_val"])}</div>'
        f'<div class="sub">{fmtq(c["whole_items"])} distinct items &nbsp;·&nbsp; {fmtq(c["whole_subj"])} subjects</div></div>'
        '<div class="stat-card nonwhole"><div class="lbl">◆ Non-whole value</div>'
        f'<div class="val" style="color:var(--nonwhole)">{fmt2(c["nonwhole_val"])}</div>'
        f'<div class="sub">{fmtq(c["nonwhole_items"])} distinct items &nbsp;·&nbsp; {fmtq(c["nonwhole_subj"])} subjects</div></div>'
        '<div class="stat-card cutsig"><div class="lbl">◆ Cut sig value</div>'
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
        '<div class="stat-card total"><div class="lbl">Value &gt;1yr</div>'
        f'<div class="val" style="color:var(--gold)">{fmt(a["total_12"])}</div>'
        f'<div class="sub">{share:.0f}% of filtered value</div></div>'
        '<div class="stat-card"><div class="lbl">Value-weighted avg age</div>'
        f'<div class="val" style="color:var(--accent)">{avg}</div>'
        '<div class="sub">age is a lower bound (see note)</div></div>'
        '<div class="stat-card cutsig"><div class="lbl">Unslated value &gt;1yr</div>'
        f'<div class="val" style="color:var(--cutsig)">{pct}</div>'
        '<div class="sub">share of unslated value</div></div>'
        '<div class="stat-card nonwhole"><div class="lbl">Items &gt;1yr</div>'
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
# A column group is (group_label, header_css, [(col_header, cell_fn, td_css,
# sort_key)]). cell_fn(d) formats a cell from either a row Series or the footer
# totals dict, so footer ratios (avg age, %>1yr) derive from the summed additive
# columns. sort_key names the FRAME field (or derived ratio) the column sorts by
# — never the formatted text; None makes the column unsortable.
_SUMMABLE = set(ROLLUP_COLS + EXT_COLS)


def _fmt_avg_age(d) -> str:
    av = float(d["av"] or 0)
    return f'{float(d["agev"]) / av:.0f} days' if av > 0 else "—"


def _fmt_pct12(d) -> str:
    tv = float(d["tv"] or 0)
    return f'{float(d["v12"]) / tv * 100:.0f}%' if tv > 0 else "—"


def _type_groups(three_stats: bool):
    def cols(p, cls):
        out = [("Qty", lambda d, k=f"{p}q": fmtq(d[k]), cls, f"{p}q")]
        if three_stats:
            out.append(("Subj", lambda d, k=f"{p}s": fmtq(d[k]), cls, f"{p}s"))
        out.append(("Value", lambda d, k=f"{p}v": fmt(d[k]), cls, f"{p}v"))
        return out

    return [
        ("◆ Whole", "th-whole", cols("w", "td-whole")),
        ("◆ Non-whole", "th-nonwhole", cols("nw", "td-nonwhole")),
        ("◆ Cut sig", "th-cutsig", cols("cs", "td-cutsig")),
    ]


_TOTAL_GROUP = ("", "th-total", [("Total value", lambda d: fmt(d["tv"]), "td-total", "tv")])
_STATUS_GROUP = ("◆ Status", "th-status", [
    ("Slated", lambda d: fmt(d["slv"]), "td-slated", "slv"),
    ("Unslated", lambda d: fmt(d["uslv"]), "td-unslated", "uslv"),
])
_AGE_GROUP = ("◆ Age", "th-age", [
    ("Avg age", _fmt_avg_age, "td-age", SORT_AVG_AGE),
    ("%>1yr value", _fmt_pct12, "td-age", SORT_PCT12),
])


def _th(header: str, css: str, sort_key: str | None, sort) -> str:
    """One <th>. Adds data-sort + an active-direction arrow when sortable."""
    cls = f' class="{css}"' if css else ""
    if not sort_key:
        return f"<th{cls}>{escape(header)}</th>"
    arrow = ""
    if sort and sort[0] == sort_key:
        glyph = "▼" if sort[1] == "desc" else "▲"
        arrow = f'<span class="sort-arw">{glyph}</span>'
    return (f'<th{cls} data-sort="{escape(sort_key)}"'
            f' title="Sort by {escape(header)}">{escape(header)}{arrow}</th>')


def _col_groups(col_set: str, three_stats: bool):
    """Column groups for a col_set. "TYPES" is the legacy 11-column layout
    (kept for the nested program table); STANDARD adds SLATED/UNSLATED;
    +AGE adds the age ratios; STATUS + AGE drops the type groups entirely.

    TOTAL VALUE is appended LAST in every layout. It is the grand total of the
    row, so it reads as the figure the other columns build up to; parking it
    mid-table (before STATUS) made it look like a subtotal of the type groups
    only. Every consumer -- band row, header row, body, footer -- iterates these
    groups in order, so position is decided here and nowhere else.
    """
    if col_set == "STATUS + AGE":
        return [_STATUS_GROUP, _AGE_GROUP, _TOTAL_GROUP]
    groups = _type_groups(three_stats)
    if col_set in ("STANDARD", "+AGE"):
        groups = groups + [_STATUS_GROUP]
    if col_set == "+AGE":
        groups = groups + [_AGE_GROUP]
    return groups + [_TOTAL_GROUP]


def _table_html(groups, first_header, frame, name_cell, row_attrs, spark_key,
                sparks, foot_label, empty_msg,
                totals_override: dict | None = None,
                sort=None, first_sort: str | None = None) -> str:
    """Shared builder for the pivot + subject drill tables.

    ``sort`` is the active ``(key, direction)`` — used only to draw the arrow;
    the row order must already have been applied by transforms.sort_frame.
    ``first_sort`` is the sort key for the leading name column.
    """
    has_trend = sparks is not None
    ncols = 1 + sum(len(g[2]) for g in groups) + (1 if has_trend else 0)
    # Authoritative widths for table-layout:fixed — see the .t-grid CSS for why
    # these live on <col> and not on the header cells.
    colgroup = ('<colgroup><col class="col-name">'
                + '<col class="col-data">' * (ncols - 1) + "</colgroup>")
    ghead = ['<tr class="cg"><th></th>']
    # Only this second header row is clickable: the .cg band row spans groups.
    chead = ["<tr>" + _th(first_header, "", first_sort, sort)]
    for glabel, gcss, cols in groups:
        ghead.append(f'<th colspan="{len(cols)}" class="{gcss}">{glabel}</th>')
        for h, _fn, _cls, skey in cols:
            chead.append(_th(h, gcss, skey, sort))
    if has_trend:
        ghead.append('<th class="th-trend"></th>')
        chead.append(_th("Trend · Δ", "th-trend", SORT_TREND, sort))
    ghead.append("</tr>")
    chead.append("</tr>")

    body = []
    for _, r in frame.iterrows():
        cells = [f"<td>{name_cell(r)}</td>"]
        for _g, _css, cols in groups:
            for _h, fn, cls, _sk in cols:
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
        for _h, fn, cls, _sk in cols:
            fcells.append(f'<td class="{cls}">{fn(totals)}</td>')
    if has_trend:
        fcells.append("<td></td>")
    foot = '<tr class="tfoot-row">' + "".join(fcells) + "</tr>"

    return ('<div class="table-wrap"><table class="t-grid">' + colgroup
            + "<thead>" + "".join(ghead) + "".join(chead)
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
                     totals_override: dict | None = None,
                     sort=None) -> str:
    """3-type rollup table with per-row percentage bars and a TOTAL footer."""
    groups = _col_groups(col_set, three_stats=True)

    def name_cell(r):
        # display-only casing; row_attrs below still keys off the raw value
        shown = (title_case(r[dim_col]) if dim_col in _TITLECASE_COLS
                 else str(r[dim_col]))
        return f'<div>{escape(shown)}</div>{_bar(r["wv"], r["nwv"], r["csv"])}'

    def row_attrs(r):
        val = str(r[dim_col])
        # the collapsed OTHER row has no drill target
        return _row_attrs(val, clickable and not val.startswith("OTHER ("), selected)

    def spark_key(r):
        return str(r[dim_col])

    return _table_html(groups, dim_label, roll, name_cell, row_attrs, spark_key,
                       sparks, "Total", "No rows match your filter.",
                       totals_override=totals_override,
                       sort=sort, first_sort=dim_col)


def subject_table_html(subs, show_brand_sublabel: bool = False,
                       clickable: bool = False, selected: str | None = None,
                       col_set: str = "TYPES", sparks: dict | None = None,
                       sort=None) -> str:
    """Subject-level drill table (QTY/VALUE per type, no subject counts)."""
    groups = _col_groups(col_set, three_stats=False)

    def name_cell(r):
        name = escape(str(r["subject_name"]))
        if show_brand_sublabel:
            brand = escape(title_case(r["brand"]))
            name = f'<div>{name}</div><div class="sub-label">{brand}</div>'
        return name

    def row_attrs(r):
        # data-key mirrors the drill_subj option string ("BRAND — SUBJECT")
        return _row_attrs(f'{r["brand"]} — {r["subject_name"]}', clickable, selected)

    def spark_key(r):
        return str(r["subj_key"])

    return _table_html(groups, "Subject", subs, name_cell, row_attrs, spark_key,
                       sparks, "Total (filtered)", "No subjects found.",
                       sort=sort, first_sort="subject_name")


# (header, frame field) for the item detail table. The field is what the column
# sorts by, so every column here is sortable on its real value — AGE sorts by
# age_days (not "120d") and VALUATION by the float (not "$1,234").
_ITEM_COLS = [
    ("Item #", "item_number"),
    ("Description", "item_description"),
    ("Team", "team"),
    ("Type", "relic_form_type"),
    ("Used status", "item_used_status"),
    ("Status", "status"),
    ("Sub-inv", "subinventory_code"),
    ("Bin", "bin_location"),
    ("Program", "program"),
    ("Age", "age_days"),
    ("Qty", "qty_onhand"),
    ("Unit cost", "unit_cost"),
    ("Valuation", "valuation"),
]
# The numeric columns: these share one uniform width and center their cells. Text
# columns keep content-based width (DESCRIPTION in particular must not be clipped
# — see .td-desc) but their HEADERS still center, like every other table.
_ITEM_NUM_FIELDS = {"age_days", "qty_onhand", "unit_cost", "valuation"}


def item_table_html(items, sort=None) -> str:
    """Item × status × sub-inventory × bin grain detail table (one row per bin)."""
    ncols = len(_ITEM_COLS)
    head = "<tr>" + "".join(
        _th(h, " ".join(c for c in (
                "th-total" if h == "Valuation" else "",
                "c-num" if field in _ITEM_NUM_FIELDS else "") if c),
            field, sort)
        for h, field in _ITEM_COLS
    ) + "</tr>"
    rows = []
    for _, r in items.iterrows():
        age = r["age_days"]
        age_txt = "—" if pd.isna(age) else f"{int(age)}d"
        scode = str(r["status"])
        slabel = STATUS_LABELS.get(scode, scode)
        desc = r["item_description"]
        desc_txt = "—" if pd.isna(desc) or not str(desc) else str(desc)
        subinv = r["subinventory_code"]
        subinv_txt = "—" if pd.isna(subinv) or not str(subinv).strip() else str(subinv)
        binloc = r["bin_location"]
        bin_txt = "—" if pd.isna(binloc) or not str(binloc).strip() else str(binloc)
        rows.append(
            "<tr>"
            f'<td>{escape(str(r["item_number"]))}</td>'
            f'<td class="td-desc" title="{escape(desc_txt)}">{escape(desc_txt)}</td>'
            f'<td style="text-align:left">{escape(str(r["team"]) or "—")}</td>'
            f'<td style="text-align:left">{escape(str(r["relic_form_type"]) or "—")}</td>'
            f'<td style="text-align:left">{escape(str(r["item_used_status"]) or "—")}</td>'
            f'<td style="text-align:left"><span class="st st-{escape(scode)}">{slabel}</span></td>'
            f'<td style="text-align:left">{escape(subinv_txt)}</td>'
            f'<td class="td-desc" title="{escape(bin_txt)}">{escape(bin_txt)}</td>'
            f'<td style="text-align:left">{escape(str(r["program"]))}</td>'
            f'<td class="c-num">{age_txt}</td>'
            f'<td class="c-num">{fmtq(r["qty_onhand"])}</td>'
            f'<td class="c-num">${r["unit_cost"]:,.2f}</td>'
            f'<td class="c-num td-total">{fmt(r["valuation"])}</td></tr>'
        )
    tq = items["qty_onhand"].sum()
    tv = items["valuation"].sum()
    foot = (
        f'<tr class="tfoot-row"><td colspan="{ncols - 3}">Total (filtered)</td>'
        f'<td class="c-num">{fmtq(tq)}</td><td class="c-num"></td>'
        f'<td class="c-num td-total">{fmt(tv)}</td></tr>'
    )
    body = "".join(rows) or (
        f'<tr><td colspan="{ncols}" style="text-align:center;color:var(--muted);'
        'padding:30px">No items found.</td></tr>'
    )
    return ('<div class="table-wrap"><table class="t-item"><thead>' + head
            + "</thead><tbody>" + body + "</tbody><tfoot>" + foot + "</tfoot></table></div>")
