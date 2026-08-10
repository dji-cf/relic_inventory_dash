"""SQL builders for the FCT Relic inventory dashboard.

Valuation is the AUTHORITATIVE monthly snapshot: ORACLE_DATA_PROD.ATHLETE_SPEND.
ATH_SPEND_RELIC_ITEM_INV_VAL_V carries one official ITEM_INV_VALU per item per
PERIOD_NAME (e.g. 'May-26'). That view is item x period grain only (no
subinventory / locator), so to keep the dashboard's per-subinventory (status) and
per-program breakdowns we ALLOCATE each item's ITEM_INV_VALU across its on-hand
bins in proportion to net quantity. The bin totals therefore reconcile to the
authoritative per-item value, and the portfolio total reconciles to the period's
TOTAL_INV_VALU.

On-hand QUANTITY per bin is still COMPUTED cumulatively from the transaction views
(procurement QTY>0 + consumption QTY<0) where TXN_DATE <= the as-of date, including
internal transfer rows so subinventory-bin balances reconcile. TXN_DATE is
first-of-month stamped and maps 1:1 to PERIOD_NAME. Source columns are TEXT, so
QTY is cast with TRY_TO_DECIMAL.
"""

import re

REC_VIEW = "ORACLE_DATA_PROD.ATHLETE_SPEND.ATH_SPEND_REC_RELIC_RAW_V"
CON_VIEW = "ORACLE_DATA_PROD.ATHLETE_SPEND.ATH_SPEND_CON_RELIC_RAW_V"
VAL_VIEW = "ORACLE_DATA_PROD.ATHLETE_SPEND.ATH_SPEND_RELIC_ITEM_INV_VAL_V"
AGING_VIEW = "ORACLE_DATA_PROD.ATHLETE_SPEND.FCT_INVENTORY_AGING"

_AS_OF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Distinct valuation periods from the authoritative snapshot, as first-of-month
# DATEs (PERIOD_NAME 'May-26' -> 2026-05-01). Returned in column 0 so
# data.available_months() keeps working unchanged.
MONTHS_SQL = f"""
SELECT DISTINCT TO_DATE('01-' || PERIOD_NAME, 'DD-MON-YY') AS as_of
FROM {VAL_VIEW}
WHERE PERIOD_NAME IS NOT NULL
ORDER BY as_of
"""

# One row per ITEM_NUMBER over EVERY item FCT_INVENTORY_AGING vouches for. Three
# jobs, all shared by onhand_sql and HISTORY_SQL — hence a parameterless constant
# rather than a per-date builder:
#
#   1. THE ITEM UNIVERSE. Both queries INNER JOIN this CTE, so an item absent from
#      FCT_INVENTORY_AGING is excluded from the ENTIRE dashboard. That view is the
#      authority on what is actually on hand; our qty is *computed* cumulatively
#      from the txn views and can disagree. The gate is per ITEM and
#      all-or-nothing, so an excluded item drops all of its bins together: the
#      allocation denominators (item_qty / item_qty_hist) stay exact for every
#      retained item and no value leaks into a sibling bin.
#      Membership only — deliberately NO per-month date test — so the snapshot and
#      the history query span the same universe in every month and
#      data.history_matches_snapshot keeps reconciling.
#
#   2. SINGLE POINT OF CHANGE for item aging. The basis is the earliest true
#      RECEIPT_DATE (VARCHAR MM/DD/YYYY, parsed with TRY_TO_DATE); STREET_DATE is
#      deliberately NOT used. The aging window opens ~2025-05-30, so an item
#      received before then reports the window-open date and its age is a LOWER
#      BOUND.
#
#   3. ITEM_DESCRIPTION, which only this view carries. It is 1:1 with ITEM_NUMBER,
#      so MAX just collapses the per-receipt rows.
#
# This CTE used to filter `WHERE receipt <= as_of`, which together with a LEFT JOIN
# stranded every item whose earliest receipt fell later in the as-of month than the
# first-of-month TXN_DATE stamp: age_date came back NULL and transforms.age_bucket
# folded it into the OLDEST bucket, so brand-new inventory (5,386 items / $6.3M at
# Jul-2026) was reported as "over 2yr / pre-window" — and its description was blank.
# The filter is gone; onhand_sql clamps age_days at 0 instead, landing those rows in
# 0-90. Output contract: (ITEM_NUMBER, age_date, item_description) — do not change.
ITEM_AGE_CTE = f"""
    item_age AS (
        SELECT ITEM_NUMBER,
               MIN(TRY_TO_DATE(RECEIPT_DATE, 'MM/DD/YYYY')) AS age_date,
               MAX(ITEM_DESCRIPTION)                        AS item_description
        FROM {AGING_VIEW}
        GROUP BY ITEM_NUMBER
    )"""


# Item + subinventory-bin grain on-hand snapshot as-of :as_of.
# Returns the classification columns the dashboard pivots on:
#   ITYPE  : WHOLE / NON-WHOLE / CUT SIG
#   STATUS : U (unslated) / S (slated) / O (obsolete)
#   PROGRAM: 3rd dot-segment of LOCATOR_NAME for slated rows (000 -> NO PROGRAM)
#
# VALUATION is the authoritative ITEM_INV_VALU for the period, allocated across
# each item's on-hand bins by qty share (bin_qty / item_qty). This reproduces the
# authoritative per-item value and portfolio total while preserving the bin grain
# the status / program views need.
#
# The as-of date is embedded as a DATE literal (not a qmark bind) so the SQL TEXT
# is unique per date. st.connection(...).query() caches results, and on Streamlit
# <=1.51 (the old SiS warehouse runtime) that cache key IGNORED `params` — a
# static SQL string + qmark binds returned the first-loaded snapshot for every
# date, so the numbers never changed once deployed. A unique SQL string per date
# sidesteps that entirely and is version-independent, so it stays even though SiS
# now runs 1.58. `as_of` comes from MONTHS_SQL, but we still assert the
# YYYY-MM-DD shape before interpolating.
def onhand_sql(as_of: str) -> str:
    """Build the on-hand snapshot SQL for ``as_of`` (YYYY-MM-DD)."""
    if not _AS_OF_RE.match(as_of):
        raise ValueError(f"as_of must be YYYY-MM-DD, got {as_of!r}")
    d = f"TO_DATE('{as_of}')"
    return f"""
WITH txns AS (
    SELECT ITEM_NUMBER, SUBJECT_NAME, BRAND, TEAM, RELIC_FORM_TYPE,
           ITEM_USED_STATUS, SUBINVENTORY_CODE, LOCATOR_NAME,
           TRY_TO_DECIMAL(QTY::string, 38, 4)        AS qty
    FROM {REC_VIEW}
    WHERE TXN_DATE <= {d}
    UNION ALL
    SELECT ITEM_NUMBER, SUBJECT_NAME, BRAND, TEAM, RELIC_FORM_TYPE,
           ITEM_USED_STATUS, SUBINVENTORY_CODE, LOCATOR_NAME,
           TRY_TO_DECIMAL(QTY::string, 38, 4)
    FROM {CON_VIEW}
    WHERE TXN_DATE <= {d}
),
bins AS (
    -- item x subinventory x locator grain, on-hand bins only
    SELECT ITEM_NUMBER, SUBJECT_NAME, BRAND, TEAM, RELIC_FORM_TYPE,
           ITEM_USED_STATUS, SUBINVENTORY_CODE, LOCATOR_NAME,
           SUM(qty) AS qty_onhand
    FROM txns
    GROUP BY 1,2,3,4,5,6,7,8
    HAVING SUM(qty) > 0
),
item_qty AS (
    -- allocation denominator; always > 0 because bins require SUM(qty) > 0
    SELECT ITEM_NUMBER, SUM(qty_onhand) AS item_qty
    FROM bins
    GROUP BY 1
),
val AS (
    -- authoritative per-item valuation for this period
    SELECT ITEM_NUMBER, ITEM_INV_VALU
    FROM {VAL_VIEW}
    WHERE TO_DATE('01-' || PERIOD_NAME, 'DD-MON-YY') = {d}
),{ITEM_AGE_CTE}
SELECT
    COALESCE(NULLIF(TRIM(b.ITEM_NUMBER), ''), 'OTHER')             AS item_number,
    a.item_description                                             AS item_description,
    COALESCE(NULLIF(TRIM(b.SUBJECT_NAME), ''), 'OTHER')           AS subject_name,
    COALESCE(NULLIF(TRIM(b.BRAND), ''), 'OTHER')                  AS brand,
    UPPER(COALESCE(NULLIF(TRIM(b.TEAM), ''), ''))                   AS team,
    UPPER(COALESCE(NULLIF(TRIM(b.RELIC_FORM_TYPE), ''), 'OTHER')) AS relic_form_type,
    UPPER(COALESCE(NULLIF(TRIM(b.ITEM_USED_STATUS), ''), ''))       AS item_used_status,
    b.SUBINVENTORY_CODE,
    TRIM(b.LOCATOR_NAME)                                            AS bin_location,
    b.qty_onhand,
    COALESCE(v.ITEM_INV_VALU, 0) * (b.qty_onhand / iq.item_qty)     AS valuation,
    CASE
        WHEN UPPER(b.ITEM_NUMBER) LIKE 'MEM%'           THEN 'WHOLE'
        WHEN UPPER(b.RELIC_FORM_TYPE) = 'CUT SIGNATURE' THEN 'CUT SIG'
        ELSE 'NON-WHOLE'
    END AS itype,
    CASE
        WHEN b.SUBINVENTORY_CODE = 'REL_SLATE'  THEN 'S'
        WHEN b.SUBINVENTORY_CODE = 'RELIC_OBSO' THEN 'O'
        ELSE 'U'
    END AS status,
    CASE
        WHEN b.SUBINVENTORY_CODE = 'REL_SLATE' THEN
            CASE
                WHEN SPLIT_PART(UPPER(b.LOCATOR_NAME), '.', 3) IN ('', '000')
                    THEN 'NO PROGRAM'
                ELSE SPLIT_PART(UPPER(b.LOCATOR_NAME), '.', 3)
            END
        ELSE NULL
    END AS program,
    a.age_date                                                     AS age_date,
    -- GREATEST(0, ...) because the age basis is a true receipt DATE while the
    -- as-of date is a first-of-month stamp: an item received later in the as-of
    -- month is genuinely on hand but would score a negative age. It clamps to 0
    -- (the 0-90 bucket), which is the truthful floor for brand-new inventory.
    GREATEST(0, DATEDIFF('day', a.age_date, {d}))    AS age_days
FROM bins b
JOIN item_qty iq      ON iq.ITEM_NUMBER = b.ITEM_NUMBER
LEFT JOIN val v       ON v.ITEM_NUMBER = b.ITEM_NUMBER
-- INNER JOIN: this is the on-hand gate. See ITEM_AGE_CTE.
JOIN item_age a       ON a.ITEM_NUMBER = b.ITEM_NUMBER
"""


# Multi-month history: ONE static, parameterless query returning the cumulative
# on-hand balance for EVERY valuation period at once, at month x item x
# subinventory x locator grain (program derived, matching onhand_sql). Feeds the
# TRENDS tab and the INVENTORY sparklines; loaded lazily (never on first paint).
#
# Parameterless (like MONTHS_SQL) to sidestep the old <=1.51 query-cache-key bug
# that ignored `params` (see the onhand_sql note above).
#
# Mirrors onhand_sql EXACTLY so the tabs agree:
#   * TRY_TO_DECIMAL(QTY::string, 38, 4)  -- QTY is VARCHAR
#   * ITEM_INV_VALU used RAW              -- it is NUMBER(18,5); NO TRY_TO_DECIMAL
#   * TXN_DATE used RAW                   -- it is a native DATE; NO TRY_TO_DATE
#   * TO_DATE('01-' || PERIOD_NAME, 'DD-MON-YY') for every period conversion
#   * identical NULLIF/TRIM guards and itype / status / program CASE expressions
#   * cumulative on-hand rule TXN_DATE <= month, LEFT JOIN valuation + COALESCE
#   * the SAME item universe: INNER JOIN item_age (see ITEM_AGE_CTE). Membership
#     only, with no per-month date test, so history spans exactly the universe the
#     snapshot does in every month and data.history_matches_snapshot holds. NOTE
#     that FCT_INVENTORY_AGING is a LIVE snapshot, so older months legitimately
#     lose items consumed since — history restates as inventory is consumed.
#   * no age columns: history has never carried them and
#     transforms._typed_value_cols already handles frames without age_days.
#
# HAVING SUM(qty) > 0 is applied at (item x subinventory x locator) grain; program
# is derived in the OUTER select only -- collapsing locator -> program before the
# HAVING would net negative locator bins into sibling programs and break agreement
# with load_onhand. monthly_net pre-aggregates txns per month before the range
# join to kill fan-out (TXN_DATE is month-stamped, so this stays small).
HISTORY_SQL = f"""
WITH months AS (
    SELECT DISTINCT TO_DATE('01-' || PERIOD_NAME, 'DD-MON-YY') AS month_start
    FROM {VAL_VIEW}
    WHERE PERIOD_NAME IS NOT NULL
),
txns AS (
    SELECT ITEM_NUMBER, SUBJECT_NAME, BRAND, TEAM, RELIC_FORM_TYPE,
           ITEM_USED_STATUS, SUBINVENTORY_CODE, LOCATOR_NAME,
           TXN_DATE                                  AS txn_month,
           TRY_TO_DECIMAL(QTY::string, 38, 4)        AS qty
    FROM {REC_VIEW}
    UNION ALL
    SELECT ITEM_NUMBER, SUBJECT_NAME, BRAND, TEAM, RELIC_FORM_TYPE,
           ITEM_USED_STATUS, SUBINVENTORY_CODE, LOCATOR_NAME,
           TXN_DATE,
           TRY_TO_DECIMAL(QTY::string, 38, 4)
    FROM {CON_VIEW}
),
monthly_net AS (
    -- pre-aggregate per txn-month BEFORE the spine join to kill fan-out
    SELECT txn_month, ITEM_NUMBER, SUBJECT_NAME, BRAND, TEAM, RELIC_FORM_TYPE,
           ITEM_USED_STATUS, SUBINVENTORY_CODE, LOCATOR_NAME,
           SUM(qty) AS qty
    FROM txns
    GROUP BY 1,2,3,4,5,6,7,8,9
),{ITEM_AGE_CTE},
bins_hist AS (
    -- cumulative on-hand per month at item x subinventory x locator grain
    SELECT m.month_start,
           n.ITEM_NUMBER, n.SUBJECT_NAME, n.BRAND, n.TEAM, n.RELIC_FORM_TYPE,
           n.ITEM_USED_STATUS, n.SUBINVENTORY_CODE, n.LOCATOR_NAME,
           SUM(n.qty) AS qty_onhand
    FROM months m
    JOIN monthly_net n ON n.txn_month <= m.month_start
    -- on-hand gate, applied BEFORE the HAVING so item_qty_hist (built off this
    -- CTE) stays an exact denominator for every retained item
    JOIN item_age a    ON a.ITEM_NUMBER = n.ITEM_NUMBER
    GROUP BY 1,2,3,4,5,6,7,8,9
    HAVING SUM(n.qty) > 0
),
item_qty_hist AS (
    -- allocation denominator per (month, item)
    SELECT month_start, ITEM_NUMBER, SUM(qty_onhand) AS item_qty
    FROM bins_hist
    GROUP BY 1,2
),
val_hist AS (
    -- authoritative per-item valuation, keyed by period first-of-month
    SELECT TO_DATE('01-' || PERIOD_NAME, 'DD-MON-YY') AS month_start,
           ITEM_NUMBER, ITEM_INV_VALU
    FROM {VAL_VIEW}
)
SELECT
    b.month_start                                                  AS month,
    COALESCE(NULLIF(TRIM(b.ITEM_NUMBER), ''), 'OTHER')           AS item_number,
    COALESCE(NULLIF(TRIM(b.SUBJECT_NAME), ''), 'OTHER')          AS subject_name,
    COALESCE(NULLIF(TRIM(b.BRAND), ''), 'OTHER')                 AS brand,
    UPPER(COALESCE(NULLIF(TRIM(b.TEAM), ''), ''))                  AS team,
    UPPER(COALESCE(NULLIF(TRIM(b.RELIC_FORM_TYPE), ''), 'OTHER')) AS relic_form_type,
    UPPER(COALESCE(NULLIF(TRIM(b.ITEM_USED_STATUS), ''), ''))      AS item_used_status,
    b.SUBINVENTORY_CODE,
    b.qty_onhand,
    COALESCE(v.ITEM_INV_VALU, 0) * (b.qty_onhand / iq.item_qty)    AS valuation,
    CASE
        WHEN UPPER(b.ITEM_NUMBER) LIKE 'MEM%'           THEN 'WHOLE'
        WHEN UPPER(b.RELIC_FORM_TYPE) = 'CUT SIGNATURE' THEN 'CUT SIG'
        ELSE 'NON-WHOLE'
    END AS itype,
    CASE
        WHEN b.SUBINVENTORY_CODE = 'REL_SLATE'  THEN 'S'
        WHEN b.SUBINVENTORY_CODE = 'RELIC_OBSO' THEN 'O'
        ELSE 'U'
    END AS status,
    CASE
        WHEN b.SUBINVENTORY_CODE = 'REL_SLATE' THEN
            CASE
                WHEN SPLIT_PART(UPPER(b.LOCATOR_NAME), '.', 3) IN ('', '000')
                    THEN 'NO PROGRAM'
                ELSE SPLIT_PART(UPPER(b.LOCATOR_NAME), '.', 3)
            END
        ELSE NULL
    END AS program
FROM bins_hist b
JOIN item_qty_hist iq ON iq.month_start = b.month_start AND iq.ITEM_NUMBER = b.ITEM_NUMBER
LEFT JOIN val_hist v  ON v.month_start = b.month_start AND v.ITEM_NUMBER = b.ITEM_NUMBER
"""
