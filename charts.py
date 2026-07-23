"""Altair charts for the TRENDS and AGING tabs (multi-month history views)."""
from __future__ import annotations

import altair as alt
import pandas as pd

WHOLE = "#4d7ab8"
NONWHOLE = "#16a34a"
CUTSIG = "#dc2626"
ACCENT = "#4d7ab8"
GOLD = "#b5822a"

_LINE_PALETTE = [
    "#4d7ab8", "#16a34a", "#dc2626", "#b5822a", "#7c3aed", "#0891b2",
    "#be185d", "#4b5563", "#ca8a04", "#059669", "#9333ea", "#e11d48",
    "#2563eb", "#65a30d", "#c2410c", "#0f766e", "#a21caf", "#78716c",
    "#1d4ed8", "#15803d",
]
OTHER_GRAY = "#9ca3af"


def _cat_colors(cat_order):
    """Stable palette per category; the folded OTHER series renders gray."""
    return [OTHER_GRAY if str(c).startswith("OTHER (") else _LINE_PALETTE[i % len(_LINE_PALETTE)]
            for i, c in enumerate(cat_order)]


def value_mix_chart(labels, mix_rows):
    """mix_rows: list of dicts {snapshot, type, value} (long form)."""
    df = pd.DataFrame(mix_rows)
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("snapshot:N", title=None, sort=labels),
            y=alt.Y("value:Q", title="Value ($)", stack="zero", axis=alt.Axis(format="$,.0s")),
            color=alt.Color(
                "type:N",
                title="Type",
                scale=alt.Scale(
                    domain=["WHOLE", "NON-WHOLE", "CUT SIG"],
                    range=[WHOLE, NONWHOLE, CUTSIG],
                ),
            ),
            order=alt.Order("type:N"),
        )
        .properties(height=260)
    )


def mom_heatmap(df, month_order, cat_order, metric_label, domain_cap):
    """Month-grid heatmap of month-over-month change.

    df: [cat, mlabel, value, delta, pct]. Cell color = ABSOLUTE MoM Δ on a
    diverging scale (green building / red shrinking), clamped symmetric at
    ``domain_cap`` (95th percentile of |Δ|) so one outlier month doesn't wash
    out the rest of the map. Row height is per-step so a 3-row STATUS map and a
    20-row SUBJECT map both size correctly.
    """
    return (
        alt.Chart(df)
        .mark_rect(stroke="#ffffff", strokeWidth=1)
        .encode(
            x=alt.X("mlabel:O", sort=month_order, title=None,
                    axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("cat:N", sort=cat_order, title=None),
            color=alt.Color(
                "delta:Q", title=f"MoM Δ {metric_label}",
                scale=alt.Scale(scheme="redyellowgreen", domainMid=0,
                                domain=[-domain_cap, domain_cap], clamp=True),
            ),
            tooltip=[
                alt.Tooltip("cat:N", title="Category"),
                alt.Tooltip("mlabel:O", title="Month"),
                alt.Tooltip("value:Q", title="Level", format=",.0f"),
                alt.Tooltip("delta:Q", title="MoM Δ", format="+,.0f"),
                alt.Tooltip("pct:Q", title="MoM Δ%", format="+.1f"),
            ],
        )
        .properties(height=alt.Step(26))
    )


def trend_lines_chart(df, month_order, cat_order, axis_title, is_value=True):
    """Top-N categories (+ gray OTHER) as lines across every month.

    df: [cat, mlabel, value].
    """
    fmt_axis = "$,.0s" if is_value else ",.0s"
    base = alt.Chart(df).encode(
        x=alt.X("mlabel:O", sort=month_order, title=None,
                axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("value:Q", title=axis_title, axis=alt.Axis(format=fmt_axis)),
        color=alt.Color("cat:N", title=None, sort=cat_order,
                        scale=alt.Scale(domain=cat_order, range=_cat_colors(cat_order))),
        detail="cat:N",
        tooltip=[
            alt.Tooltip("cat:N", title="Category"),
            alt.Tooltip("mlabel:O", title="Month"),
            alt.Tooltip("value:Q", title="Value", format=",.0f"),
        ],
    )
    return (base.mark_line(strokeWidth=2, opacity=0.9)
            + base.mark_point(size=40, filled=True)).properties(height=320)


def age_profile_chart(df, bucket_order):
    """Stacked value by age bucket and status. df: [bucket, status, value]."""
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("bucket:N", sort=bucket_order, title=None),
            y=alt.Y("value:Q", title="Value ($)", stack="zero",
                    axis=alt.Axis(format="$,.0s")),
            color=alt.Color(
                "status:N", title="Status",
                scale=alt.Scale(domain=["UNSLATED", "SLATED", "OBSOLETE"],
                                range=[WHOLE, NONWHOLE, CUTSIG]),
            ),
            tooltip=[
                alt.Tooltip("bucket:N", title="Age"),
                alt.Tooltip("status:N", title="Status"),
                alt.Tooltip("value:Q", title="Value", format="$,.0f"),
            ],
        )
        .properties(height=220)
    )
