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
"""
from __future__ import annotations

import streamlit as st

from style import TABLE_CSS

_SHELL_HTML = '<div class="fct"><div class="mount"></div></div>'

# Re-runs whenever the mounted data changes: re-render the table, rebind row
# clicks, and (one-shot, Python-controlled) scroll the table into view.
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
  if (data.scroll) {
    const el = wrap || mount.firstElementChild;
    // ~80ms mirrors the template's scrollBelowHeader timing; lets layout settle
    if (el) setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  }
}
"""

_renderer = st.components.v2.component("fct_table", html=_SHELL_HTML,
                                       css=TABLE_CSS, js=_JS)


def table(html: str, *, key: str, scroll: bool = False,
          max_height: int | None = None) -> str | None:
    """Mount an .fct table block; returns the clicked row's data-key, if any.

    Rows carry data-key only when built with clickable=True (style.py), so
    plain tables mounted through here are static but still scrollable targets.
    The returned value is a transient trigger: it is non-None only on the
    script run caused by the click.

    max_height (px) caps the table's scroll container for the rows-per-view
    option; None leaves it uncapped (renders every row, as before).
    """
    res = _renderer(key=key,
                    data={"html": html, "scroll": scroll, "maxHeight": max_height},
                    on_select_change=lambda: None)
    return res.select
