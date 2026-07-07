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
# is unique per date. st.connection(...).query() caches results, and in the
# Streamlit version SiS runs (<=1.51) that cache key IGNORES `params` — a static
# SQL string + qmark binds returns the first-loaded snapshot for every date, so
# the numbers never change once deployed. A unique SQL string per date sidesteps
# that entirely and is version-independent. `as_of` comes from MONTHS_SQL, but we
# still assert the YYYY-MM-DD shape before interpolating.
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
)
SELECT
    COALESCE(NULLIF(TRIM(b.ITEM_NUMBER), ''), '(BLANK)')             AS item_number,
    COALESCE(NULLIF(TRIM(b.SUBJECT_NAME), ''), '(BLANK)')           AS subject_name,
    COALESCE(NULLIF(TRIM(b.BRAND), ''), '(BLANK)')                  AS brand,
    UPPER(COALESCE(NULLIF(TRIM(b.TEAM), ''), ''))                   AS team,
    UPPER(COALESCE(NULLIF(TRIM(b.RELIC_FORM_TYPE), ''), '(BLANK)')) AS relic_form_type,
    UPPER(COALESCE(NULLIF(TRIM(b.ITEM_USED_STATUS), ''), ''))       AS item_used_status,
    b.SUBINVENTORY_CODE,
    b.qty_onhand,
    v.ITEM_INV_VALU * (b.qty_onhand / iq.item_qty)                  AS valuation,
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
FROM bins b
JOIN item_qty iq ON iq.ITEM_NUMBER = b.ITEM_NUMBER
JOIN val v       ON v.ITEM_NUMBER = b.ITEM_NUMBER
"""
