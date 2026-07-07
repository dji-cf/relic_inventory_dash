"""Pure-pandas aggregation / drill-down logic mirroring the source HTML dashboard.

Every figure is split across three item types — WHOLE / NON-WHOLE / CUT SIG —
each with QTY, distinct-SUBJECT count, and VALUE, plus a TOTAL VALUE.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# itype value -> column prefix
TYPES = {"WHOLE": "w", "NON-WHOLE": "nw", "CUT SIG": "cs"}
ROLLUP_COLS = ["wq", "ws", "wv", "nwq", "nws", "nwv", "csq", "css", "csv", "tv"]
FT_OTHER_THRESHOLD = 25_000


# ── formatting ──────────────────────────────────────────────────────────────
def fmt(n) -> str:
    if n is None or pd.isna(n):
        return "—"
    return "$" + f"{round(float(n)):,}"


def fmtq(n) -> str:
    if n is None or pd.isna(n):
        return "—"
    return f"{round(float(n)):,}"


# ── filtering ───────────────────────────────────────────────────────────────
def apply_filters(df, status="A", team="", formtype="", usedstatus="", brand=""):
    out = df
    if status != "A":
        out = out[out["status"] == status]
    if team:
        out = out[out["team"] == team]
    if formtype:
        out = out[out["relic_form_type"] == formtype]
    if usedstatus:
        out = out[out["item_used_status"] == usedstatus]
    if brand:
        out = out[out["brand"] == brand]
    return out


# ── rollups ─────────────────────────────────────────────────────────────────
def _typed_value_cols(df):
    g = df.copy()
    for t, p in TYPES.items():
        m = g["itype"] == t
        g[f"{p}q"] = np.where(m, g["qty_onhand"], 0.0)
        g[f"{p}v"] = np.where(m, g["valuation"], 0.0)
    return g


def _subject_counts(g, dim):
    """Distinct subjects per type whose summed qty within the group is > 0."""
    counts = {}
    for t, p in TYPES.items():
        sub = g[g["itype"] == t]
        if sub.empty:
            counts[f"{p}s"] = pd.Series(dtype=int)
            continue
        per = sub.groupby([dim, "subj_key"], dropna=False)["qty_onhand"].sum().reset_index()
        per = per[per["qty_onhand"] > 0]
        counts[f"{p}s"] = per.groupby(dim)["subj_key"].nunique()
    return counts


def rollup(df, dim) -> pd.DataFrame:
    """Group ``df`` by ``dim`` into the standard 3-type rollup table."""
    empty = pd.DataFrame(columns=[dim] + ROLLUP_COLS)
    if df.empty:
        return empty
    g = _typed_value_cols(df)
    base = (
        g.groupby(dim, dropna=False)
        .agg(
            wq=("wq", "sum"), wv=("wv", "sum"),
            nwq=("nwq", "sum"), nwv=("nwv", "sum"),
            csq=("csq", "sum"), csv=("csv", "sum"),
        )
    )
    counts = _subject_counts(g, dim)
    base["ws"] = counts["ws"]
    base["nws"] = counts["nws"]
    base["css"] = counts["css"]
    base[["ws", "nws", "css"]] = base[["ws", "nws", "css"]].fillna(0).astype(int)
    base = base.reset_index()
    base["tv"] = base["wv"] + base["nwv"] + base["csv"]
    base = base.sort_values("tv", ascending=False).reset_index(drop=True)
    return base[[dim] + ROLLUP_COLS]


def collapse_other(roll, dim, threshold=FT_OTHER_THRESHOLD):
    """Fold rows below ``threshold`` total value into a single OTHER row (Form Type view)."""
    if roll.empty:
        return roll
    main = roll[roll["tv"] >= threshold]
    other = roll[roll["tv"] < threshold]
    if other.empty:
        return roll
    agg = {c: other[c].sum() for c in ROLLUP_COLS}
    agg[dim] = f"OTHER ({len(other)} form types)"
    return pd.concat([main, pd.DataFrame([agg])], ignore_index=True)


def subject_rollup(sub) -> pd.DataFrame:
    """One row per (brand, subject) with the 3-type split. Used in drill panels."""
    cols = ["brand", "subject_name", "subj_key"] + ROLLUP_COLS
    if sub.empty:
        return pd.DataFrame(columns=cols)
    g = _typed_value_cols(sub)
    base = (
        g.groupby(["brand", "subject_name", "subj_key"], dropna=False)
        .agg(
            wq=("wq", "sum"), wv=("wv", "sum"),
            nwq=("nwq", "sum"), nwv=("nwv", "sum"),
            csq=("csq", "sum"), csv=("csv", "sum"),
        )
        .reset_index()
    )
    base["ws"] = (base["wq"] > 0).astype(int)
    base["nws"] = (base["nwq"] > 0).astype(int)
    base["css"] = (base["csq"] > 0).astype(int)
    base["tv"] = base["wv"] + base["nwv"] + base["csv"]
    return base.sort_values("tv", ascending=False).reset_index(drop=True)[cols]


def items_for(df, brand, subject) -> pd.DataFrame:
    """Item-grain detail for a brand+subject (collapses subinventory bins)."""
    g = df[(df["brand"] == brand) & (df["subject_name"] == subject)]
    cols = ["item_number", "team", "relic_form_type", "item_used_status",
            "qty_onhand", "unit_cost", "valuation"]
    if g.empty:
        return pd.DataFrame(columns=cols)
    out = (
        g.groupby("item_number", dropna=False)
        .agg(
            team=("team", "first"),
            relic_form_type=("relic_form_type", "first"),
            item_used_status=("item_used_status", "first"),
            qty_onhand=("qty_onhand", "sum"),
            valuation=("valuation", "sum"),
        )
        .reset_index()
    )
    out["unit_cost"] = np.where(out["qty_onhand"] != 0,
                                out["valuation"] / out["qty_onhand"], 0.0)
    return out.sort_values("valuation", ascending=False).reset_index(drop=True)[cols]


def program_breakdown(df, brand, subject) -> pd.DataFrame:
    """Program-level split for a slated brand+subject (LOCATOR-derived program)."""
    g = df[(df["brand"] == brand) & (df["subject_name"] == subject)
           & (df["status"] == "S")].copy()
    if g.empty:
        return pd.DataFrame(columns=["program"] + ROLLUP_COLS)
    g["program"] = g["program"].fillna("NO PROGRAM")
    return rollup(g, "program")


# ── stat cards & totals ───────────────────────────────────────────────────────
def stat_cards(df) -> dict:
    def vq(t):
        sub = df[df["itype"] == t]
        return float(sub["valuation"].sum()), float(sub["qty_onhand"].sum())

    def subj(t):
        sub = df[df["itype"] == t]
        per = sub.groupby("subj_key")["qty_onhand"].sum()
        return set(per[per > 0].index)

    wv, wq = vq("WHOLE")
    nwv, nwq = vq("NON-WHOLE")
    csv, csq = vq("CUT SIG")
    ws, nws, css = subj("WHOLE"), subj("NON-WHOLE"), subj("CUT SIG")
    return {
        "total_val": wv + nwv + csv,
        "whole_val": wv, "nonwhole_val": nwv, "cutsig_val": csv,
        "whole_qty": wq, "nonwhole_qty": nwq, "cutsig_qty": csq,
        "brands": int(df["brand"].nunique()),
        "total_subj": len(ws | nws | css),
        "whole_subj": len(ws), "nonwhole_subj": len(nws), "cutsig_subj": len(css),
    }


def totals_row(roll) -> dict:
    return {c: roll[c].sum() for c in ROLLUP_COLS}


# ── trends ────────────────────────────────────────────────────────────────────
def portfolio_totals(df) -> dict:
    return {
        "total": float(df["valuation"].sum()),
        "whole": float(df[df["itype"] == "WHOLE"]["valuation"].sum()),
        "nonwhole": float(df[df["itype"] == "NON-WHOLE"]["valuation"].sum()),
        "cutsig": float(df[df["itype"] == "CUT SIG"]["valuation"].sum()),
    }


def subject_movers(df_a, df_b):
    """(gainers, losers) DataFrames of subject-level total-value change A -> B."""
    a = df_a.groupby(["brand", "subject_name"])["valuation"].sum().rename("prev")
    b = df_b.groupby(["brand", "subject_name"])["valuation"].sum().rename("curr")
    m = pd.concat([a, b], axis=1).fillna(0.0).reset_index()
    m["delta"] = m["curr"] - m["prev"]
    m = m[m["delta"].abs() > 0].sort_values("delta", ascending=False)
    gainers = m.head(10).reset_index(drop=True)
    losers = m.tail(10).sort_values("delta").reset_index(drop=True)
    return gainers, losers


def brand_compare(df_a, df_b, top=10):
    a = df_a.groupby("brand")["valuation"].sum().rename("a")
    b = df_b.groupby("brand")["valuation"].sum().rename("b")
    m = pd.concat([a, b], axis=1).fillna(0.0)
    m["total"] = m["a"] + m["b"]
    return m.sort_values("total", ascending=False).head(top).drop(columns="total").reset_index()
