"""Render the accessibility-tree DSL snapshot for a live session.

browser-use produces tab-indented ``[index]<tag attr=val />`` lines. We
parse the index tokens to count interactive elements, and ride along
with a URL/title pull so the brain has minimal page metadata without
chaining a second tool call.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from browser_use import BrowserSession

log = logging.getLogger(__name__)

_INDEX_RE = re.compile(r"\*?\[(\d+)\]<")


async def render(session: BrowserSession) -> dict[str, Any]:
    text = await session.get_state_as_text()
    indexes = {int(m.group(1)) for m in _INDEX_RE.finditer(text)}
    url = ""
    title = ""
    try:
        url = await session.get_current_page_url() or ""
    except Exception:
        log.debug("get_current_page_url failed", exc_info=True)
    try:
        title = await session.get_current_page_title() or ""
    except Exception:
        log.debug("get_current_page_title failed", exc_info=True)
    return {
        "snapshot": text,
        "element_count": len(indexes),
        "url": url,
        "title": title,
    }
