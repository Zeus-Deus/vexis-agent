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

The walk reaches past a flat ``querySelectorAll`` in two ways modern web
UIs demand: it recurses into open shadow roots (web components), and it
treats plain ``div/span/li/td/tr`` styled with ``cursor: pointer`` as
clickable custom controls — not just elements with semantic tags/roles.
See ``_SNAPSHOT_JS`` for the exact rules (and the wrapper-noise guard).

The marker attribute survives on the live page until the next snapshot
re-stamps it, so an index handed to the brain stays valid until the DOM
is re-serialized — and a click against a vanished index simply finds no
element, which the action layer reports as a stale-index hint. Indices
stamped inside an open shadow root resolve from the action layer because
Playwright's CSS selector engine pierces open shadow DOM.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

#: Attribute stamped on each indexed element. Shared with ``tools.py``,
#: which builds ``[data-vexis-idx="N"]`` selectors from it.
INDEX_ATTR = "data-vexis-idx"

# DOM serializer. Returns one row per interactive, visible element:
# {idx, tag, attrs (subset), text}. Two pieces of modern-web reach beyond
# a flat `document.querySelectorAll`:
#
#   * Shadow DOM — `querySelectorAll` stops at shadow boundaries, so we
#     recurse into every OPEN `shadowRoot`. (Closed roots are inaccessible
#     to any script — nothing we can do.) The marker attribute we stamp is
#     resolvable from the action layer because Playwright's CSS engine
#     pierces open shadow DOM, so `[data-vexis-idx="N"]` still finds an
#     element stamped inside a web component.
#   * Custom controls — frameworks build clickable widgets out of plain
#     `div/span/li/td/tr` carrying no semantic role, signalled only by a
#     `cursor: pointer` style. We index those too, but skip a styled
#     element that merely WRAPS a real semantic control (we'd rather hand
#     the brain the inner button/link than the decorative container).
#
# Marks left by a prior snapshot are cleared element-by-element during the
# walk (including inside shadow roots a top-level clear can't reach) so
# indices never collide across calls. Row shape is unchanged from the
# semantic-only era, so `_format_rows`/`render` are untouched.
_SNAPSHOT_JS = (
    r"""
() => {
  const ATTR = "%s";
  const SELECTOR = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'summary',
    'details', 'label', '[role=button]', '[role=link]', '[role=checkbox]',
    '[role=radio]', '[role=tab]', '[role=menuitem]', '[role=switch]',
    '[role=textbox]', '[contenteditable=""]', '[contenteditable=true]',
    '[onclick]', '[tabindex]:not([tabindex="-1"])'
  ].join(',');
  // Non-semantic tags promoted to clickable only when styled with a
  // pointer cursor — the convention custom controls use.
  const CURSOR_TAGS = new Set(['div', 'span', 'li', 'td', 'tr']);
  const KEEP_ATTRS = ['type', 'name', 'id', 'placeholder', 'value',
    'aria-label', 'role', 'href', 'alt', 'title'];
  const rows = [];
  let i = 0;

  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      // Clear any prior mark first — even on elements we won't re-index and
      // even inside shadow roots — so stale indices can't survive a snapshot.
      el.removeAttribute(ATTR);
      // Descend into open web components before considering the host.
      if (el.shadowRoot) walk(el.shadowRoot);
      // Cheap candidacy test BEFORE the expensive layout/style reads:
      // semantic match, or a cursor-tag that might be a custom control.
      const semantic = el.matches(SELECTOR);
      const cursorTag = !semantic && CURSOR_TAGS.has(el.tagName.toLowerCase());
      if (!semantic && !cursorTag) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      const style = window.getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none' ||
          style.opacity === '0') continue;
      if (el.disabled) continue;
      if (cursorTag) {
        if (style.cursor !== 'pointer') continue;
        // A pointer-styled wrapper around a real control is noise — index
        // the inner semantic element instead, not the container.
        if (el.querySelector(SELECTOR)) continue;
      }
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
  };
  walk(document);
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
