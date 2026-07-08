"""Core capability prompt-block registry (issue #30).

Decomposes the former ``CAPABILITIES.md`` monolith into per-capability
prompt blocks that live NEXT TO the code they document — the
Hermes-style modular-docs model. Each core capability module
(``vexis_agent/tools/browser/capability.py``,
``vexis_agent/tools/desktop_capability.py``, …) owns one slice of the
system-prompt "Capabilities" section and self-registers here on import.

``assemble_capability_docs()`` re-assembles those blocks, in a stable
``order``, into the exact text the monolith used to carry — so the
model sees the same prompt while the docs now move with the tool they
describe. When an engine changes (the browser switched to Camoufox;
the next swap), its guidance is edited in the same module/PR instead
of drifting in a shared 1000-line file.

``CAPABILITIES.md`` is **not deleted**: it shrinks to the stable core
(identity + the add-on/skill/MCP model) and is emitted first, at
``CORE_BLOCK_ORDER`` (0), via :func:`vexis_agent.data.read_capabilities`.

Relationship to the add-on prompt-block hook: add-ons contribute
*dynamic* "active state" headers via
``ctx.register_system_prompt_block`` (appended at the very END of the
prompt by ``main._addon_header_blocks``). THIS registry is for *core*
capability how-to and slots in where ``CAPABILITIES.md`` used to sit
(after SOUL, before the skill-authoring block). The two are
deliberately separate: different lifecycles, different prompt
positions.

Byte-identity contract: ``assemble_capability_docs()`` reproduces the
pre-decomposition ``CAPABILITIES.md`` verbatim, guarded by
``tests/test_capability_blocks.py`` against a frozen golden snapshot
(``tests/data/capabilities_golden.md``). Both brain prompt builders
(claude-code + opencode) call it, so the contract holds for every
brain.

Adding a new core capability: drop a ``*_capability.py`` next to your
tool, call :func:`register_capability_block` with a fresh ``order``,
and list the module in :data:`_BUILTIN_CAPABILITY_MODULES` below. You
never edit ``CAPABILITIES.md`` again. See ``docs/capabilities.md``.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

#: Order of the stable-core block (the shrunk ``CAPABILITIES.md``).
#: Everything else sorts after it; the per-tool blocks keep the chunk
#: indices they had in the monolith so the assembled order — and thus
#: the assembled bytes — is unchanged.
CORE_BLOCK_ORDER = 0


@dataclass(frozen=True)
class CapabilityBlock:
    """One core capability's contribution to the system prompt.

    ``provider`` is called per assembly and returns the block's
    markdown (injected verbatim) or ``None`` (skip — block hidden).
    Almost every core block returns a constant string; the ``core``
    block re-reads ``CAPABILITIES.md`` from package data each call so
    a wheel/source edit hot-reloads on the next prompt build, matching
    the monolith's old read-per-build behaviour.
    """

    name: str
    order: float
    provider: Callable[[], Optional[str]]


#: name -> CapabilityBlock. Populated by ``register_capability_block``
#: at module-import time (per-tool modules) plus the ``core`` block
#: registered lazily in :func:`_ensure_loaded`. Add-on capability
#: blocks land here too: ``ctx.register_capability_block`` delegates to
#: ``register_capability_block`` via ``AddonRuntime.add_capability_block``
#: at add-on load time, so add-on blocks share this one registry — and
#: thus the same global ``order`` space and the same conflict checks —
#: with the core blocks. ``assemble_capability_docs()`` reads this dict,
#: so the merge is automatic; no separate add-on assembly path exists.
_REGISTRY: dict[str, CapabilityBlock] = {}

#: Modules that own a core capability block. Imported exactly once by
#: :func:`_ensure_loaded`; each calls :func:`register_capability_block`
#: at import time. The order of THIS list is irrelevant — the ``order``
#: field on each block decides placement. Modules are co-located with
#: the tool each documents (docs move with code, issue #30); the three
#: cross-cutting blocks that have no single owning tool live in
#: ``builtin`` here in the capabilities package.
_BUILTIN_CAPABILITY_MODULES: tuple[str, ...] = (
    # Self-extension framing (no single tool owner): which seam to use
    # when adding a capability (skill / MCP / add-on) + hot-vs-restart.
    "vexis_agent.core.capabilities.self_extension",
    # Cross-cutting (no single tool owner): inbound images, omarchy-kb,
    # web dashboard.
    "vexis_agent.core.capabilities.builtin",
    # Desktop: screenshot capture, mouse/keyboard/window control, the
    # vision-verification loop.
    "vexis_agent.tools.desktop_capability",
    # Sandboxes + headless displays (vexis-sandbox / vexis-display).
    "vexis_agent.tools.sandbox.capability",
    # Live MJPEG streaming (vexis-stream).
    "vexis_agent.tools.livestream_capability",
    # Background tasks (vexis-bg) + system-context envelope.
    "vexis_agent.tools.background_capability",
    # Background subagents (Agent tool) under the per-turn `claude -p`
    # harness: bounded post-reply wait, route long work to kanban/goal.
    # Order 9.25 places it right after background-tasks (issue #61).
    "vexis_agent.tools.background_subagents_capability",
    # Goals (/goal) — background-by-default multi-step objectives +
    # the [BACKGROUND GOALS] progress block. Order 9.5 places it right
    # after background-tasks and before memory.
    "vexis_agent.core.goal_capability",
    # Persistent memory notes (vexis-mem).
    "vexis_agent.tools.memory_capability",
    # Procedural-knowledge skills library (vexis-skill).
    "vexis_agent.tools.skills_capability",
    # Scheduling (vexis-agent schedule).
    "vexis_agent.tools.schedule_tool.capability",
    # NOTE: web-browsing (order 13) is NOT a builtin. It moved to the
    # browser add-on (``vexis_agent/addons/browser/``), which registers
    # it via ``ctx.register_capability_block`` at load time — so the
    # "Web browsing" section appears in the assembled prompt only when
    # the browser add-on is enabled, and is absent from the
    # builtins-only golden. Same model as codemux-orchestration below.
    # NOTE: codemux-orchestration (order 15) is NOT a builtin. It moved
    # to the codemux add-on, which registers it via
    # ``ctx.register_capability_block`` at load time — so it appears in
    # the assembled prompt only when codemux is enabled, and is absent
    # from the builtins-only golden.
)

_loaded = False


def register_capability_block(
    name: str,
    *,
    order: float,
    provider: Callable[[], Optional[str]],
) -> None:
    """Register one core capability block.

    Raises ``ValueError`` on a duplicate ``name`` OR a duplicate
    ``order`` — orders are positions in the assembled doc and must be
    unique so the output is deterministic. Called at import time by
    each capability module.
    """
    if name in _REGISTRY:
        raise ValueError(
            f"capability block {name!r} already registered "
            f"(order {_REGISTRY[name].order})"
        )
    for existing in _REGISTRY.values():
        if existing.order == order:
            raise ValueError(
                f"capability block order {order} already taken by "
                f"{existing.name!r}; pick a distinct order for {name!r}"
            )
    _REGISTRY[name] = CapabilityBlock(name=name, order=order, provider=provider)


def _core_block() -> Optional[str]:
    """Provider for the stable-core block — the shrunk CAPABILITIES.md.

    Re-reads package data each call (hot-reload parity with the old
    monolith). ``None`` when the file is missing from the wheel — the
    same packaging-regression case ``main`` already warns about.
    """
    from vexis_agent.data import read_capabilities

    text = read_capabilities()
    return text.rstrip("\n") if text else None


def _ensure_loaded() -> None:
    """Import every capability module once and register the core block.

    Import failures are logged but never raised: a missing block
    degrades the prompt (that section vanishes) rather than crashing
    the daemon's prompt build. The byte-identity test catches a
    genuinely missing/renamed module in CI.
    """
    global _loaded
    if _loaded:
        return
    if "core" not in _REGISTRY:
        register_capability_block(
            "core", order=CORE_BLOCK_ORDER, provider=_core_block
        )
    for module_path in _BUILTIN_CAPABILITY_MODULES:
        try:
            importlib.import_module(module_path)
        except Exception:
            log.exception(
                "capability module %r failed to import; its block will be "
                "missing from the system prompt",
                module_path,
            )
    _loaded = True


def iter_capability_blocks() -> list[CapabilityBlock]:
    """All registered blocks, sorted by ``(order, name)`` (loads first)."""
    _ensure_loaded()
    return sorted(_REGISTRY.values(), key=lambda b: (b.order, b.name))


def assemble_capability_docs() -> str:
    """Assemble the full "Capabilities" prompt section.

    Replaces the monolithic ``read_capabilities()`` call in both brain
    prompt builders. Returns the per-capability blocks joined exactly
    as the monolith was laid out (``"\\n\\n"`` between sections, single
    trailing newline) — byte-identical to the pre-decomposition file
    when only core blocks are registered.

    Add-on capability blocks merge in transparently: they live in the
    same ``_REGISTRY`` (registered via
    ``ctx.register_capability_block`` at add-on load time) and are
    sorted into the same global ``order`` space, so they appear in
    deterministic position with no separate assembly path. Order
    collisions across core+add-on are rejected at registration time,
    not here. A provider that raises is logged and skipped so one
    broken block can't take down the whole prompt.
    """
    parts: list[str] = []
    for block in iter_capability_blocks():
        try:
            text = block.provider()
        except Exception:
            log.exception(
                "capability block %r provider raised; skipping", block.name
            )
            continue
        if text and text.strip():
            parts.append(text.rstrip("\n"))
    return "\n\n".join(parts) + "\n"
