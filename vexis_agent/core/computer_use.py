"""Computer-use model selection — the Codex-style "dynamic model"
trick, adapted for vexis.

Two layers, both strictly opt-in (everything stays on the brain's
account default until the user touches the dashboard's Computer Use
tab):

* **Pinned model** (``computer_use.model``) — a per-turn model
  override that bites only when the foreground turn is actually
  doing computer-use work.
* **Dynamic model** (``computer_use.dynamic``) — when the last
  ``vexis-ui`` snapshot was a *rich* AT-SPI textual tree (no
  screenshot fallback, ``element_count`` over the threshold), the
  turn can run on a faster model: the interface is fully described
  in text, so no vision-capable model is needed.

The signal flows over a small JSON file rather than in-process state
because ``UIDriver`` runs wherever ``vexis-ui`` is invoked (the brain
subprocess, or the daemon) while the gating decision happens in the
daemon's ``MessageHandler``. The file is the IPC seam:

    UIDriver.snapshot()  ──writes──▶  computer-use-runtime.json
                                            │
    MessageHandler.handle()  ──reads──▶  resolve_computer_use_override()

"Recent activity" gating is what keeps pure chat untouched: if no
``vexis-ui`` snapshot landed in the last :data:`ACTIVITY_TTL_SECONDS`,
the turn is not a computer-use turn and no override applies — the
``computer_use.*`` config is inert for plain Telegram / text chat.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from vexis_agent.core.paths import vexis_dir
from vexis_agent.core.yaml_config import (
    DEFAULT_COMPUTER_USE_MIN_ELEMENTS,
    computer_use_dynamic_enabled,
    computer_use_dynamic_min_elements,
    computer_use_dynamic_model,
    computer_use_dynamic_reasoning_level,
    computer_use_model,
    computer_use_reasoning_level,
)

log = logging.getLogger(__name__)

# A ``vexis-ui`` snapshot older than this no longer counts as "the
# turn is doing computer-use work". 10 minutes is generous — a single
# build-and-test loop step is far shorter — but it errs on the side of
# keeping the override active across a slow think rather than flapping
# back to the brain default mid-task.
ACTIVITY_TTL_SECONDS: float = 600.0

_RUNTIME_STATE_FILENAME = "computer-use-runtime.json"


def _runtime_state_path() -> Path:
    return vexis_dir() / _RUNTIME_STATE_FILENAME


# ──────────────────────────────────────────────────────────────────
# Runtime state — the UIDriver → handler IPC seam
# ──────────────────────────────────────────────────────────────────


def record_snapshot_activity(
    *,
    element_count: int,
    used_vision_fallback: bool,
    stale: bool,
) -> None:
    """Record the character of the most recent ``vexis-ui`` snapshot.

    Best-effort: any failure (no ``~/.vexis`` dir, read-only fs) is
    swallowed — a missing signal just means the next turn falls back
    to the brain default, which is the safe direction.

    ``used_vision_fallback`` is True for ``vision_snapshot()`` (the
    screenshot path) and False for the AT-SPI ``snapshot()``. ``stale``
    mirrors ``SnapshotResult.stale`` (empty tree / no focused window).
    """
    payload = {
        "element_count": int(element_count),
        "used_vision_fallback": bool(used_vision_fallback),
        "stale": bool(stale),
        "ts": time.time(),
    }
    try:
        path = _runtime_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.debug("computer-use: could not record snapshot activity: %s", exc)


def read_runtime_state() -> dict[str, Any] | None:
    """Parse the runtime-state file, or ``None`` when it is absent or
    unreadable. Never raises."""
    try:
        raw = _runtime_state_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _state_age_seconds(state: dict[str, Any], now: float) -> float | None:
    ts = state.get("ts")
    if not isinstance(ts, (int, float)):
        return None
    return max(0.0, now - float(ts))


def _state_is_fresh(state: dict[str, Any], now: float) -> bool:
    age = _state_age_seconds(state, now)
    return age is not None and age <= ACTIVITY_TTL_SECONDS


def _state_is_rich(state: dict[str, Any]) -> bool:
    """A snapshot is "rich enough to skip vision" when it is a real
    AT-SPI tree (not stale, not the screenshot fallback) carrying at
    least :func:`computer_use_dynamic_min_elements` indexed widgets."""
    if state.get("stale") is True:
        return False
    if state.get("used_vision_fallback") is True:
        return False
    count = state.get("element_count")
    if not isinstance(count, int):
        return False
    return count >= computer_use_dynamic_min_elements()


# ──────────────────────────────────────────────────────────────────
# The decision — consulted by MessageHandler at turn start
# ──────────────────────────────────────────────────────────────────


def resolve_computer_use_override(
    *, now: float | None = None,
) -> tuple[str | None, str | None]:
    """Return the ``(model, reasoning_level)`` override for a
    foreground turn, or ``(None, None)`` when no computer-use override
    applies.

    ``(None, None)`` is the common case and is bit-for-bit identical
    to the pre-feature behaviour — the handler simply passes ``None``
    on to the brain. An override is returned only when **both**:

    * the user opted in (a pinned model and/or dynamic mode is
      configured), AND
    * there is a fresh ``vexis-ui`` snapshot — i.e. this turn really
      is doing computer-use work.

    When dynamic mode is on AND the last snapshot was rich, the
    dynamic (fast) model wins. Otherwise the pinned model is used.
    """
    pinned = computer_use_model()
    dynamic_enabled = computer_use_dynamic_enabled()

    # User hasn't opted into anything — never touch the brain default.
    if pinned is None and not dynamic_enabled:
        return (None, None)

    state = read_runtime_state()
    if state is None:
        return (None, None)
    if not _state_is_fresh(state, now if now is not None else time.time()):
        # No recent computer-use activity. This is a plain chat turn;
        # the computer_use.* config is inert here by design.
        return (None, None)

    if dynamic_enabled and _state_is_rich(state):
        dyn_model = computer_use_dynamic_model()
        if dyn_model is not None:
            return (dyn_model, computer_use_dynamic_reasoning_level())
        # Dynamic on but no dynamic model set → fall through to the
        # pinned model (or, below, the brain default).

    if pinned is not None:
        return (pinned, computer_use_reasoning_level())
    return (None, None)


# ──────────────────────────────────────────────────────────────────
# Dashboard payload + writer (Computer Use tab)
# ──────────────────────────────────────────────────────────────────


def _activity_view(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project the runtime state into a UI-friendly readout, or
    ``None`` when there has never been a snapshot."""
    if state is None:
        return None
    now = time.time()
    age = _state_age_seconds(state, now)
    fresh = _state_is_fresh(state, now)
    return {
        "element_count": state.get("element_count"),
        "used_vision_fallback": bool(state.get("used_vision_fallback")),
        "stale": bool(state.get("stale")),
        "age_seconds": age,
        "fresh": fresh,
        # ``rich`` is the live verdict the dynamic switch keys off —
        # surfaced so the dashboard can show "dynamic model would
        # apply right now".
        "rich": fresh and _state_is_rich(state),
    }


def computer_use_payload() -> dict[str, Any]:
    """Snapshot for the dashboard's Computer Use tab — current
    ``computer_use.*`` config plus the discovery-backed model list and
    a live readout of the last ``vexis-ui`` snapshot."""
    from vexis_agent.core.model_discovery import available_models_for_picker
    from vexis_agent.core.yaml_config import brain_kind

    return {
        # Empty-string sentinel ("") on the wire = "use brain default",
        # symmetric with the Voice tab's call-mode picker.
        "model": computer_use_model() or "",
        "reasoning_level": computer_use_reasoning_level() or "",
        "dynamic": {
            "enabled": computer_use_dynamic_enabled(),
            "model": computer_use_dynamic_model() or "",
            "reasoning_level": computer_use_dynamic_reasoning_level() or "",
            "min_elements": computer_use_dynamic_min_elements(),
        },
        "available_models": available_models_for_picker(brain_kind()),
        "last_activity": _activity_view(read_runtime_state()),
    }


def _coerce_model_knob(value: Any, label: str) -> str | None:
    """Null / empty / ``default`` → ``None`` (drop the key); a real
    string → the trimmed value. Anything else raises ``ValueError``."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.lower() == "default":
            return None
        return cleaned
    raise ValueError(f"{label} must be a string or null")


def computer_use_set(payload: dict[str, Any]) -> dict[str, Any]:
    """Partial update of the ``computer_use.*`` config keys. Accepts
    any subset of::

        {
          "model": str | null,
          "reasoning_level": str | null,
          "dynamic": {
            "enabled": bool,
            "model": str | null,
            "reasoning_level": str | null,
            "min_elements": int,
          },
        }

    Writes through the same atomic + comment-preserving path the Voice
    and Models tabs use. Raises :class:`ValueError` on a malformed
    payload (the route layer maps that to a 400). Returns the
    post-write payload so the UI picks up resolved state without a
    follow-up GET.
    """
    from vexis_agent.core.yaml_config import _read_raw
    from vexis_agent.core.yaml_config_writer import (
        atomic_write_yaml,
        backup_if_commented,
    )

    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    current = _read_raw()
    proposed = dict(current)
    section = dict(proposed.get("computer_use") or {})

    # Top-level model / reasoning_level — same reset semantics as the
    # Voice tab's call_mode knobs.
    for key in ("model", "reasoning_level"):
        if key not in payload:
            continue
        coerced = _coerce_model_knob(payload[key], key)
        if coerced is None:
            section.pop(key, None)
        else:
            section[key] = coerced
    # Reasoning is meaningful only WITH a model — drop the orphan so we
    # never pass --effort to whatever account-default model is active.
    if "model" not in section and "reasoning_level" in section:
        section.pop("reasoning_level")

    if "dynamic" in payload:
        if not isinstance(payload["dynamic"], dict):
            raise ValueError("dynamic must be an object")
        dynamic = dict(section.get("dynamic") or {})
        dyn_in = payload["dynamic"]

        if "enabled" in dyn_in:
            if not isinstance(dyn_in["enabled"], bool):
                raise ValueError("dynamic.enabled must be a bool")
            dynamic["enabled"] = dyn_in["enabled"]

        for key in ("model", "reasoning_level"):
            if key not in dyn_in:
                continue
            coerced = _coerce_model_knob(dyn_in[key], f"dynamic.{key}")
            if coerced is None:
                dynamic.pop(key, None)
            else:
                dynamic[key] = coerced
        if "model" not in dynamic and "reasoning_level" in dynamic:
            dynamic.pop("reasoning_level")

        if "min_elements" in dyn_in:
            v = dyn_in["min_elements"]
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError("dynamic.min_elements must be an integer")
            if v < 1:
                raise ValueError("dynamic.min_elements must be >= 1")
            if v == DEFAULT_COMPUTER_USE_MIN_ELEMENTS:
                # Keep the YAML uncluttered — drop the key when it
                # matches the built-in default.
                dynamic.pop("min_elements", None)
            else:
                dynamic["min_elements"] = v

        if dynamic:
            section["dynamic"] = dynamic
        else:
            section.pop("dynamic", None)

    if section:
        proposed["computer_use"] = section
    else:
        proposed.pop("computer_use", None)

    cfg_path = vexis_dir() / "config.yaml"
    backup_path = backup_if_commented(cfg_path) if cfg_path.exists() else None
    atomic_write_yaml(cfg_path, proposed)

    result = computer_use_payload()
    result["ok"] = True
    result["backup_path"] = str(backup_path) if backup_path else None
    return result
