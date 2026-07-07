"""Altair charts for the Trends tab (snapshot-to-snapshot comparison)."""
from __future__ import annotations

import altair as alt
import pandas as pd

WHOLE = "#4d7ab8"
NONWHOLE = "#16a34a"
CUTSIG = "#dc2626"
ACCENT = "#4d7ab8"
GOLD = "#b5822a"


def total_value_chart(labels, totals):
    df = pd.DataFrame({"snapshot": labels, "value": totals})
    base = alt.Chart(df).encode(
        x=alt.X("snapshot:N", title=None, sort=labels),
        y=alt.Y("value:Q", title="Total Value ($)", axis=alt.Axis(format="$,.0s")),
    )
    return (base.mark_line(color=ACCENT, strokeWidth=2.5)
            + base.mark_point(color=ACCENT, size=70, filled=True)).properties(height=240)


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


def brand_compare_chart(brand_df, labels):
    """brand_df columns: brand, a, b. Renders top brands across the two snapshots."""
    long = brand_df.melt(id_vars="brand", value_vars=["a", "b"],
                         var_name="which", value_name="value")
    long["snapshot"] = long["which"].map({"a": labels[0], "b": labels[-1]})
    base = alt.Chart(long).encode(
        x=alt.X("snapshot:N", title=None, sort=[labels[0], labels[-1]]),
        y=alt.Y("value:Q", title="Total Value ($)", axis=alt.Axis(format="$,.0s")),
        color=alt.Color("brand:N", title="Brand"),
        detail="brand:N",
    )
    return (base.mark_line(strokeWidth=2, opacity=0.85)
            + base.mark_point(size=55, filled=True)).properties(height=300)
