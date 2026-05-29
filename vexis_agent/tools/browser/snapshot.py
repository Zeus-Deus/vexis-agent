"""Render the indexed accessibility-tree DSL snapshot for a live page.

browser-use used to hand us pre-serialized ``[index]<tag attr=val />``
lines plus an internal selector map. Driving the Camoufox page directly,
we build the same DSL ourselves: a single ``page.evaluate`` walks the DOM,
keeps the interactive/visible elements, stamps each with a
``data-vexis-idx`` attribute, and returns the rows. Python formats them
into the ``[idx]<tag .../>`` text the brain already reads, and ``click`` /
``type`` resolve an index back to its element via the stamped attribute
(see ``tools.py``). The index marker is the contract between snapshot and
the action that follows it.

The marker attribute survives on the live page until the next snapshot
re-stamps it, so an index handed to the brain stays valid until the DOM
is re-serialized — and a click against a vanished index simply finds no
element, which the action layer reports as a stale-index hint.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

#: Attribute stamped on each indexed element. Shared with ``tools.py``,
#: which builds ``[data-vexis-idx="N"]`` selectors from it.
INDEX_ATTR = "data-vexis-idx"

# Single-pass DOM serializer. Returns one row per interactive, visible
# element: {idx, tag, attrs (subset), text}. Clears any marks left by a
# prior snapshot first so indices never collide across calls.
_SNAPSHOT_JS = (
    r"""
() => {
  const ATTR = "%s";
  for (const el of document.querySelectorAll('[' + ATTR + ']')) {
    el.removeAttribute(ATTR);
  }
  const SELECTOR = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'summary',
    'details', 'label', '[role=button]', '[role=link]', '[role=checkbox]',
    '[role=radio]', '[role=tab]', '[role=menuitem]', '[role=switch]',
    '[role=textbox]', '[contenteditable=""]', '[contenteditable=true]',
    '[onclick]', '[tabindex]:not([tabindex="-1"])'
  ].join(',');
  const KEEP_ATTRS = ['type', 'name', 'id', 'placeholder', 'value',
    'aria-label', 'role', 'href', 'alt', 'title'];
  const rows = [];
  let i = 0;
  const seen = new Set();
  for (const el of document.querySelectorAll(SELECTOR)) {
    if (seen.has(el)) continue;
    seen.add(el);
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' ||
        style.opacity === '0') continue;
    if (el.disabled) continue;
    el.setAttribute(ATTR, String(i));
    const attrs = {};
    for (const a of KEEP_ATTRS) {
      let v = el.getAttribute(a);
      if (v === null || v === '') continue;
      if (a === 'href') v = v.slice(0, 120);
      attrs[a] = String(v).slice(0, 160);
    }
    let text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text) text = (el.value || attrs['aria-label'] || attrs['placeholder'] || '');
    rows.push({
      idx: i,
      tag: el.tagName.toLowerCase(),
      attrs: attrs,
      text: String(text).slice(0, 120),
    });
    i++;
  }
  return JSON.stringify(rows);
}
"""
    % INDEX_ATTR
)


def _format_rows(rows: list[dict[str, Any]]) -> str:
    """Turn the JS rows into ``[idx]<tag attr="v">text</tag>`` lines."""
    lines: list[str] = []
    for row in rows:
        attrs = row.get("attrs") or {}
        # 'value' lives in the text slot for inputs; don't double-print it.
        attr_str = "".join(
            f' {k}="{v}"' for k, v in attrs.items() if k != "value"
        )
        text = (row.get("text") or "").strip()
        tag = row.get("tag", "div")
        idx = row.get("idx")
        if text:
            lines.append(f"[{idx}]<{tag}{attr_str}>{text}</{tag}>")
        else:
            lines.append(f"[{idx}]<{tag}{attr_str} />")
    return "\n".join(lines)


async def render(page: Any) -> dict[str, Any]:
    """Serialize ``page`` into the snapshot dict the tools layer returns.

    Keys mirror the historical browser-use contract:
    ``snapshot`` (DSL text), ``element_count``, ``url``, ``title``.
    """
    raw = await page.evaluate(_SNAPSHOT_JS)
    try:
        rows = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (ValueError, TypeError):
        log.debug("snapshot rows were not valid JSON", exc_info=True)
        rows = []
    text = _format_rows(rows)
    url = ""
    title = ""
    try:
        url = page.url or ""
    except Exception:
        log.debug("page.url failed", exc_info=True)
    try:
        title = await page.title() or ""
    except Exception:
        log.debug("page.title() failed", exc_info=True)
    return {
        "snapshot": text,
        "element_count": len(rows),
        "url": url,
        "title": title,
    }
