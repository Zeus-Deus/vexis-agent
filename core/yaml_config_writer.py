"""Atomic writer for ``~/.vexis/config.yaml`` — Day 2 of model UX.

The existing ``core/yaml_config.py`` is read-only; nothing
programmatically wrote the user's config until Day 2's ``/model``
slash command landed. This module owns the write path:

  - ``has_comments(yaml_text)`` — comment-presence check shared
    with the dashboard's POST endpoint (Day 4). Single source of
    truth.
  - ``backup_if_commented(path)`` — comment-presence-gated backup
    helper. Self-managing: after the first edit comments are
    gone, so subsequent calls see no comments and skip backup,
    preserving the original .bak through daemon restarts.
    Documented at length in the research doc §5
    "Comment-preservation regret" — the in-memory-flag pattern
    actively destroys comments after a daemon restart; this is
    the right shape.
  - ``atomic_write_yaml(path, data)`` — fcntl.flock(LOCK_EX) +
    sidecar .lock + atomic temp-rename. Mirrors
    ``core.goal_state.GoalStore._mutate`` 1:1 in locking
    semantics; reads stay lock-free.

PyYAML's ``safe_dump`` strips comments (the comment-preservation
regret). v1 accepts that loss because the
backup-if-commented pre-step preserves the original; v2 may
swap to ruamel.yaml round-trip preservation if dogfood surfaces
the gap (per §5 research doc).

Design citation: ``.plans/model-management-ux-research.md`` §3
(atomic-write idiom from goal_state) + §5 (comment-preservation
two-pronged middle-ground) + §6 Day 2.
"""

from __future__ import annotations

import fcntl
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Comment detection — shared with the dashboard's POST endpoint
# ──────────────────────────────────────────────────────────────────


def has_comments(yaml_text: str) -> bool:
    """Return True if any line in ``yaml_text``, after
    whitespace-strip, starts with ``#``.

    The single source of truth for "does this config have YAML
    comments?" — used by the slash command's
    ``backup_if_commented`` AND by the dashboard's pre-save
    confirm modal (Day 4). Both surfaces import this helper; do
    not duplicate the detection logic.

    The check is intentionally simple. False-positive surface:
    a string value containing a literal ``#`` at column 0 of a
    line (e.g. a multiline literal block scalar). Acceptable for
    v1 — vexis configs are short and don't carry such strings.
    False-negative surface: an inline comment at the end of a
    value line (``models.brain: default  # foreground``); these
    aren't preserved by safe_dump either, so reporting "no
    comments" is honest.
    """
    if not isinstance(yaml_text, str):
        return False
    for line in yaml_text.splitlines():
        if line.strip().startswith("#"):
            return True
    return False


# ──────────────────────────────────────────────────────────────────
# Comment-presence-gated backup
# ──────────────────────────────────────────────────────────────────


def backup_if_commented(path: Path) -> Path | None:
    """If ``path`` exists AND has YAML comments, copy verbatim to
    ``<path>.bak`` and return the .bak path. Otherwise return
    ``None``.

    Self-managing across daemon restarts. After the first
    successful slash-command write, ``path`` no longer has
    comments (PyYAML stripped them), so this function returns
    ``None`` on subsequent calls and the original .bak from the
    first edit is preserved indefinitely. The user has to
    manually re-comment the file to opt back into the next-edit
    safety — that's a deliberate user action, not a daemon-
    restart accident.

    Why NOT an in-memory "have we backed up this session?" flag
    (the obvious-but-broken alternative): the flag resets on
    daemon restart. Sequence: edit → backup with comments →
    write comment-stripped config → restart → edit again → flag
    is False so re-back-up → overwrites the original .bak with
    a now-comment-stripped version → comments lost from BOTH
    files. The mitigation makes the regret worse than no backup
    at all. Comment-presence as the trigger is the right shape.

    Returns the .bak path on backup, ``None`` on skip (file
    missing or no comments). Logs at INFO level on backup.
    """
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning(
            "backup_if_commented: could not read %s (%s); skipping",
            path, exc,
        )
        return None
    if not has_comments(text):
        return None

    bak = path.with_suffix(path.suffix + ".bak")
    try:
        shutil.copy2(path, bak)
    except OSError as exc:
        log.warning(
            "backup_if_commented: copy %s -> %s failed (%s); proceeding without backup",
            path, bak, exc,
        )
        return None

    log.info(
        "backup_if_commented: backed up commented config %s -> %s "
        "(future edits won't re-back-up until comments are re-added)",
        path, bak,
    )
    return bak


# ──────────────────────────────────────────────────────────────────
# Atomic YAML write — fcntl.flock + temp-rename
# ──────────────────────────────────────────────────────────────────


def atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as YAML to ``path`` atomically under exclusive
    lock.

    Mirrors ``core.goal_state.GoalStore._mutate`` 1:1:

      1. Ensure parent directory exists.
      2. Open sidecar ``<path>.lock`` for fcntl.flock(LOCK_EX).
      3. Serialise ``data`` via ``yaml.safe_dump`` to a temp file
         next to ``path`` (same filesystem so rename is atomic).
      4. fsync the temp file's contents.
      5. ``os.replace(tmp, path)`` — atomic.
      6. Release lock.

    Reads (the existing ``yaml_config._read_raw`` path) stay
    lock-free. The atomic rename guarantees a consistent snapshot
    for any reader open during the write.

    Comment loss: ``yaml.safe_dump`` doesn't preserve comments.
    Callers should run :func:`backup_if_commented` BEFORE this
    function on the user's behalf. See module docstring for the
    full middle-ground design.

    ``sort_keys=False`` to preserve user-meaningful ordering;
    ``default_flow_style=False`` for the standard nested-block
    layout users expect.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                data,
                fh,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


__all__ = [
    "atomic_write_yaml",
    "backup_if_commented",
    "has_comments",
]
