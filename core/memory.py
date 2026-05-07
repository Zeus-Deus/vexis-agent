"""Persistent memory: MEMORY.md (agent notes) + USER.md (user profile).

Two files, both `§`-delimited, both injected into the brain's system
prompt. Hard char caps (2200 / 1375 by default) keep the always-on
token tax bounded.

Concurrency model — load-bearing detail:

  * Mutations acquire ``fcntl.flock`` on a SIDECAR ``.lock`` file
    (e.g. ``MEMORY.md.lock``), re-read MEMORY.md from disk under the
    lock, mutate in-memory, write atomically via temp+rename, release.
    The lock is sidecar (not on MEMORY.md itself) precisely so the
    real file can be atomically replaced — you can't atomically
    replace a file you're holding a write-lock on.

  * Reads do NOT lock. Atomic rename means a reader either sees the
    old file or the new file — never a partial write.

  * Counter-style writes (skill telemetry) intentionally don't lock.
    Memory IS correctness-critical (lost entries are user-visible);
    telemetry is statistical. See core/skills.py for the other side
    of this two-tier model.

Threat scanning runs on every ``add`` and the new content of every
``replace``. Memory entries land in the system prompt of every future
session — a single injection payload that lands here is permanent.
The pattern list is small and biased toward jailbreak phrases, lifted
verbatim from Hermes (12 regex + invisible-unicode guard).

Frozen-snapshot pattern (in core/brain/claude_code.py):
At session start the brain captures ``format_for_system_prompt(...)``
once and reuses it for every turn of that session. Mid-session
``add`` / ``replace`` / ``remove`` mutate disk + return live state in
the tool response, but the system-prompt block stays byte-identical
for the whole session — preserves Anthropic's prefix cache. The
trade-off (model can't see its own writes re-injected mid-session) is
documented in CAPABILITIES.md so the model knows.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.yaml_config import memory_char_limit, user_char_limit

log = logging.getLogger(__name__)

Target = Literal["memory", "user"]
Action = Literal["add", "replace", "remove"]

# Box-drawing double horizontal line × 46. Used as the visual fence
# around each memory block so the model's eye snaps to it.
_SEPARATOR = "═" * 46

# Section sign. Never appears in normal English text — clean
# delimiter, easy to grep, survives copy-paste.
ENTRY_DELIMITER = "\n§\n"

# 12 patterns lifted from Hermes verbatim (see /tmp/hermes-research-v2.md
# Part A2e). These catch low-effort jailbreak / exfil payloads in
# memory writes. High-effort attacks should be defended elsewhere; this
# is cheap insurance against the most common failure mode.
_THREAT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ignore\s+(previous|all|above|prior)\s+instructions", re.I), "prompt_injection"),
    (re.compile(r"you\s+are\s+now\s+", re.I), "role_hijack"),
    (re.compile(r"do\s+not\s+tell\s+the\s+user", re.I), "deception_hide"),
    (re.compile(r"system\s+prompt\s+override", re.I), "sys_prompt_override"),
    (re.compile(r"disregard\s+(your|all|any)\s+(instructions|rules|guidelines)", re.I), "disregard_rules"),
    (re.compile(r"act\s+as\s+(if|though)\s+you\s+(have\s+no|don't\s+have)\s+(restrictions|limits|rules)", re.I), "bypass_restrictions"),
    (re.compile(r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", re.I), "exfil_curl"),
    (re.compile(r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", re.I), "exfil_wget"),
    (re.compile(r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)", re.I), "read_secrets"),
    (re.compile(r"authorized_keys", re.I), "ssh_backdoor"),
    (re.compile(r"\$HOME/\.ssh|~/\.ssh", re.I), "ssh_access"),
    (re.compile(r"\$HOME/\.vexis/\.env|~/\.vexis/\.env", re.I), "vexis_env"),
)

# Zero-width / direction-control unicode that's invisible in most
# editors but still tokenized by the model. Reject outright.
_INVISIBLE_CHARS: frozenset[str] = frozenset(
    [
        "​",  # zero-width space
        "‌",  # zero-width non-joiner
        "‍",  # zero-width joiner
        "⁠",  # word joiner
        "﻿",  # zero-width no-break space (BOM)
        "‪",  # left-to-right embedding
        "‫",  # right-to-left embedding
        "‬",  # pop directional formatting
        "‭",  # left-to-right override
        "‮",  # right-to-left override
    ]
)


def _scan_for_threats(content: str, *, target: str | None = None) -> str | None:
    """Return a refusal reason if content looks malicious; None otherwise.

    ``target`` selects whether the USER.md-specific extension fires:
      - ``None`` (default) or ``"memory"``: base 12 patterns +
        invisible-unicode only. Used for MEMORY.md writes.
      - ``"user"``: base set PLUS the religion/politics/sexuality/
        self-harm/third-party patterns from
        ``core.identity_threat``. Used for USER.md writes — these
        patterns fire here (not just inside the curator's
        ``_validate_lesson``) so non-curator paths (migration script,
        future hand-CLI, anything else calling
        ``MemoryStore.add(target='user')``) get the same coverage.
    """
    for ch in content:
        if ch in _INVISIBLE_CHARS:
            return (
                f"content contains invisible unicode character "
                f"U+{ord(ch):04X} (possible injection)"
            )
    for pattern, pid in _THREAT_PATTERNS:
        if pattern.search(content):
            return f"content matches threat pattern '{pid}'"
    if target == "user":
        # Local import keeps memory.py importable without dragging in
        # the identity-threat patterns when only MEMORY.md is in play.
        from core.identity_threat import scan_user_identity_content
        user_pid = scan_user_identity_content(content)
        if user_pid:
            return f"content matches USER.md threat pattern '{user_pid}'"
    return None


@dataclass(frozen=True)
class MemoryError_:
    """Structured error returned to the CLI/tool layer."""

    message: str
    extra: dict | None = None


@dataclass(frozen=True)
class MemorySuccess:
    """Structured success returned to the CLI/tool layer.

    The render is the live MEMORY/USER block AFTER the mutation —
    the model sees its write reflected immediately in the tool
    response, even though the system-prompt snapshot stays frozen
    for the rest of the session.
    """

    message: str
    render: str


def _read_entries(path: Path) -> list[str]:
    """Parse the §-delimited file at ``path``. Missing file → empty list.

    Splits on ENTRY_DELIMITER, strips each entry, drops empties,
    and dedups (preserving first-seen order) — a defense against a
    file that got hand-edited to add a duplicate.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    parts = [p.strip() for p in raw.split(ENTRY_DELIMITER)]
    seen: dict[str, None] = {}
    for part in parts:
        if part:
            seen.setdefault(part, None)
    return list(seen.keys())


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via temp+rename.

    The temp file lives in the same directory so ``os.replace`` is an
    atomic rename (cross-filesystem rename would fall through to a
    copy and lose atomicity). On failure the temp file is best-effort
    cleaned up.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class _FileLock:
    """fcntl.flock-backed exclusive lock on a sidecar file.

    The sidecar is created lazily; if an OS error prevents acquiring
    the lock we raise — the alternative (proceeding unlocked) would
    silently corrupt MEMORY.md under concurrent writes.
    """

    def __init__(self, sidecar: Path) -> None:
        self._sidecar = sidecar
        self._fd: int | None = None

    def __enter__(self) -> _FileLock:
        self._sidecar.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(
            str(self._sidecar), os.O_CREAT | os.O_RDWR, 0o600
        )
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


class MemoryStore:
    """Owns MEMORY.md and USER.md inside ``<workspace>/memories/``.

    The store holds NO mid-session state — every operation re-reads
    from disk under the lock. The "frozen snapshot" pattern lives in
    the brain layer: it asks for ``format_for_system_prompt`` once at
    session start and caches the string until the session UUID rotates.
    Mutations don't even attempt to invalidate that cache; they just
    update disk and let the brain's per-session cache age out.
    """

    def __init__(
        self,
        memories_dir: Path,
        *,
        memory_limit: int | None = None,
        user_limit: int | None = None,
    ) -> None:
        self._dir = memories_dir
        self._memory_limit = (
            memory_limit if memory_limit is not None else memory_char_limit()
        )
        self._user_limit = (
            user_limit if user_limit is not None else user_char_limit()
        )

    def _path(self, target: Target) -> Path:
        return self._dir / ("USER.md" if target == "user" else "MEMORY.md")

    def _lock_for(self, target: Target) -> _FileLock:
        return _FileLock(self._dir / f"{self._path(target).name}.lock")

    def _limit(self, target: Target) -> int:
        return self._user_limit if target == "user" else self._memory_limit

    # ---------- public API ----------

    def list_entries(self, target: Target) -> list[str]:
        """Live entries on disk for ``target``. No lock — atomic rename
        means readers see either old or new state, never a tear."""
        return _read_entries(self._path(target))

    def render(self, target: Target) -> str:
        """Build the system-prompt block for ``target``. Empty when no
        entries — the caller drops empty blocks to avoid a header with
        no body."""
        entries = self.list_entries(target)
        return self._render_block(target, entries)

    def format_for_system_prompt(self, target: Target) -> str | None:
        """Same as ``render`` but returns None for the empty case so
        the caller can use ``if block:`` to skip injection."""
        block = self.render(target)
        return block if block else None

    def add(self, target: str, content: str) -> MemorySuccess | MemoryError_:
        # ``target`` accepts the wider str type (not just Target) so
        # we can intercept the special-case "relationships" value
        # below. Existing callers continue to pass ``"memory"`` /
        # ``"user"`` and behavior is unchanged for those.
        if target == "relationships":
            # v3b: writes to RELATIONSHIPS.md require an explicit
            # ConsentToken and are routed through
            # ``core/relationships/store.py`` ``RelationshipsStore``,
            # NOT through MemoryStore. The bullet-text content shape
            # MemoryStore enforces doesn't fit the H2-per-person
            # YAML+facts shape relationships uses, and the
            # token-gated bypass at ``core/learning_review.py`` only
            # runs at the RelationshipsStore call site. Fail fast
            # rather than risk a writer accidentally bypassing
            # consent by calling the wrong API.
            raise PermissionError(
                "MemoryStore does not write to RELATIONSHIPS.md. "
                "Relationships writes require an explicit ConsentToken; "
                "use core.relationships.store.RelationshipsStore "
                "(staged via core.relationships.curator.RelationshipsCurator) "
                "instead."
            )
        content = content.strip()
        if not content:
            return MemoryError_("content is empty")
        threat = _scan_for_threats(content, target=target)
        if threat:
            return MemoryError_(
                f"Blocked: {threat}. Memory entries are injected into the "
                f"system prompt and must not contain injection or "
                f"exfiltration payloads."
            )

        with self._lock_for(target):
            entries = _read_entries(self._path(target))
            if content in entries:
                # Hermes pattern: success, not error. Model sees "yes
                # that's saved" and moves on — duplicates are a no-op,
                # not a programming error.
                return MemorySuccess(
                    "Entry already exists (no duplicate added).",
                    self._render_block(target, entries),
                )
            new_entries = entries + [content]
            new_total = self._content_length(new_entries)
            limit = self._limit(target)
            if new_total > limit:
                current = self._content_length(entries)
                return MemoryError_(
                    f"Memory at {current:,}/{limit:,} chars. "
                    f"Adding this entry ({len(content)} chars) would exceed "
                    f"the limit. Replace or remove existing entries first.",
                    extra={
                        "current_entries": entries,
                        "usage": f"{current:,}/{limit:,}",
                    },
                )
            self._write_entries(target, new_entries)
            return MemorySuccess(
                f"Added entry to {target}.",
                self._render_block(target, new_entries),
            )

    def ensure_seed(
        self, target: Target, *, marker: str, content: str,
    ) -> bool:
        """Idempotent seed: write ``content`` as a new entry only
        if no existing entry contains ``marker``. Returns True iff
        the seed was added.

        Used by daemon startup to install meta-system context (e.g.
        v3c's "Vexis silently captures third-party relationship
        facts ..." line) directly into USER.md without going
        through the candidate queue. The marker is the dedup key:
        future daemon starts that find the marker substring in any
        existing entry skip the install.

        Skips threat-scanning and char-limit checks because the
        caller is the daemon itself, not a model output. The
        content is hand-authored docs-style text; the threat
        scanner's medical/legal/financial regexes would never fire
        on it, and the char-limit is the user's quota — a
        single-line seed shouldn't fail it. If a real production
        case ever hits the limit here, swap to ``add`` for the
        full validation path.
        """
        if not marker.strip():
            raise ValueError("ensure_seed: marker must be non-empty")
        if not content.strip():
            raise ValueError("ensure_seed: content must be non-empty")
        with self._lock_for(target):
            entries = _read_entries(self._path(target))
            if any(marker in entry for entry in entries):
                return False
            new_entries = entries + [content.strip()]
            self._write_entries(target, new_entries)
            return True

    def replace(
        self, target: Target, old_text: str, content: str
    ) -> MemorySuccess | MemoryError_:
        old_text = old_text.strip()
        content = content.strip()
        if not old_text:
            return MemoryError_("old_text is empty")
        if not content:
            return MemoryError_("content is empty")
        threat = _scan_for_threats(content, target=target)
        if threat:
            return MemoryError_(
                f"Blocked: {threat}. Memory entries are injected into the "
                f"system prompt and must not contain injection or "
                f"exfiltration payloads."
            )

        with self._lock_for(target):
            entries = _read_entries(self._path(target))
            indexes = self._match_indexes(entries, old_text)
            if not indexes:
                return MemoryError_(f"No entry matched '{old_text}'.")
            if len(indexes) > 1:
                # If every match is byte-identical, treat as silent
                # dedup helper and operate on the first. Otherwise
                # reject — let the model disambiguate.
                texts = {entries[i] for i in indexes}
                if len(texts) > 1:
                    previews = [_preview(entries[i]) for i in indexes]
                    return MemoryError_(
                        f"Multiple entries matched '{old_text}'. Be more "
                        f"specific.",
                        extra={"matches": previews},
                    )
            target_idx = indexes[0]
            new_entries = list(entries)
            new_entries[target_idx] = content
            # Dedup: a replace can collapse two distinct entries into
            # one if the new content matches an existing entry.
            new_entries = list(dict.fromkeys(new_entries))
            new_total = self._content_length(new_entries)
            limit = self._limit(target)
            if new_total > limit:
                current = self._content_length(entries)
                return MemoryError_(
                    f"Memory at {current:,}/{limit:,} chars. "
                    f"Replacing would push it to {new_total:,}. "
                    f"Remove or shorten other entries first.",
                    extra={
                        "current_entries": entries,
                        "usage": f"{current:,}/{limit:,}",
                    },
                )
            self._write_entries(target, new_entries)
            return MemorySuccess(
                f"Replaced entry in {target}.",
                self._render_block(target, new_entries),
            )

    def remove(
        self, target: Target, old_text: str
    ) -> MemorySuccess | MemoryError_:
        old_text = old_text.strip()
        if not old_text:
            return MemoryError_("old_text is empty")

        with self._lock_for(target):
            entries = _read_entries(self._path(target))
            indexes = self._match_indexes(entries, old_text)
            if not indexes:
                return MemoryError_(f"No entry matched '{old_text}'.")
            if len(indexes) > 1:
                texts = {entries[i] for i in indexes}
                if len(texts) > 1:
                    previews = [_preview(entries[i]) for i in indexes]
                    return MemoryError_(
                        f"Multiple entries matched '{old_text}'. Be more "
                        f"specific.",
                        extra={"matches": previews},
                    )
            target_idx = indexes[0]
            new_entries = list(entries)
            del new_entries[target_idx]
            self._write_entries(target, new_entries)
            return MemorySuccess(
                f"Removed entry from {target}.",
                self._render_block(target, new_entries),
            )

    # ---------- internals ----------

    @staticmethod
    def _match_indexes(entries: list[str], old_text: str) -> list[int]:
        return [i for i, e in enumerate(entries) if old_text in e]

    @staticmethod
    def _content_length(entries: list[str]) -> int:
        if not entries:
            return 0
        return len(ENTRY_DELIMITER.join(entries))

    def _render_block(self, target: Target, entries: list[str]) -> str:
        if not entries:
            return ""
        limit = self._limit(target)
        body = ENTRY_DELIMITER.join(entries)
        current = len(body)
        # Floor at 1% so a non-empty memory never reads as 0%. Cap at
        # 100 just in case a future config tweak pushes us over.
        pct = max(1, min(100, int((current / limit) * 100))) if limit > 0 else 0
        if target == "user":
            header = (
                f"USER PROFILE (who the user is) "
                f"[{pct}% — {current:,}/{limit:,} chars]"
            )
        else:
            header = (
                f"MEMORY (your personal notes) "
                f"[{pct}% — {current:,}/{limit:,} chars]"
            )
        return f"{_SEPARATOR}\n{header}\n{_SEPARATOR}\n{body}"

    def _write_entries(self, target: Target, entries: list[str]) -> None:
        path = self._path(target)
        if not entries:
            # Empty list → empty file (don't delete; the file is the
            # canonical record). Reading back will yield [].
            _atomic_write(path, "")
            return
        _atomic_write(path, ENTRY_DELIMITER.join(entries) + "\n")


def _preview(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
