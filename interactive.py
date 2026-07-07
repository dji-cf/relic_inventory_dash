"""Clickable .fct tables with scroll-into-view, via st.components.v2.

Custom Components v2 (added in Streamlit 1.51 — the SiS warehouse-runtime
pin in environment.yml) render frameless with bidirectional data flow. The
component is defined with inline HTML/CSS/JS only, which is the one form
Streamlit-in-Snowflake supports (no asset dirs; CSP forbids external
scripts/eval — none used here).

Cross-version notes (local venv 1.58 vs SiS 1.51):
  * isolate_styles moved from mount-time (1.51) to definition-time (1.58);
    we never pass it, so both versions use the shadow-DOM default. That is
    why the table CSS ships via css= (page styles can't pierce the shadow
    root) and why the JS must write into the .mount child — replacing
    parentElement's innerHTML would wipe the injected <style>.
  * The result class was renamed BidiComponentResult -> ComponentResult;
    it is never imported here.
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
  mount.querySelectorAll('tbody tr[data-key]').forEach((tr) => {
    tr.addEventListener('click', () => setTriggerValue('select', tr.getAttribute('data-key')));
  });
  if (data.scroll) {
    const el = mount.querySelector('.table-wrap') || mount.firstElementChild;
    // ~80ms mirrors the template's scrollBelowHeader timing; lets layout settle
    if (el) setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  }
}
"""

_renderer = st.components.v2.component("fct_table", html=_SHELL_HTML,
                                       css=TABLE_CSS, js=_JS)


def table(html: str, *, key: str, scroll: bool = False) -> str | None:
    """Mount an .fct table block; returns the clicked row's data-key, if any.

    Rows carry data-key only when built with clickable=True (style.py), so
    plain tables mounted through here are static but still scrollable targets.
    The returned value is a transient trigger: it is non-None only on the
    script run caused by the click.
    """
    res = _renderer(key=key, data={"html": html, "scroll": scroll},
                    on_select_change=lambda: None)
    return res.select
