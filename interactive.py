"""Clickable .fct tables with scroll-into-view, via st.components.v2.

Custom Components v2 (Streamlit >= 1.51; both local and the SiS container
runtime run 1.58) render frameless with bidirectional data flow. The
component is defined with inline HTML/CSS/JS only, which is the one form
Streamlit-in-Snowflake supports (no asset dirs; CSP forbids external
scripts/eval — none used here).

The component renders in a shadow DOM (we never pass isolate_styles, so the
isolating default applies). That is why the table CSS ships via css= — page
styles can't pierce the shadow root — and why the JS must write into the
.mount child: replacing parentElement's innerHTML would wipe the injected
<style>.

Two independent triggers flow back to Python:
  select : a tbody row's data-key (drill-down)
  sort   : a thead cell's data-sort (column sort)
They can never collide — data-key is only ever emitted inside <tbody> and
data-sort only inside <thead> (see style.py).
"""
from __future__ import annotations

from typing import NamedTuple

import streamlit as st

from style import TABLE_CSS

_SHELL_HTML = '<div class="fct"><div class="mount"></div></div>'

# Re-runs whenever the mounted data changes: re-render the table, rebind row +
# header clicks, and (one-shot, Python-controlled) scroll the table into view.
_JS = """
export default function ({ data, setTriggerValue, parentElement }) {
  const mount = parentElement.querySelector('.mount');
  mount.innerHTML = data.html;
  const wrap = mount.querySelector('.table-wrap');
  // rows-per-view cap: bound the table height and scroll the overflow (null = uncapped)
  if (wrap && data.maxHeight) {
    wrap.classList.add('scrollable');
    wrap.style.maxHeight = data.maxHeight + 'px';
  }
  mount.querySelectorAll('tbody tr[data-key]').forEach((tr) => {
    tr.addEventListener('click', () => setTriggerValue('select', tr.getAttribute('data-key')));
  });
  // Column sort: the sorting itself happens in pandas on the real values, so
  // this only reports WHICH column was clicked. Sorting rendered cell text here
  // would order formatted strings ($1,234 / 120d / — ) instead of numbers.
  mount.querySelectorAll('thead th[data-sort]').forEach((th) => {
    th.addEventListener('click', () => setTriggerValue('sort', th.getAttribute('data-sort')));
  });
  if (data.scroll) {
    const el = wrap || mount.firstElementChild;
    // ~80ms mirrors the template's scrollBelowHeader timing; lets layout settle
    if (el) setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  }
}
"""

# The component NAME is versioned deliberately. A v2 component's css=/js= are
# registered once per name, so editing TABLE_CSS while keeping the old name can
# leave a browser serving the previously registered stylesheet — which is exactly
# what kept rendering table headers in UPPERCASE after text-transform was removed
# (the per-render HTML was already correct; only the CSS was stale). Bump this
# suffix whenever TABLE_CSS or _JS changes in a way that must reach clients.
_renderer = st.components.v2.component("fct_table_v3", html=_SHELL_HTML,
                                       css=TABLE_CSS, js=_JS)


class TableEvent(NamedTuple):
    """Transient click results for one mounted table.

    Both fields are non-None only on the script run caused by that click.
    ``select`` is a row's data-key (drill-down); ``sort`` is a header's
    data-sort (the column to sort by).
    """

    select: str | None
    sort: str | None


def table(html: str, *, key: str, scroll: bool = False,
          max_height: int | None = None) -> TableEvent:
    """Mount an .fct table block; report row-click and header-click events.

    Rows carry data-key only when built with clickable=True (style.py), so
    plain tables mounted through here are static but still scrollable targets.
    Headers carry data-sort whenever the builder was given a sortable column
    spec. Both returned values are transient triggers: non-None only on the
    script run caused by the click.

    max_height (px) caps the table's scroll container for the rows-per-view
    option; None leaves it uncapped (renders every row, as before).
    """
    res = _renderer(key=key,
                    data={"html": html, "scroll": scroll, "maxHeight": max_height},
                    on_select_change=lambda: None,
                    on_sort_change=lambda: None)
    return TableEvent(res.select, res.sort)
