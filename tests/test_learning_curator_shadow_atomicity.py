"""Atomicity + lock + truncation-defense tests for
``core.learning_curator._append_to_shadow_file``.

Closes the TODO from commit 41fb2b4 — the observed 2026-05-03
truncation bug where MEMORY-SHADOW.md went from 28 entries (14k
bytes) to 1 entry (~750 bytes) mid-tick. These tests exercise:

  - the read-modify-write race directly (concurrent appenders),
  - the defensive size-shrunk guard for cause-(c) (a foreign writer
    or stale read inside the lock window),
  - the atomic-rename property under simulated kill,
  - lock release on exception.

The existing ``tests/test_learning_curator.py`` only stubs the
helper through a single-call ``_stub_review_fn``; this file is the
focused atomicity/concurrency suite the helper deserves.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from core.learning_curator import ENTRY_DELIMITER, _append_to_shadow_file


def _entries(text: str) -> list[str]:
    """Split a shadow file's body on the §-delimiter, dropping empty
    chunks. Used to count surviving entries after a concurrent test."""
    return [e for e in text.split(ENTRY_DELIMITER) if e.strip()]


# --------------------------------------------------------------------
# Concurrent appenders — pins the lock around the read-modify-write.
# --------------------------------------------------------------------


def test_concurrent_appenders_do_not_truncate(tmp_path: Path) -> None:
    """Two threads each append 50 distinct entries to the same shadow
    file. Final file must contain exactly 100 distinct entries.

    Without ``fcntl.flock`` the read-modify-write race causes lost
    updates: thread A reads, thread B reads (same stale state), A
    writes new_A, B's atomic replace overwrites A's. This is the
    same race shape that produced the 2026-05-03 truncation
    incident. The fix wraps read+write inside ``LOCK_EX``."""
    path = tmp_path / "MEMORY-SHADOW.md"
    iters = 50
    errors: list[BaseException] = []

    def worker(prefix: str) -> None:
        try:
            for i in range(iters):
                _append_to_shadow_file(path, f"[entry {prefix}-{i:03d}]")
        except BaseException as e:  # noqa: BLE001 — propagate to assert
            errors.append(e)

    t1 = threading.Thread(target=worker, args=("A",))
    t2 = threading.Thread(target=worker, args=("B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors, errors

    text = path.read_text(encoding="utf-8")
    entries = _entries(text)
    assert len(entries) == 2 * iters, (
        f"expected {2 * iters} entries, got {len(entries)} — "
        f"read-modify-write race fired and entries were lost"
    )
    # All distinct — no torn or duplicated content from overlapping writes.
    assert len(set(entries)) == 2 * iters, (
        "duplicate or torn entries detected — atomic-rename "
        "guarantee broken under contention"
    )


# --------------------------------------------------------------------
# Size-shrunk guard — defense-in-depth for cause-(c) from the TODO.
# --------------------------------------------------------------------


def test_size_shrunk_guard_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cause-(c) defense: if some unidentified writer truncated the
    file inside our lock window — or our ``read_text`` returned stale
    empty content — the post-rename file size is smaller than the
    pre-write size. The defensive guard turns silent data loss into
    a loud ``RuntimeError`` naming both the data path and the lock
    path so an operator sees the truncation immediately."""
    path = tmp_path / "MEMORY-SHADOW.md"
    for i in range(5):
        _append_to_shadow_file(path, f"[entry {i}]")
    pre_size = path.stat().st_size
    assert pre_size > 0

    # Simulate cause-(c): read_text returns empty for the shadow
    # file. The function computes a tiny ``new`` from the empty
    # ``existing`` and replaces the still-large on-disk file with
    # it. The size-shrunk guard must catch this.
    real_read_text = Path.read_text

    def fake_read_text(self: Path, *args, **kwargs) -> str:
        if self == path:
            return ""
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    with pytest.raises(RuntimeError) as exc:
        _append_to_shadow_file(path, "[new entry]")
    msg = str(exc.value)
    assert str(path) in msg, f"data path missing from error: {msg}"
    assert ".lock" in msg, f"lock path missing from error: {msg}"


# --------------------------------------------------------------------
# Atomic rename under simulated kill — pins the temp+rename property.
# --------------------------------------------------------------------


def test_atomic_rename_under_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If something goes wrong between tmp-write and os.replace —
    process killed, OS error mid-syscall, etc. — the on-disk file at
    ``path`` must still hold the OLD content (never torn, never
    empty). The replace either succeeds (new content lands) or
    fails (old content survives); no third state."""
    path = tmp_path / "MEMORY-SHADOW.md"
    for i in range(5):
        _append_to_shadow_file(path, f"[entry {i}]")
    pre_text = path.read_text(encoding="utf-8")
    pre_size = path.stat().st_size

    real_replace = os.replace

    def boom_replace(src, dst):  # type: ignore[no-untyped-def]
        if Path(dst) == path:
            raise OSError("simulated kill between tmp-write and replace")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", boom_replace)

    with pytest.raises(OSError):
        _append_to_shadow_file(path, "[would-be new entry]")

    # Old content survives untouched.
    assert path.read_text(encoding="utf-8") == pre_text
    assert path.stat().st_size == pre_size


# --------------------------------------------------------------------
# Lock release on exception — pins the finally-block lifecycle.
# --------------------------------------------------------------------


def test_lock_released_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception raised inside the lock window must still release
    the sidecar lock — otherwise a single bad write would deadlock
    every subsequent append for the remainder of the process
    lifetime. We force one failure, then verify the next append
    completes promptly (a hung lock would block on LOCK_EX
    indefinitely)."""
    path = tmp_path / "MEMORY-SHADOW.md"
    for i in range(5):
        _append_to_shadow_file(path, f"[entry {i}]")

    real_replace = os.replace
    fired = {"n": 0}

    def boom_once(src, dst):  # type: ignore[no-untyped-def]
        if Path(dst) == path and fired["n"] == 0:
            fired["n"] += 1
            raise OSError("forced one-shot failure inside lock window")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", boom_once)

    with pytest.raises(OSError):
        _append_to_shadow_file(path, "[trigger failure]")

    # The exception unwound through the finally block — lock should
    # be released. Subsequent append must not hang.
    done = threading.Event()
    err: list[BaseException] = []

    def follow_up() -> None:
        try:
            _append_to_shadow_file(path, "[follow-up]")
        except BaseException as e:  # noqa: BLE001
            err.append(e)
        finally:
            done.set()

    t = threading.Thread(target=follow_up)
    t.start()
    t.join(timeout=5.0)
    assert done.is_set(), (
        "follow-up append hung — lock was NOT released after the "
        "first call's exception"
    )
    assert not err, err
    assert "[follow-up]" in path.read_text(encoding="utf-8")
