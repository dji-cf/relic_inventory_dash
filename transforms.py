"""Pure-pandas aggregation / drill-down logic mirroring the source HTML dashboard.

Every figure is split across three item types — WHOLE / NON-WHOLE / CUT SIG —
each with QTY, distinct-SUBJECT count, and VALUE, plus a TOTAL VALUE. Rollups
additionally carry ADDITIVE slated/unslated and aging columns (EXT_COLS) so the
OTHER row and table footers stay exact under any fold; ratio figures (avg age,
% of value 12+) are derived from those sums at render time, never stored.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# itype value -> column prefix
TYPES = {"WHOLE": "w", "NON-WHOLE": "nw", "CUT SIG": "cs"}
ROLLUP_COLS = ["wq", "ws", "wv", "nwq", "nws", "nwv", "csq", "css", "csv", "tv"]
# Additive extension columns (sums only — see module docstring):
#   slv / uslv : slated / unslated value (obsolete counts in neither)
#   agev / av  : Σ(valuation × age_days) and Σ(valuation); the known-age masking
#                is a no-op now that every retained item has an age basis, but it
#                keeps avg age = agev/av honest if a NULL ever slips through
#   v12        : valuation in the >1yr age buckets (over 1yr + over 2yr)
EXT_COLS = ["slv", "uslv", "agev", "av", "v12"]
FT_OTHER_THRESHOLD = 25_000
# Placeholder queries.onhand_sql substitutes for a blank ITEM_NUMBER / SUBJECT_NAME
# / BRAND. It is not a real member, so headline distinct counts exclude it.
OTHER = "OTHER"

STATUS_LABELS = {"U": "UNSLATED", "S": "SLATED", "O": "OBSOLETE"}

# ── item aging (shared data-layer + transforms contract) ──────────────────────
# Left-closed DAY buckets matching FCT_INVENTORY_AGING (RECEIPT_DATE-based).
# See queries.ITEM_AGE_CTE for the swappable age basis. Two window effects to
# keep in mind when reading an age:
#   * the aging window opens ~2025-05-30, so an item received earlier reports the
#     window-open date — every age is a LOWER BOUND, and "over 2yr" therefore
#     stays empty until 2027-05-30 rather than collecting pre-window inventory;
#   * an item received later in the as-of month than its first-of-month TXN_DATE
#     stamp clamps to age 0 and lands in "0-90" (see queries.onhand_sql).
AGE_EDGES = [0, 91, 181, 366, 731, np.inf]
AGE_LABELS = ["0-90", "91-180", "181-365", "over 1yr", "over 2yr"]


def age_bucket(days: pd.Series) -> pd.Series:
    """Bucket item age (in days) into ``AGE_LABELS`` (left-closed bins).

    Edges match FCT_INVENTORY_AGING: 0-90, 91-180, 181-365, over 1yr, over 2yr.
    ``age_days`` is non-NULL by construction (queries.ITEM_AGE_CTE is INNER
    JOINed and the age is clamped at 0), so the ``fillna`` below is a pure
    defense: were a NULL ever to appear it folds into the oldest bucket, keeping
    BY-AGE rollups reconciled to the portfolio total rather than dropping value.
    """
    s = pd.to_numeric(days, errors="coerce")
    cut = pd.cut(s, bins=AGE_EDGES, labels=AGE_LABELS, right=False)
    return cut.fillna(AGE_LABELS[-1])


# ── formatting ──────────────────────────────────────────────────────────────
def fmt(n) -> str:
    if n is None or pd.isna(n):
        return "—"
    return "$" + f"{round(float(n)):,}"


def fmt2(n) -> str:
    """Dollars WITH cents — headline stat cards only, so they tie to the
    authoritative valuation snapshot exactly. Tables keep ``fmt``."""
    if n is None or pd.isna(n):
        return "—"
    return "$" + f"{float(n):,.2f}"


def fmtq(n) -> str:
    if n is None or pd.isna(n):
        return "—"
    return f"{round(float(n)):,}"


# ── filtering ───────────────────────────────────────────────────────────────
def apply_filters(df, status="A", team="", formtype="", usedstatus="", brand="",
                  subinventory=""):
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
    if subinventory:
        out = out[out["subinventory_code"] == subinventory]
    return out


# ── column sorting (click-to-sort headers) ───────────────────────────────────
# Sorting runs HERE, on the real frame values — never on the rendered cell text.
# The tables display formatted strings ("$1,234", "120d", "45%", "—"), so sorting
# those in the browser would order text and silently mis-rank numbers.
#
# A sort key is either a frame column name or one of these derived ratios, which
# are computed from the additive EXT_COLS rather than stored (see the module
# docstring). Ratio denominators of 0 yield NaN, which always sorts last.
SORT_AVG_AGE = "@avg_age"    # agev / av
SORT_PCT12 = "@pct12"        # v12 / tv
SORT_TREND = "@trend"        # sparkline % change (needs the sparks dict)

_OTHER_PREFIX = "OTHER ("


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    """num/den as float with 0 (and NaN) denominators becoming NaN."""
    d = pd.to_numeric(den, errors="coerce").astype("float64")
    n = pd.to_numeric(num, errors="coerce").astype("float64")
    return n / d.where(d != 0)


def sort_series(df, key, *, sparks=None, spark_key=None):
    """Resolve a sort key to the Series the frame should be ordered by.

    Returns None when the key can't be resolved against this frame — callers
    treat that as "sort no longer applies" and fall back to the default order.
    """
    if key == SORT_AVG_AGE:
        if {"agev", "av"} <= set(df.columns):
            return _ratio(df["agev"], df["av"])
        return None
    if key == SORT_PCT12:
        if {"v12", "tv"} <= set(df.columns):
            return _ratio(df["v12"], df["tv"])
        return None
    if key == SORT_TREND:
        # sparks maps a row's spark key -> (values, pct); pct is None for "NEW".
        if not sparks or spark_key is None:
            return None
        pct = [(sparks.get(spark_key(r)) or (None, None))[1] for _, r in df.iterrows()]
        return pd.Series(pct, index=df.index, dtype="float64")
    if key in df.columns:
        return df[key]
    return None


def default_descending(s: pd.Series) -> bool:
    """True when a column should sort largest-first on its FIRST click.

    Numbers read best biggest-first (a $ or QTY column), text reads best A-Z.
    Categorical dtypes hold strings here (see data.load_history), so they count
    as text even though pandas may store integer codes. DATES read best newest
    first, which is the same instinct as numbers.
    """
    if isinstance(s.dtype, pd.CategoricalDtype):
        return False
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    return bool(pd.api.types.is_numeric_dtype(s))


def sort_frame(df, spec, *, sparks=None, spark_key=None, pin_other_col=None):
    """Order ``df`` by ``spec`` = ``(key, "asc"|"desc")``; no-op when spec is None.

    * text sorts case-insensitively, so "adidas" and "Adidas" interleave sanely
    * NaN / missing always sorts LAST, in both directions — an em-dash row is
      never the "top" result
    * stable, so rows tied on the sorted column keep the frame's incoming order
      (which is the default value ranking)
    * ``pin_other_col``: name of the dim column whose collapsed "OTHER (n …)"
      bucket must stay pinned at the bottom. It is an aggregate of folded rows,
      not a real member, so letting it sort among real values would mislead.
    """
    if df.empty or not spec:
        return df
    key, direction = spec
    s = sort_series(df, key, sparks=sparks, spark_key=spark_key)
    if s is None:
        return df
    asc = direction != "desc"
    # datetimes are already ordered — stringifying them would work only by ISO
    # accident, so they skip the text branch and sort natively
    if not pd.api.types.is_datetime64_any_dtype(s) and (
            isinstance(s.dtype, pd.CategoricalDtype)
            or not pd.api.types.is_numeric_dtype(s)):
        # str(NaN) == "nan", which would sort among real values — mask the blanks
        # back to NaN (taken from the resolved series, not the frame) so
        # na_position still sinks them in BOTH directions.
        na = s.isna()
        s = s.astype(str).str.lower().mask(na)
    order = pd.DataFrame({"_s": s.to_numpy()}, index=df.index)
    by = ["_s"]
    ascending = [asc]
    if pin_other_col and pin_other_col in df.columns:
        order["_o"] = (df[pin_other_col].astype(str)
                       .str.startswith(_OTHER_PREFIX).to_numpy())
        by = ["_o"] + by
        ascending = [True] + ascending
    order = order.sort_values(by, ascending=ascending, kind="stable",
                              na_position="last")
    return df.loc[order.index]


# ── rollups ─────────────────────────────────────────────────────────────────
def _typed_value_cols(df):
    g = df.copy()
    for t, p in TYPES.items():
        m = g["itype"] == t
        g[f"{p}q"] = np.where(m, g["qty_onhand"], 0.0)
        g[f"{p}v"] = np.where(m, g["valuation"], 0.0)
    g["slv"] = np.where(g["status"] == "S", g["valuation"], 0.0)
    g["uslv"] = np.where(g["status"] == "U", g["valuation"], 0.0)
    if "age_days" in g.columns:
        # age_days is Int64-nullable: mask first, multiply on a float copy. The
        # mask is all-True in practice (see the age_bucket contract); it stays so
        # a stray NULL can never poison the value-weighted average.
        age = pd.to_numeric(g["age_days"], errors="coerce").astype("float64")
        known = age.notna().to_numpy()
        g["agev"] = np.where(known, g["valuation"] * age.fillna(0.0), 0.0)
        g["av"] = np.where(known, g["valuation"], 0.0)
        # v12 = valuation in the >1yr buckets (over 1yr + over 2yr)
        bucket_str = g["age_bucket"].astype(str)
        g["v12"] = np.where(
            (bucket_str == AGE_LABELS[-1]) | (bucket_str == AGE_LABELS[-2]),
            g["valuation"], 0.0)
    else:  # frames without aging (e.g. a history month) still roll up cleanly
        g["agev"] = 0.0
        g["av"] = 0.0
        g["v12"] = 0.0
    return g


def _subject_counts(g, dim):
    """Distinct subjects per type whose summed qty within the group is > 0.

    Keyed on ``subj_key`` (brand + subject), so these are BRAND-SCOPED counts: a
    subject stocked under two brands counts once per brand. That is the right
    grain for a table row (it matches the subjects the row drills into) and is
    identical to a plain subject count in the BY BRAND / SPORT view. It is NOT
    the headline definition — ``stat_cards`` counts distinct ``subject_name`` so
    the cards tie to the authoritative Sigma figures. Expect the sum of a
    table's subject column to exceed the card's total by the number of
    cross-brand subjects.
    """
    counts = {}
    for t, p in TYPES.items():
        sub = g[g["itype"] == t]
        if sub.empty:
            counts[f"{p}s"] = pd.Series(dtype=int)
            continue
        per = (sub.groupby([dim, "subj_key"], dropna=False, observed=False)
               ["qty_onhand"].sum().reset_index())
        per = per[per["qty_onhand"] > 0]
        counts[f"{p}s"] = per.groupby(dim, observed=False)["subj_key"].nunique()
    return counts


def subject_count_totals(df) -> dict:
    """Distinct subjects per type over the whole frame — ``_subject_counts``
    without the ``dim`` grouping. Used to override a pivot table's TOTAL footer,
    where summing the per-row distinct counts would double-count any subject
    that appears under more than one dim value."""
    out = {}
    for t, p in TYPES.items():
        sub = df[df["itype"] == t]
        if sub.empty:
            out[f"{p}s"] = 0
            continue
        per = sub.groupby("subj_key", dropna=False, observed=False)["qty_onhand"].sum()
        out[f"{p}s"] = int((per > 0).sum())
    return out


def rollup(df, dim) -> pd.DataFrame:
    """Group ``df`` by ``dim`` into the standard 3-type rollup table."""
    empty = pd.DataFrame(columns=[dim] + ROLLUP_COLS + EXT_COLS)
    if df.empty:
        return empty
    g = _typed_value_cols(df)
    base = (
        g.groupby(dim, dropna=False, observed=False)
        .agg(
            wq=("wq", "sum"), wv=("wv", "sum"),
            nwq=("nwq", "sum"), nwv=("nwv", "sum"),
            csq=("csq", "sum"), csv=("csv", "sum"),
            slv=("slv", "sum"), uslv=("uslv", "sum"),
            agev=("agev", "sum"), av=("av", "sum"), v12=("v12", "sum"),
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
    return base[[dim] + ROLLUP_COLS + EXT_COLS]


def collapse_other(roll, dim, threshold=FT_OTHER_THRESHOLD):
    """Fold rows below ``threshold`` total value into a single OTHER row (Type view).

    Value / qty / EXT_COLS are additive so the OTHER row is exact. The subject
    columns (ws / nws / css) are distinct counts and are only summed here — a
    subject present in two folded types is counted twice in the OTHER row.
    """
    if roll.empty:
        return roll
    main = roll[roll["tv"] >= threshold]
    other = roll[roll["tv"] < threshold]
    if other.empty:
        return roll
    # every non-dim column is additive, so a plain sum keeps the OTHER row exact
    agg = {c: other[c].sum() for c in roll.columns if c != dim}
    agg[dim] = f"OTHER ({len(other)} types)"
    return pd.concat([main, pd.DataFrame([agg])], ignore_index=True)


def subject_rollup(sub) -> pd.DataFrame:
    """One row per (brand, subject) with the 3-type split. Used in drill panels.

    Rows are keyed on ``subj_key`` because a subject can appear under multiple
    brands and the drill panel must address exactly one of them. See
    ``_subject_counts`` for why this differs from the ``stat_cards`` definition.
    """
    cols = ["brand", "subject_name", "subj_key"] + ROLLUP_COLS + EXT_COLS
    if sub.empty:
        return pd.DataFrame(columns=cols)
    g = _typed_value_cols(sub)
    base = (
        g.groupby(["brand", "subject_name", "subj_key"], dropna=False, observed=False)
        .agg(
            wq=("wq", "sum"), wv=("wv", "sum"),
            nwq=("nwq", "sum"), nwv=("nwv", "sum"),
            csq=("csq", "sum"), csv=("csv", "sum"),
            slv=("slv", "sum"), uslv=("uslv", "sum"),
            agev=("agev", "sum"), av=("av", "sum"), v12=("v12", "sum"),
        )
        .reset_index()
    )
    base["ws"] = (base["wq"] > 0).astype(int)
    base["nws"] = (base["nwq"] > 0).astype(int)
    base["css"] = (base["csq"] > 0).astype(int)
    base["tv"] = base["wv"] + base["nwv"] + base["csv"]
    return base.sort_values("tv", ascending=False).reset_index(drop=True)[cols]


def _join_programs(s: pd.Series) -> str:
    vals = sorted(str(v) for v in s.dropna().unique())
    return " / ".join(vals) if vals else "—"


def items_for(df, brand, subject) -> pd.DataFrame:
    """Item × status × sub-inventory × bin grain detail for a brand+subject.

    One row per (item, status, sub-inventory, bin/locator): an item held in more
    than one bin — or part-slated across sub-inventories — shows each location as
    its own row so a picklist export states exactly where each unit sits and how
    much is available vs. committed. Rows for one item stay adjacent (sorted by
    the item's total value, then item, then status, sub-inventory, bin).
    """
    g = df[(df["brand"] == brand) & (df["subject_name"] == subject)]
    cols = ["item_number", "item_description", "team", "relic_form_type",
            "item_used_status", "status", "subinventory_code", "bin_location",
            "program", "age_days", "qty_onhand", "unit_cost", "valuation"]
    if g.empty:
        return pd.DataFrame(columns=cols)
    out = (
        g.groupby(["item_number", "status", "subinventory_code", "bin_location"],
                  dropna=False, observed=False)
        .agg(
            item_description=("item_description", "first"),
            team=("team", "first"),
            relic_form_type=("relic_form_type", "first"),
            item_used_status=("item_used_status", "first"),
            program=("program", _join_programs),
            age_days=("age_days", "first"),
            qty_onhand=("qty_onhand", "sum"),
            valuation=("valuation", "sum"),
        )
        .reset_index()
    )
    out["unit_cost"] = np.where(out["qty_onhand"] != 0,
                                out["valuation"] / out["qty_onhand"], 0.0)
    item_tv = out.groupby("item_number")["valuation"].transform("sum")
    out = (out.assign(_itv=item_tv)
           .sort_values(["_itv", "item_number", "status", "subinventory_code",
                         "bin_location"],
                        ascending=[False, True, True, True, True])
           .drop(columns="_itv")
           .reset_index(drop=True))
    return out[cols]


RECEIPT_COLS = ["receipt_month", "item_number", "item_description",
                "relic_form_type", "item_used_status", "qty_received",
                "qty_onhand", "total_value", "is_onhand"]


def receipts_for(df, brand, subject, onhand_only: bool = False) -> pd.DataFrame:
    """Receipt-month grain procurement receipts for one brand+subject.

    One row per (item, receipt month): an item received in three different months
    shows three rows, because that is what a receipt list is for. Sorted newest
    receipt first, then item, so the most recent arrivals lead.

    ``onhand_only`` narrows to items still in stock. The unfiltered frame is the
    wider "ever received" universe and therefore includes items the on-hand tabs
    cannot show at all -- that gap is the whole reason this view exists.

    ``qty_onhand`` and ``total_value`` are the item's CURRENT figures repeated on
    each of its rows (see queries.receipts_sql); neither is additive down the
    column.
    """
    g = df[(df["brand"] == brand) & (df["subject_name"] == subject)]
    if onhand_only:
        g = g[g["is_onhand"]]
    if g.empty:
        return pd.DataFrame(columns=RECEIPT_COLS)
    return (g.sort_values(["receipt_month", "item_number"],
                          ascending=[False, True])
            .reset_index(drop=True)[RECEIPT_COLS])


def program_breakdown(df, brand, subject) -> pd.DataFrame:
    """Program-level split for a slated brand+subject (LOCATOR-derived program)."""
    g = df[(df["brand"] == brand) & (df["subject_name"] == subject)
           & (df["status"] == "S")].copy()
    if g.empty:
        return pd.DataFrame(columns=["program"] + ROLLUP_COLS + EXT_COLS)
    g["program"] = g["program"].fillna("NO PROGRAM")
    return rollup(g, "program")


# ── stat cards & totals ───────────────────────────────────────────────────────
def stat_cards(df) -> dict:
    """Headline card figures.

    Counts are deliberately NOT keyed on ``subj_key``: a subject appearing under
    two brands is ONE subject here, matching the authoritative Sigma cards.
    ``OTHER`` is the blank-value placeholder from queries.onhand_sql (blank
    BRAND / SUBJECT_NAME), not a real brand or subject, so it is excluded from
    both counts — its valuation still counts in full.
    """
    def vq(t):
        sub = df[df["itype"] == t]
        return float(sub["valuation"].sum()), float(sub["qty_onhand"].sum())

    def items(t):
        return int(df.loc[df["itype"] == t, "item_number"].nunique())

    def subj(t):
        sub = df[df["itype"] == t]
        per = sub.groupby("subject_name", observed=True)["qty_onhand"].sum()
        return set(per[per > 0].index) - {OTHER}

    wv, wq = vq("WHOLE")
    nwv, nwq = vq("NON-WHOLE")
    csv, csq = vq("CUT SIG")
    ws, nws, css = subj("WHOLE"), subj("NON-WHOLE"), subj("CUT SIG")
    brands = df.loc[df["brand"] != OTHER, "brand"].nunique()
    return {
        "total_val": wv + nwv + csv,
        "whole_val": wv, "nonwhole_val": nwv, "cutsig_val": csv,
        "whole_qty": wq, "nonwhole_qty": nwq, "cutsig_qty": csq,
        "whole_items": items("WHOLE"), "nonwhole_items": items("NON-WHOLE"),
        "cutsig_items": items("CUT SIG"),
        "brands": int(brands),
        "total_subj": len(ws | nws | css),
        "whole_subj": len(ws), "nonwhole_subj": len(nws), "cutsig_subj": len(css),
    }


def totals_row(roll) -> dict:
    return {c: roll[c].sum() for c in ROLLUP_COLS + EXT_COLS if c in roll.columns}


# ── multi-month history (TRENDS tab + INVENTORY sparklines) ───────────────────
def history_long(hist, dim, metric, months) -> pd.DataFrame:
    """Long frame ``[dim, month, value]`` with the month × category grid completed.

    ``metric`` is ``valuation`` or ``qty_onhand``. A category/month pair with no
    on-hand rows is a TRUE ZERO (the history query's HAVING drops zero bins), so
    the grid is completed with 0 — never forward-filled or dropped.
    """
    if hist.empty:
        return pd.DataFrame(columns=[dim, "month", "value"])
    wide = (hist.groupby([dim, "month"], dropna=False, observed=True)[metric]
            .sum().unstack("month", fill_value=0.0)
            .reindex(columns=list(months), fill_value=0.0))
    long = wide.stack().rename("value").reset_index()
    long.columns = [dim, "month", "value"]
    long[dim] = long[dim].astype(str)
    long["month"] = long["month"].astype(str)
    return long


def top_n_other(long_df, dim, n, rank_month):
    """Keep the top-``n`` categories by value at ``rank_month`` (the anchor);
    fold the rest into one ``OTHER (k)`` series summed per month.

    Returns ``(long_df, ordered_categories)`` with OTHER last.
    """
    if long_df.empty:
        return long_df, []
    rank = (long_df[long_df["month"] == rank_month]
            .groupby(dim, observed=True)["value"].sum()
            .sort_values(ascending=False))
    top = [str(c) for c in rank.head(n).index]
    is_top = long_df[dim].isin(top)
    main = long_df[is_top].copy()
    rest = long_df[~is_top]
    k = rest[dim].nunique()
    if k:
        label = f"OTHER ({k})"
        other = rest.groupby("month", as_index=False, observed=True)["value"].sum()
        other[dim] = label
        main = pd.concat([main, other[[dim, "month", "value"]]], ignore_index=True)
        return main, top + [label]
    return main, top


def mom_change(long_df, dim) -> pd.DataFrame:
    """Add ``delta`` / ``pct`` (vs. prior month) per category; each category's
    first month is dropped (no month-over-month basis)."""
    if long_df.empty:
        return long_df.assign(delta=pd.Series(dtype=float), pct=pd.Series(dtype=float))
    out = long_df.sort_values([dim, "month"], kind="stable").copy()
    prev = out.groupby(dim, observed=True)["value"].shift(1)
    out["delta"] = out["value"] - prev
    out["pct"] = np.where(prev.abs() > 0, out["delta"] / prev * 100.0, np.nan)
    return out.dropna(subset=["delta"]).reset_index(drop=True)


def trailing_deltas(long_df, dim, months, anchor, lags=(1, 3, 6)) -> pd.DataFrame:
    """Per category: value at ``anchor`` plus Δ / Δ% vs. each trailing lag.

    Lags are POSITIONAL steps into the sorted month spine (not date arithmetic);
    a lag reaching before the window start yields NaN (rendered as an em dash).
    Sorted by current value descending.
    """
    cols = [dim, "current"] + [f"d{k}" for k in lags] + [f"p{k}" for k in lags]
    months = list(months)
    if long_df.empty or anchor not in months:
        return pd.DataFrame(columns=cols)
    wide = (long_df.pivot_table(index=dim, columns="month", values="value",
                                aggfunc="sum", observed=True)
            .reindex(columns=months, fill_value=0.0).fillna(0.0))
    idx = months.index(anchor)
    out = pd.DataFrame({dim: wide.index.astype(str),
                        "current": wide[anchor].to_numpy(dtype=float)})
    for k in lags:
        j = idx - k
        if j >= 0:
            base = wide[months[j]].to_numpy(dtype=float)
            out[f"d{k}"] = out["current"] - base
            out[f"p{k}"] = np.where(np.abs(base) > 0,
                                    (out["current"] - base) / base * 100.0, np.nan)
        else:
            out[f"d{k}"] = np.nan
            out[f"p{k}"] = np.nan
    return out.sort_values("current", ascending=False).reset_index(drop=True)[cols]


def sparks_for(hist_flt, key_col, months) -> dict:
    """Per key value: ``(value series over months, window % change)``.

    The series covers exactly ``months`` (pass months ≤ the as-of date so the
    last point equals the displayed TOTAL). All-zero series are omitted (their
    cells render empty). pct is ``(last − first_nonzero) / first_nonzero``, or
    ``None`` when the first nonzero IS the last point (rendered as "NEW").
    """
    if hist_flt.empty or key_col not in hist_flt.columns:
        return {}
    wide = (hist_flt.groupby([key_col, "month"], dropna=False, observed=True)
            ["valuation"].sum().unstack("month", fill_value=0.0)
            .reindex(columns=list(months), fill_value=0.0))
    out = {}
    for key, row in wide.iterrows():
        vals = [float(v) for v in row.to_list()]
        nz = [(i, v) for i, v in enumerate(vals) if v > 0]
        if not nz:
            continue
        first_i, first_v = nz[0]
        pct = None if first_i == len(vals) - 1 else (vals[-1] - first_v) / first_v * 100.0
        out[str(key)] = (vals, pct)
    return out


# ── aging summary & stale report ──────────────────────────────────────────────
def aging_stats(df) -> dict:
    """Headline aging figures over the FCT_INVENTORY_AGING on-hand universe.

    Every row has a real age basis now, so avg_age spans the full portfolio and
    total_12 / items_12 no longer sweep in unknown-age value. The NULL-age
    guards below are retained defensively (see the age_bucket contract)."""
    total_val = float(df["valuation"].sum()) if len(df) else 0.0
    if df.empty:
        return {"total_val": 0.0, "total_12": 0.0, "avg_age": None,
                "pct_unslated_12": None, "items_12": 0}
    # >1yr = over 1yr + over 2yr
    bucket_str = df["age_bucket"].astype(str)
    in12 = (bucket_str == AGE_LABELS[-1]) | (bucket_str == AGE_LABELS[-2])
    total_12 = float(df.loc[in12, "valuation"].sum())
    age = pd.to_numeric(df["age_days"], errors="coerce").astype("float64")
    known = age.notna()
    av = float(df.loc[known, "valuation"].sum())
    agev = float((df.loc[known, "valuation"] * age[known]).sum())
    avg_age = (agev / av) if av > 0 else None
    uns = df["status"] == "U"
    uns_val = float(df.loc[uns, "valuation"].sum())
    uns_12 = float(df.loc[uns & in12, "valuation"].sum())
    pct_unslated_12 = (uns_12 / uns_val * 100.0) if uns_val > 0 else None
    items_12 = int(df.loc[in12, "item_number"].nunique())
    return {"total_val": total_val, "total_12": total_12, "avg_age": avg_age,
            "pct_unslated_12": pct_unslated_12, "items_12": items_12}


def stale_items(df, min_age, statuses) -> pd.DataFrame:
    """Item × status grain disposition-candidate report, valuation descending.

    ``min_age`` is in DAYS. ``statuses`` is a set of status codes. Ages are lower
    bounds (the aging window opens ~2025-05-30), so a long-held item can sit just
    under the threshold. The ``age.isna()`` term is defensive only — age is
    non-NULL by construction (see the age_bucket contract) — and previously swept
    every ageless row into the report regardless of threshold.
    """
    age = pd.to_numeric(df["age_days"], errors="coerce")
    g = df[(age.isna() | (age >= min_age))]
    if statuses:
        g = g[g["status"].isin(statuses)]
    cols = ["item_number", "brand", "subject_name", "team", "relic_form_type",
            "item_used_status", "status", "program", "age_days",
            "qty_onhand", "valuation"]
    if g.empty:
        return pd.DataFrame(columns=cols)
    out = (
        g.groupby(["item_number", "status"], dropna=False, observed=False)
        .agg(
            brand=("brand", "first"),
            subject_name=("subject_name", "first"),
            team=("team", "first"),
            relic_form_type=("relic_form_type", "first"),
            item_used_status=("item_used_status", "first"),
            program=("program", _join_programs),
            age_days=("age_days", "first"),
            qty_onhand=("qty_onhand", "sum"),
            valuation=("valuation", "sum"),
        )
        .reset_index()
    )
    return out.sort_values("valuation", ascending=False).reset_index(drop=True)[cols]
