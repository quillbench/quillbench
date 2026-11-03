"""Keyboard shortcuts for the annotation screens.

1-9 answer with the n-th button, S skips, J / → next item, K / ← previous item.
Keys are ignored while the cursor is in a text box, so notes can be typed freely.
The handler finds buttons by the ``st-key-<key>`` class Streamlit gives each
keyed widget, so it needs no knowledge of the task being shown.
"""

from __future__ import annotations

from .embed import run_script

_JS = """
<script>
(function () {
  const doc = window.parent.document;
  if (window.parent.__pasShortcuts) { return; }
  window.parent.__pasShortcuts = true;
  function click(prefix) {
    const el = doc.querySelector('[class*="st-key-' + prefix + '"] button');
    if (el && !el.disabled) { el.click(); return true; }
    return false;
  }
  doc.addEventListener('keydown', function (e) {
    const t = e.target;
    const tag = (t && t.tagName || '').toLowerCase();
    if (tag === 'textarea' || tag === 'input' || tag === 'select' || (t && t.isContentEditable)) { return; }
    if (e.ctrlKey || e.metaKey || e.altKey) { return; }
    const k = e.key;
    let done = false;
    if (/^[1-9]$/.test(k)) { done = click('verdict_' + k + '_'); }
    else if (k === 's' || k === 'S') { done = click('verdict_skip_'); }
    else if (k === 'j' || k === 'J' || k === 'ArrowRight') { done = click('nav_next_'); }
    else if (k === 'k' || k === 'K' || k === 'ArrowLeft') { done = click('nav_prev_'); }
    else if (k === 'Enter') { done = click('rating_save_'); }
    if (done) { e.preventDefault(); }
  });
})();
</script>
"""


def install() -> None:
    run_script(_JS, "shortcuts")


HELP_HTML = (
    '<div class="nav-legend"><b>Keyboard</b><br>'
    '<span class="kbd">1</span>–<span class="kbd">9</span> answer &nbsp; '
    '<span class="kbd">S</span> skip &nbsp; <span class="kbd">J</span>/<span class="kbd">→</span> next &nbsp; '
    '<span class="kbd">K</span>/<span class="kbd">←</span> previous &nbsp; '
    '<span class="kbd">Enter</span> save rating<br>Keys are ignored while you type in a text box.</div>'
)
