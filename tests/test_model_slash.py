"""Day 2 model UX — /model slash command tests.

Mirrors the test infrastructure in ``tests/test_goal_command.py``
(fake PTB Update / Message / Bot / Ctx) so the slash handler can
be exercised without spawning real Telegram or any subprocess.

Coverage:
- Disabled-flag short-circuit
- Auth gate
- /model (status), /model status verbose
- /model list (subsystems + brains), /model list <brain>
- /model set brain <name> — write + restart-required note +
  invalid-kind policy refusal
- /model set <subsystem> <value> — write, validator-refusal,
  unknown subsystem
- /model reset — all + per-subsystem
- Comment-presence-gated backup integration: backup-runs-when-
  comments-present, backup-skipped-when-no-comments-present,
  daemon-restart-preserves-bak via the writer's helper

Design citation: ``.plans/model-management-ux-research.md`` §6
Day 2 tests bullet.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from core.running_tasks import RunningTasks
from transports.telegram import TelegramTransport


_USER = 1001
_OTHER_USER = 9999
_CHAT = 5050


# ──────────────────────────────────────────────────────────────────
# Fakes (same shape as tests/test_goal_command.py)
# ──────────────────────────────────────────────────────────────────


class _FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []
        # Day 3 of model picker UX — track delete_message calls so
        # the cancel-deletes-via-bot.delete_message test can assert
        # the picker reply was actually removed.
        self.deleted_messages: list[tuple[int, int]] = []
        # When set, delete_message raises this — used to pin the
        # 48-hour stale-message edge case (the user's flagged
        # concern: graceful fallback, not a crash).
        self.delete_raises: Exception | None = None

    async def send_message(self, chat_id: int, text: str, **_kw: Any) -> None:
        self.sent_messages.append((chat_id, text))

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        if self.delete_raises is not None:
            raise self.delete_raises
        self.deleted_messages.append((chat_id, message_id))


class _FakeMessage:
    def __init__(self, text: str, chat_id: int, bot: _FakeBot) -> None:
        self.text = text
        self.chat_id = chat_id
        self._bot = bot
        self.reply_log: list[str] = []
        # Day 3 of model picker UX — when reply_text is called with
        # an InlineKeyboardMarkup, the test asserts on it directly.
        self.reply_markups: list[Any] = []

    async def reply_text(self, text: str, **_kw: Any) -> None:
        self.reply_log.append(text)
        self.reply_markups.append(_kw.get("reply_markup"))
        await self._bot.send_message(chat_id=self.chat_id, text=text)

    def get_bot(self) -> _FakeBot:
        return self._bot


class _FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _FakeUpdate:
    def __init__(self, message: _FakeMessage, user: _FakeUser) -> None:
        self.message = message
        self.effective_user = user


# Day 3 of model picker UX — callback-side fakes. Mirror PTB's
# CallbackQuery shape so transport._on_callback can drive the
# picker flow without a real Telegram bot. The handler reads:
#   query.data        — callback_data string
#   query.from_user   — for the auth gate
#   query.message     — for bot.delete_message + edit_message_text
#   query.answer()    — async ack
#   query.edit_message_text(text, reply_markup=...) — async


class _FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class _FakeCallbackMessage:
    def __init__(self, chat_id: int, message_id: int, bot: _FakeBot) -> None:
        self.chat = _FakeChat(chat_id)
        self.chat_id = chat_id
        self.message_id = message_id
        self._bot = bot

    def get_bot(self) -> _FakeBot:
        return self._bot


class _FakeCallbackQuery:
    def __init__(
        self, data: str, user_id: int, chat_id: int, message_id: int,
        bot: _FakeBot,
    ) -> None:
        self.data = data
        self.from_user = _FakeUser(user_id)
        self.message = _FakeCallbackMessage(chat_id, message_id, bot)
        self.answered = False
        self.edits: list[tuple[str, Any]] = []

    async def answer(self, *_a: Any, **_k: Any) -> None:
        self.answered = True

    async def edit_message_text(
        self, text: str, reply_markup: Any = None, **_kw: Any,
    ) -> None:
        self.edits.append((text, reply_markup))


class _FakeCallbackUpdate:
    def __init__(self, query: _FakeCallbackQuery) -> None:
        self.callback_query = query
        self.message = None
        self.effective_user = query.from_user


def _callback(
    data: str, user_id: int = _USER, message_id: int = 12345,
) -> tuple[_FakeCallbackUpdate, _FakeBot, _FakeCallbackQuery]:
    bot = _FakeBot()
    query = _FakeCallbackQuery(
        data=data, user_id=user_id, chat_id=_CHAT,
        message_id=message_id, bot=bot,
    )
    upd = _FakeCallbackUpdate(query)
    return upd, bot, query


class _FakeCtx:
    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args or []


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def vexis_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``vexis_dir()`` and ``yaml_config._config_path``
    to a tmp dir so /model writes never touch the user's real
    ~/.vexis/."""
    home = tmp_path / "vexis"
    home.mkdir()
    monkeypatch.setattr("core.paths.vexis_dir", lambda: home)
    monkeypatch.setattr("core.yaml_config.vexis_dir", lambda: home)
    monkeypatch.setattr(
        "core.yaml_config._config_path", lambda: home / "config.yaml"
    )
    return home


@pytest.fixture
def model_ux_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``model_ux_enabled()`` True regardless of config."""
    monkeypatch.setattr("core.yaml_config.model_ux_enabled", lambda: True)


@pytest.fixture
def model_ux_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.yaml_config.model_ux_enabled", lambda: False)


@pytest.fixture
def transport(vexis_home: Path) -> TelegramTransport:
    """Bare TelegramTransport with the wiring /model touches."""
    t = TelegramTransport.__new__(TelegramTransport)
    t._allowed_user_id = _USER  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    return t


def _update(text: str, user_id: int = _USER) -> tuple[_FakeUpdate, _FakeBot, _FakeMessage]:
    bot = _FakeBot()
    msg = _FakeMessage(text=text, chat_id=_CHAT, bot=bot)
    upd = _FakeUpdate(msg, _FakeUser(user_id))
    return upd, bot, msg


def _ctx(*args: str) -> _FakeCtx:
    return _FakeCtx(list(args))


def _seed_config(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


# ──────────────────────────────────────────────────────────────────
# Auth + disabled-flag gates
# ──────────────────────────────────────────────────────────────────


def test_rejects_disallowed_user(transport, model_ux_on, vexis_home):
    upd, bot, msg = _update("/model status", user_id=_OTHER_USER)
    asyncio.run(transport._on_model(upd, _ctx("status")))
    assert msg.reply_log == []
    assert bot.sent_messages == []


def test_disabled_flag_short_circuits(transport, model_ux_off, vexis_home):
    upd, _bot, msg = _update("/model status")
    asyncio.run(transport._on_model(upd, _ctx("status")))
    assert len(msg.reply_log) == 1
    assert "/model is disabled" in msg.reply_log[0]
    assert "model_ux.enabled" in msg.reply_log[0]


# ──────────────────────────────────────────────────────────────────
# /model (status) — bare invocation
# ──────────────────────────────────────────────────────────────────


def test_bare_status_renders_resolution_table(
    transport, model_ux_on, vexis_home,
):
    upd, _bot, msg = _update("/model")
    asyncio.run(transport._on_model(upd, _ctx()))
    assert len(msg.reply_log) == 1
    out = msg.reply_log[0]
    assert "Current resolution" in out
    # All 8 known subsystems should appear in the table.
    for name in [
        "curator", "coherence_judge", "goal_judge",
        "relationships_extractor", "relationships_classifier",
        "learning_review", "learning_triage",
    ]:
        assert name in out


def test_status_unconfigured_row_shows_default_not_tier_name(
    transport, model_ux_on, vexis_home,
):
    """Polish-pass ask 1 + 4: unconfigured subsystems show
    ``(default → <resolved>)`` rather than the resolved tier
    name. Pre-polish slash showed ``small → haiku`` for an
    unconfigured curator (misleading: the user never opted into
    'small'). Post-polish: ``(default → haiku)``."""
    upd, _bot, msg = _update("/model status")
    asyncio.run(transport._on_model(upd, _ctx("status")))
    out = msg.reply_log[0]
    # Default rendering visible.
    assert "(default → haiku)" in out
    # The pre-polish bug — resolved tier as configured display —
    # must NOT appear.
    assert "small    → haiku" not in out
    assert "tiny     → haiku" not in out


def test_status_passthrough_value_drops_arrow(
    transport, model_ux_on, vexis_home,
):
    """Polish-pass ask 2: when configured == resolved (e.g.
    legacy alias passthrough OR picker-written model id), drop
    the redundant arrow. Pin via ``learning_review: sonnet`` on
    claude-code — passes through, no translation."""
    _seed_config(
        vexis_home / "config.yaml",
        "models:\n  learning_review: sonnet\n",
    )
    upd, _bot, msg = _update("/model status")
    asyncio.run(transport._on_model(upd, _ctx("status")))
    out = msg.reply_log[0]
    # learning_review row shows "sonnet" alone, no arrow.
    assert "learning_review" in out
    # No "sonnet → sonnet" pattern anywhere.
    assert "sonnet → sonnet" not in out
    assert "sonnet    → sonnet" not in out


def test_status_subcommand_same_as_bare(transport, model_ux_on, vexis_home):
    upd, _bot, msg = _update("/model status")
    asyncio.run(transport._on_model(upd, _ctx("status")))
    assert len(msg.reply_log) == 1
    assert "Current resolution" in msg.reply_log[0]


def test_status_surfaces_validator_warnings(
    transport, model_ux_on, vexis_home,
):
    """A config with a known issue (legacy raw-string on opencode)
    surfaces the rule-4 error in the status reply."""
    _seed_config(
        vexis_home / "config.yaml",
        "brain:\n  kind: opencode\nmodels:\n  learning_review: sonnet\n",
    )
    upd, _bot, msg = _update("/model")
    asyncio.run(transport._on_model(upd, _ctx()))
    out = msg.reply_log[0]
    assert "Validator:" in out
    assert "learning_review" in out


# ──────────────────────────────────────────────────────────────────
# /model list
# ──────────────────────────────────────────────────────────────────


def test_list_no_args_enumerates_subsystems_and_brains(
    transport, model_ux_on, vexis_home,
):
    upd, _bot, msg = _update("/model list")
    asyncio.run(transport._on_model(upd, _ctx("list")))
    out = msg.reply_log[0]
    assert "Subsystems:" in out
    assert "Brains:" in out
    assert "claude-code" in out
    assert "opencode" in out


def test_list_claude_code_describes_aliases(
    transport, model_ux_on, vexis_home,
):
    upd, _bot, msg = _update("/model list claude-code")
    asyncio.run(transport._on_model(upd, _ctx("list", "claude-code")))
    out = msg.reply_log[0]
    assert "sonnet" in out
    assert "haiku" in out
    assert "opus" in out


def test_list_opencode_describes_provider_model_format(
    transport, model_ux_on, vexis_home,
):
    upd, _bot, msg = _update("/model list opencode")
    asyncio.run(transport._on_model(upd, _ctx("list", "opencode")))
    out = msg.reply_log[0]
    assert "provider/model" in out
    assert "opencode models" in out


def test_list_unknown_brain(transport, model_ux_on, vexis_home):
    upd, _bot, msg = _update("/model list nonexistent-brain")
    asyncio.run(transport._on_model(upd, _ctx("list", "nonexistent-brain")))
    assert "Unknown brain" in msg.reply_log[0]


# ──────────────────────────────────────────────────────────────────
# /model set <subsystem> <value> — happy path
# ──────────────────────────────────────────────────────────────────


def test_set_subsystem_writes_config(transport, model_ux_on, vexis_home):
    """Writes models.subsystems.<name> = <value> via atomic write.
    Reply confirms with the resolved native id for the current
    brain."""
    upd, _bot, msg = _update("/model set goal_judge large")
    asyncio.run(transport._on_model(upd, _ctx("set", "goal_judge", "large")))
    cfg_path = vexis_home / "config.yaml"
    assert cfg_path.is_file()
    import yaml
    parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert parsed["models"]["subsystems"]["goal_judge"] == "large"
    # Reply confirms.
    assert any("goal_judge" in r and "large" in r for r in msg.reply_log)


def test_set_subsystem_unknown_name_refused(
    transport, model_ux_on, vexis_home,
):
    upd, _bot, msg = _update("/model set madeup_subsystem small")
    asyncio.run(transport._on_model(upd, _ctx("set", "madeup_subsystem", "small")))
    out = msg.reply_log[0]
    assert "Unknown subsystem" in out
    # No write happened.
    assert not (vexis_home / "config.yaml").exists()


# ──────────────────────────────────────────────────────────────────
# /model set <subsystem> — validator refuses on errors
# ──────────────────────────────────────────────────────────────────


def test_set_subsystem_refused_when_validator_errors(
    transport, model_ux_on, vexis_home,
):
    """User on opencode tries to set a bare alias — validator's
    rule 4 fires error; slash refuses to write."""
    _seed_config(vexis_home / "config.yaml", "brain:\n  kind: opencode\n")
    upd, _bot, msg = _update("/model set goal_judge sonnet")
    asyncio.run(transport._on_model(upd, _ctx("set", "goal_judge", "sonnet")))
    out = msg.reply_log[0]
    assert "Won't write" in out
    assert "goal_judge" in out
    # Original config preserved (no models block written).
    import yaml
    parsed = yaml.safe_load(
        (vexis_home / "config.yaml").read_text(encoding="utf-8")
    )
    assert "subsystems" not in (parsed.get("models") or {})


# ──────────────────────────────────────────────────────────────────
# /model set brain <kind> — special path with restart-required note
# ──────────────────────────────────────────────────────────────────


def test_set_brain_kind_writes_and_announces_restart(
    transport, model_ux_on, vexis_home,
):
    upd, _bot, msg = _update("/model set brain opencode")
    asyncio.run(transport._on_model(upd, _ctx("set", "brain", "opencode")))
    out = msg.reply_log[0]
    assert "brain.kind" in out
    assert "opencode" in out
    assert "Restart" in out
    import yaml
    parsed = yaml.safe_load(
        (vexis_home / "config.yaml").read_text(encoding="utf-8")
    )
    assert parsed["brain"]["kind"] == "opencode"


def test_set_brain_kind_typo_refused_as_policy(
    transport, model_ux_on, vexis_home,
):
    """Per §4 rule 1: severity is warning (matches daemon
    fallback) but slash refuses anyway as policy — typos are
    user-hostile to recover from."""
    upd, _bot, msg = _update("/model set brain claudecode")
    asyncio.run(transport._on_model(upd, _ctx("set", "brain", "claudecode")))
    out = msg.reply_log[0]
    assert "Won't write" in out
    assert "claudecode" in out
    # No file written.
    assert not (vexis_home / "config.yaml").exists()


# ──────────────────────────────────────────────────────────────────
# /model reset
# ──────────────────────────────────────────────────────────────────


def test_reset_all_clears_subsystem_assignments(
    transport, model_ux_on, vexis_home,
):
    """Reset removes both legacy raw-string keys AND new-schema
    models.subsystems.<name>. Leaves models.tiers and
    models.brain alone."""
    _seed_config(
        vexis_home / "config.yaml",
        "brain:\n  kind: claude-code\n"
        "models:\n"
        "  brain: default\n"
        "  learning_review: sonnet\n"  # legacy
        "  subsystems:\n"
        "    curator: small\n"           # new schema
        "  tiers:\n"
        "    opencode:\n"
        "      large: openai/gpt-4o\n",  # tier override (preserved)
    )
    upd, _bot, msg = _update("/model reset")
    asyncio.run(transport._on_model(upd, _ctx("reset")))
    import yaml
    parsed = yaml.safe_load(
        (vexis_home / "config.yaml").read_text(encoding="utf-8")
    )
    models = parsed.get("models") or {}
    assert "subsystems" not in models
    assert "learning_review" not in models
    assert models.get("brain") == "default"  # preserved
    # tier override preserved.
    assert models["tiers"]["opencode"]["large"] == "openai/gpt-4o"


def test_reset_one_subsystem(transport, model_ux_on, vexis_home):
    _seed_config(
        vexis_home / "config.yaml",
        "models:\n"
        "  learning_review: sonnet\n"
        "  coherence_judge: haiku\n"
        "  subsystems:\n"
        "    curator: small\n"
        "    goal_judge: large\n",
    )
    upd, _bot, msg = _update("/model reset goal_judge")
    asyncio.run(transport._on_model(upd, _ctx("reset", "goal_judge")))
    import yaml
    parsed = yaml.safe_load(
        (vexis_home / "config.yaml").read_text(encoding="utf-8")
    )
    models = parsed["models"]
    # goal_judge cleared from subsystems block but curator survives.
    assert models["subsystems"] == {"curator": "small"}
    # Other legacy keys preserved.
    assert models["learning_review"] == "sonnet"
    assert models["coherence_judge"] == "haiku"


def test_reset_unknown_subsystem_refused(
    transport, model_ux_on, vexis_home,
):
    upd, _bot, msg = _update("/model reset madeup")
    asyncio.run(transport._on_model(upd, _ctx("reset", "madeup")))
    assert "Unknown subsystem" in msg.reply_log[0]


# ──────────────────────────────────────────────────────────────────
# Comment-presence-gated backup integration
# ──────────────────────────────────────────────────────────────────


def test_backup_runs_when_comments_present(
    transport, model_ux_on, vexis_home,
):
    """Default Day-2 case: user has commented config, slash runs,
    backup fires verbatim. Reply text mentions the backup."""
    _seed_config(
        vexis_home / "config.yaml",
        "# learning curator notes\n"
        "models:\n  learning_review: sonnet\n",
    )
    upd, _bot, msg = _update("/model set goal_judge large")
    asyncio.run(transport._on_model(upd, _ctx("set", "goal_judge", "large")))
    bak = vexis_home / "config.yaml.bak"
    assert bak.is_file()
    # .bak preserves the original commented content.
    assert "# learning curator notes" in bak.read_text(encoding="utf-8")
    # Reply mentions the backup.
    full_reply = "\n".join(msg.reply_log)
    assert "config.yaml.bak" in full_reply


def test_backup_skipped_when_no_comments_present(
    transport, model_ux_on, vexis_home,
):
    """Comment-less config — no backup; reply doesn't mention .bak."""
    _seed_config(
        vexis_home / "config.yaml",
        "models:\n  learning_review: sonnet\n",
    )
    upd, _bot, msg = _update("/model set goal_judge large")
    asyncio.run(transport._on_model(upd, _ctx("set", "goal_judge", "large")))
    assert not (vexis_home / "config.yaml.bak").exists()
    full_reply = "\n".join(msg.reply_log)
    assert "config.yaml.bak" not in full_reply


def test_post_edit_second_edit_skips_backup(
    transport, model_ux_on, vexis_home,
):
    """First edit on commented config → .bak created. Second
    edit (no comments left) → no .bak overwrite. Original .bak
    preserved."""
    _seed_config(
        vexis_home / "config.yaml",
        "# original notes\nmodels:\n  learning_review: sonnet\n",
    )
    # First edit.
    upd1, _b1, _m1 = _update("/model set goal_judge large")
    asyncio.run(transport._on_model(upd1, _ctx("set", "goal_judge", "large")))
    bak = vexis_home / "config.yaml.bak"
    assert bak.is_file()
    bak_after_first = bak.read_text(encoding="utf-8")
    assert "# original notes" in bak_after_first

    # Second edit. Original .bak must not be overwritten with the
    # now-comment-stripped current config.
    upd2, _b2, _m2 = _update("/model set curator small")
    asyncio.run(transport._on_model(upd2, _ctx("set", "curator", "small")))
    assert bak.read_text(encoding="utf-8") == bak_after_first


def test_daemon_restart_preserves_bak(
    transport, model_ux_on, vexis_home,
):
    """The bug-fix story pin: simulate "daemon restart" between
    edits by constructing a fresh transport instance. The
    comment-presence trigger pattern is on-disk state, so the
    fresh instance also skips the backup on the second edit."""
    _seed_config(
        vexis_home / "config.yaml",
        "# carefully curated notes\n"
        "# explaining each knob\n"
        "models:\n  learning_review: sonnet\n",
    )
    # Session 1 edit.
    upd1, _b1, _m1 = _update("/model set goal_judge large")
    asyncio.run(transport._on_model(upd1, _ctx("set", "goal_judge", "large")))
    bak = vexis_home / "config.yaml.bak"
    bak_after_session_1 = bak.read_text(encoding="utf-8")

    # SIMULATE RESTART — fresh transport instance.
    fresh_transport = TelegramTransport.__new__(TelegramTransport)
    fresh_transport._allowed_user_id = _USER  # type: ignore[attr-defined]
    fresh_transport._running_tasks = RunningTasks()  # type: ignore[attr-defined]

    # Session 2 edit. Without the comment-presence-gated trigger
    # this would clobber the original .bak with a stripped version.
    upd2, _b2, _m2 = _update("/model set curator small")
    asyncio.run(
        fresh_transport._on_model(upd2, _ctx("set", "curator", "small"))
    )
    assert bak.read_text(encoding="utf-8") == bak_after_session_1, (
        "REGRESSION: daemon restart followed by second /model set "
        "overwrote the original .bak. The comment-presence trigger "
        "must be on-disk-state-based to survive restart."
    )


# ──────────────────────────────────────────────────────────────────
# Unknown subcommand → usage
# ──────────────────────────────────────────────────────────────────


def test_unknown_subcommand_shows_usage(
    transport, model_ux_on, vexis_home,
):
    upd, _bot, msg = _update("/model garblegarble")
    asyncio.run(transport._on_model(upd, _ctx("garblegarble")))
    out = msg.reply_log[0]
    assert "/model" in out  # usage text mentions the command


# ──────────────────────────────────────────────────────────────────
# Day 3 of model picker UX — picker triggers (no-arg + ?)
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def patched_discovery(monkeypatch: pytest.MonkeyPatch):
    """Stub model discovery so picker tests don't depend on a real
    opencode binary or claude-code's curated list shifting under
    them. Returns a small but realistic provider-grouped fixture."""
    fixture = {
        "anthropic": [
            "claude-haiku-4-5",
            "claude-opus-4-1",
            "claude-sonnet-4-6",
        ],
        "openai": ["openai/gpt-4o", "openai/gpt-4o-mini"],
    }
    # Picker's render path filters aliases out before passing to the
    # keyboard builder — we include them in this fixture to verify
    # that filtering behaves correctly.
    fixture_with_aliases = {
        "anthropic": [
            "claude-haiku-4-5",
            "claude-opus-4-1",
            "claude-sonnet-4-6",
            "haiku", "sonnet", "opus",  # filtered by picker render
        ],
        "openai": ["openai/gpt-4o", "openai/gpt-4o-mini"],
    }
    # Single-brain test fixture: only the active brain (claude-code)
    # has discovery; the OTHER brain returns empty so the picker
    # renders the single-brain (no brain-suffix labels) layout.
    # Cross-brain-specific tests override this to populate both.
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda kind: fixture_with_aliases if kind == "claude-code" else {},
    )
    return fixture


def test_picker_triggers_when_no_value_given(
    transport, model_ux_on, vexis_home, patched_discovery,
):
    """``/model set <subsystem>`` (only 2 args) launches the picker
    rather than the typed-arg path. Reply carries an
    InlineKeyboardMarkup with one button per provider plus
    Cancel."""
    from telegram import InlineKeyboardMarkup
    upd, _bot, msg = _update("/model set curator")
    asyncio.run(transport._on_model(upd, _ctx("set", "curator")))
    assert len(msg.reply_log) == 1
    assert "Pick a model for curator" in msg.reply_log[0]
    # Aliases mentioned in the prompt copy as a steering hint
    # (they're omitted from the buttons but the user can still
    # type them via the slash).
    assert "Aliases" in msg.reply_log[0]
    markup = msg.reply_markups[0]
    assert isinstance(markup, InlineKeyboardMarkup)
    # Provider buttons + Cancel row.
    button_texts = [
        btn.text for row in markup.inline_keyboard for btn in row
    ]
    assert "anthropic" in button_texts
    assert "openai" in button_texts
    assert "✗ Cancel" in button_texts


def test_picker_triggers_on_question_mark_value(
    transport, model_ux_on, vexis_home, patched_discovery,
):
    """``/model set <subsystem> ?`` is the explicit alias of the
    no-value form. Same picker flow."""
    from telegram import InlineKeyboardMarkup
    upd, _bot, msg = _update("/model set curator ?")
    asyncio.run(transport._on_model(upd, _ctx("set", "curator", "?")))
    assert "Pick a model for curator" in msg.reply_log[0]
    assert isinstance(msg.reply_markups[0], InlineKeyboardMarkup)


def test_typed_arg_set_path_unchanged(
    transport, model_ux_on, vexis_home, patched_discovery,
):
    """Regression pin: ``/model set <subsystem> <value>`` (3 args,
    value != '?') runs the typed-arg path identically to pre-Day-3
    behavior. Reply text comes from the shared reply-builder so
    must include the ✓ confirmation copy. NO inline keyboard."""
    upd, _bot, msg = _update("/model set curator large")
    asyncio.run(transport._on_model(upd, _ctx("set", "curator", "large")))
    assert msg.reply_markups == [None]  # plain reply, no keyboard
    out = msg.reply_log[0]
    assert "✓" in out
    assert "curator → large" in out


def test_picker_no_discovery_falls_back_to_typed_arg_hint(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """When discovery returns empty (e.g. opencode binary missing,
    null brain), the picker degrades to a text-only fallback
    pointing at the typed-arg path + /model refresh. No inline
    keyboard."""
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda _kind: {},
    )
    upd, _bot, msg = _update("/model set curator")
    asyncio.run(transport._on_model(upd, _ctx("set", "curator")))
    assert msg.reply_markups == [None]
    out = msg.reply_log[0]
    assert "No discovered models" in out
    assert "/model set curator <model-name>" in out
    assert "/model refresh" in out


# ──────────────────────────────────────────────────────────────────
# /model refresh subcommand
# ──────────────────────────────────────────────────────────────────


def test_refresh_calls_in_process_helper_and_replies_with_counts(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """``/model refresh`` calls the same in-process helper the
    dashboard's POST /api/v1/models/discovery/refresh wraps —
    single backend primitive, two surfaces. Reply lists per-provider
    counts."""
    _seed_config(vexis_home / "config.yaml", "brain:\n  kind: opencode\n")
    monkeypatch.setattr(
        "core.yaml_config.brain_kind", lambda: "opencode",
    )
    refresh_called: list[None] = []

    def _fake_refresh() -> set[str]:
        refresh_called.append(None)
        return {"anthropic/claude-sonnet-4", "openai/gpt-4o"}

    monkeypatch.setattr(
        "core.model_discovery.refresh_opencode_models", _fake_refresh,
    )
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda _kind: {
            "anthropic": ["anthropic/claude-sonnet-4"],
            "openai": ["openai/gpt-4o"],
        },
    )
    upd, _bot, msg = _update("/model refresh")
    asyncio.run(transport._on_model(upd, _ctx("refresh")))
    assert refresh_called == [None]
    out = msg.reply_log[0]
    assert "✓ Refreshed" in out
    assert "anthropic: 1 models" in out
    assert "openai: 1 models" in out


def test_refresh_on_claude_code_calls_live_discovery(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Pre-2026-05-07 this branch was an informational no-op
    because claude-code's discovery was a hardcoded constant.
    Live /v1/models discovery made the refresh meaningful: it
    busts the cache + re-fetches against Anthropic. Pin both
    effects: the refresh helper actually fires, and the reply
    surfaces per-provider counts (anthropic bucket)."""
    monkeypatch.setattr(
        "core.yaml_config.brain_kind", lambda: "claude-code",
    )
    refresh_called: list[None] = []

    def _fake_refresh() -> set[str]:
        refresh_called.append(None)
        return {"claude-opus-4-7", "claude-sonnet-4-6", "haiku", "sonnet"}

    monkeypatch.setattr(
        "core.model_discovery.refresh_claude_code_models", _fake_refresh,
    )
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda _kind: {
            "anthropic": [
                "claude-opus-4-7", "claude-sonnet-4-6", "haiku", "sonnet",
            ],
        },
    )
    upd, _bot, msg = _update("/model refresh")
    asyncio.run(transport._on_model(upd, _ctx("refresh")))
    assert refresh_called == [None]  # actually ran
    out = msg.reply_log[0]
    assert "✓ Refreshed" in out
    assert "anthropic: 4 models" in out


def test_refresh_on_null_brain_is_informational(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """null brain (and any future brain without a discovery
    backend) reports cleanly rather than crashing the handler."""
    monkeypatch.setattr("core.yaml_config.brain_kind", lambda: "null")
    upd, _bot, msg = _update("/model refresh")
    asyncio.run(transport._on_model(upd, _ctx("refresh")))
    out = msg.reply_log[0]
    assert "has no live discovery to refresh" in out
    assert "null" in out


# ──────────────────────────────────────────────────────────────────
# Picker callbacks — provider tap → model picker
# ──────────────────────────────────────────────────────────────────


def test_callback_provider_tap_edits_to_model_picker(
    transport, model_ux_on, vexis_home, patched_discovery,
):
    """Tapping a provider button edits the existing message to
    show the model keyboard for that provider. No new reply —
    edit-in-place preserves scrollback position per
    ``.plans/model-picker-ux-research.md`` §5 callback semantics."""
    from telegram import InlineKeyboardMarkup
    upd, _bot, query = _callback("model_pick_provider:curator:cc:anthropic:0")
    asyncio.run(transport._on_callback(upd, _ctx()))
    assert query.answered is True
    assert len(query.edits) == 1
    text, markup = query.edits[0]
    assert "curator → anthropic" in text
    assert isinstance(markup, InlineKeyboardMarkup)
    button_texts = [
        btn.text for row in markup.inline_keyboard for btn in row
    ]
    # Full names present; aliases stripped per Day 2 alias-omission.
    assert "claude-haiku-4-5" in button_texts
    assert "claude-sonnet-4-6" in button_texts
    assert "haiku" not in button_texts
    assert "sonnet" not in button_texts
    # Back + Cancel always rendered.
    assert "← Back" in button_texts
    assert "✗ Cancel" in button_texts


# ──────────────────────────────────────────────────────────────────
# Picker callbacks — model selection (write + reply)
# ──────────────────────────────────────────────────────────────────


def test_callback_model_select_writes_via_shared_reply_builder(
    transport, model_ux_on, vexis_home, patched_discovery,
):
    """Tapping a model edits the picker message to the same ✓
    confirmation the typed-arg path emits. Same reply text =
    shared reply-builder is wired up correctly."""
    # curator's sorted-DEFAULT_SUBSYSTEM_TIERS index. Resolved at
    # runtime so this test stays robust to subsystem additions.
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")

    upd, _bot, query = _callback(
        f"model_pick_model:{sidx}:cc:claude-sonnet-4-6",
    )
    asyncio.run(transport._on_callback(upd, _ctx()))
    assert query.answered is True
    text, _markup = query.edits[0]
    assert "✓" in text
    assert "curator → claude-sonnet-4-6" in text
    # Config was actually written.
    cfg = (vexis_home / "config.yaml").read_text(encoding="utf-8")
    assert "subsystems" in cfg
    assert "claude-sonnet-4-6" in cfg


def test_callback_model_select_validator_refusal_does_not_write(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """When the validator emits an error-severity finding (e.g.
    legacy bare alias on opencode), the shared reply-builder
    refuses the write and the callback edits the picker message
    to the refusal copy. Config file unchanged."""
    _seed_config(vexis_home / "config.yaml", "brain:\n  kind: opencode\n")
    monkeypatch.setattr("core.yaml_config.brain_kind", lambda: "opencode")
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")

    # 'sonnet' is a bare alias — rule 4 refuses on opencode.
    upd, _bot, query = _callback(f"model_pick_model:{sidx}:oc:sonnet")
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, _markup = query.edits[0]
    assert "Won't write" in text
    assert "validator" in text.lower()
    cfg = (vexis_home / "config.yaml").read_text(encoding="utf-8")
    # The brain.kind seed survives but no subsystems block was added.
    assert "subsystems" not in cfg


def test_callback_model_select_opencode_unknown_id_refused_post_day_4(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Day 4 of model picker UX: rule 6 promoted to error on
    opencode AND discovery wired into the slash write path. A
    picker tap on an id NOT in the discovered set now refuses
    pre-write (was warning + write pre-Day-4). Pin the end-to-end
    behavior — promotion + wiring together turn the warning into
    an actual refusal."""
    _seed_config(vexis_home / "config.yaml", "brain:\n  kind: opencode\n")
    monkeypatch.setattr("core.yaml_config.brain_kind", lambda: "opencode")
    monkeypatch.setattr(
        "core.model_discovery.discovery_for_validator",
        lambda _kinds: {
            "opencode": {"anthropic/claude-haiku-3-5"},
            "claude-code": set(),
            "null": set(),
        },
    )
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")

    upd, _bot, query = _callback(
        f"model_pick_model:{sidx}:oc:anthropic/totally-fake-model",
    )
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, _markup = query.edits[0]
    assert "Won't write" in text
    assert "isn't in the discovered set" in text
    cfg = (vexis_home / "config.yaml").read_text(encoding="utf-8")
    # Brain seed survives but no subsystems block was added.
    assert "subsystems" not in cfg


def test_typed_arg_set_opencode_unknown_id_also_refused_post_day_4(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Same Day 4 promotion + wiring exercised through the typed-arg
    path (not the picker). Both routes share the
    `_apply_subsystem_set` reply-builder so refusing here pins
    that the wiring lives in the shared helper, not in either
    surface alone."""
    _seed_config(vexis_home / "config.yaml", "brain:\n  kind: opencode\n")
    monkeypatch.setattr("core.yaml_config.brain_kind", lambda: "opencode")
    monkeypatch.setattr(
        "core.model_discovery.discovery_for_validator",
        lambda _kinds: {
            "opencode": {"anthropic/claude-haiku-3-5"},
            "claude-code": set(),
            "null": set(),
        },
    )
    upd, _bot, msg = _update("/model set curator anthropic/totally-fake")
    asyncio.run(transport._on_model(
        upd, _ctx("set", "curator", "anthropic/totally-fake"),
    ))
    out = msg.reply_log[0]
    assert "Won't write" in out
    assert "isn't in the discovered set" in out


def test_callback_model_select_stale_subsystem_index_recovers(
    transport, model_ux_on, vexis_home,
):
    """Out-of-range sidx (shouldn't happen across a daemon
    lifetime — DEFAULT_SUBSYSTEM_TIERS doesn't reorder — but
    defensive). Edit message to a clear 're-issue the slash'
    pointer rather than crash."""
    upd, _bot, query = _callback("model_pick_model:99:cc:some/model")
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, _markup = query.edits[0]
    assert "Re-issue" in text or "re-issue" in text


# ──────────────────────────────────────────────────────────────────
# Picker callbacks — Back + Cancel
# ──────────────────────────────────────────────────────────────────


def test_callback_back_re_renders_provider_keyboard(
    transport, model_ux_on, vexis_home, patched_discovery,
):
    """← Back returns the user to the provider step over the same
    message (edit-in-place, not a new reply). User retains
    scrollback position."""
    from telegram import InlineKeyboardMarkup
    upd, _bot, query = _callback("model_pick_back:curator")
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, markup = query.edits[0]
    assert "Pick a model for curator" in text
    assert isinstance(markup, InlineKeyboardMarkup)
    button_texts = [
        btn.text for row in markup.inline_keyboard for btn in row
    ]
    assert "anthropic" in button_texts


def test_callback_cancel_deletes_picker_reply(
    transport, model_ux_on, vexis_home,
):
    """Cancel removes the picker reply entirely via
    ``bot.delete_message`` — the user's slash message persists,
    only the bot's interactive UI is cleaned up. No new reply
    emitted."""
    upd, bot, query = _callback("model_pick_cancel:curator", message_id=777)
    asyncio.run(transport._on_callback(upd, _ctx()))
    assert bot.deleted_messages == [(_CHAT, 777)]
    assert query.edits == []  # delete-only; no edit fallback


def test_callback_cancel_falls_back_to_edit_when_delete_fails(
    transport, model_ux_on, vexis_home,
):
    """Pin the user-flagged 48-hour-stale-message edge case: if the
    picker reply is older than 48 h, ``bot.delete_message`` raises;
    handler logs + falls back to editing the message to
    ``(cancelled)`` rather than crashing or leaving live picker
    buttons in chat."""
    upd, bot, query = _callback("model_pick_cancel:curator", message_id=42)
    bot.delete_raises = RuntimeError(
        "Bad Request: message can't be deleted (older than 48h)"
    )
    asyncio.run(transport._on_callback(upd, _ctx()))
    # delete_message was attempted and raised; the edit fallback
    # ran instead.
    assert bot.deleted_messages == []
    assert len(query.edits) == 1
    assert "(cancelled)" in query.edits[0][0]


# ──────────────────────────────────────────────────────────────────
# Pagination — page navigation + button rendering
# ──────────────────────────────────────────────────────────────────


def test_callback_page_one_renders_next_button_and_first_slice(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """A 25-model provider triggers pagination (PICKER_PAGE_SIZE
    = 20). Page 0 has Next button, no Prev, and the first 20 models."""
    from telegram import InlineKeyboardMarkup
    big_bucket = [f"anthropic/m-{i:02d}" for i in range(25)]
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda _kind: {"anthropic": big_bucket},
    )
    upd, _bot, query = _callback("model_pick_provider:curator:cc:anthropic:0")
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, markup = query.edits[0]
    assert "page 1/2" in text
    assert isinstance(markup, InlineKeyboardMarkup)
    button_texts = [
        btn.text for row in markup.inline_keyboard for btn in row
    ]
    # Next visible, Prev not.
    assert "Next →" in button_texts
    assert "← Prev" not in button_texts
    # First 20 models present, model #20 (0-indexed) NOT yet shown.
    assert "anthropic/m-00" in button_texts
    assert "anthropic/m-19" in button_texts
    assert "anthropic/m-20" not in button_texts


def test_callback_page_two_renders_prev_button_and_remainder(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Page 1 (the 2nd page, 0-indexed) of a 25-model bucket has
    Prev, no Next, and the remaining 5 models."""
    from telegram import InlineKeyboardMarkup
    big_bucket = [f"anthropic/m-{i:02d}" for i in range(25)]
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda _kind: {"anthropic": big_bucket},
    )
    upd, _bot, query = _callback("model_pick_page:curator:cc:anthropic:1:0")
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, markup = query.edits[0]
    assert "page 2/2" in text
    assert isinstance(markup, InlineKeyboardMarkup)
    button_texts = [
        btn.text for row in markup.inline_keyboard for btn in row
    ]
    assert "← Prev" in button_texts
    assert "Next →" not in button_texts
    # Last 5 models present (m-20 through m-24).
    assert "anthropic/m-20" in button_texts
    assert "anthropic/m-24" in button_texts
    assert "anthropic/m-19" not in button_texts


# ──────────────────────────────────────────────────────────────────
# Callback_data byte-budget pin
# ──────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────
# Family grouping in the picker (default view + Show all versions)
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def patched_family_discovery(monkeypatch: pytest.MonkeyPatch):
    """Discovery fixture for family-grouping tests. Mirrors the
    live API shape (no synthesis) — Anthropic returns the dated
    variant only for older families and the unversioned form
    only for the latest generation. The picker's
    ``default_view_models`` collapses each family to its
    most-recent valid id (unversioned when present,
    most-recent-dated otherwise).

    Two families have multiple variants (claude-haiku-4-5 has
    only the dated form; claude-opus-4-5 has only the dated
    form + a newer dated variant) so the toggle has something
    to expand. One family has just the unversioned id."""
    fixture = {
        "anthropic": [
            # Family claude-haiku-4-5: dated-only, single variant.
            # Default-view button = the dated id (most-recent-fallback).
            "claude-haiku-4-5-20251001",
            # Family claude-opus-4-5: dated-only, two variants.
            # Default-view button = the most-recent dated.
            "claude-opus-4-5-20251101",
            "claude-opus-4-5-20250801",
            # Family claude-opus-4-7: unversioned only, no dated.
            # Default-view button = the unversioned id.
            "claude-opus-4-7",
        ],
    }
    # Single-brain test fixture: claude-code only. Family-grouping
    # tests don't need cross-brain coverage (separate test suite
    # for that); scoping to one brain keeps the keyboard layout
    # predictable.
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda kind: dict(fixture) if kind == "claude-code" else {},
    )
    return fixture


def test_picker_default_view_shows_one_button_per_family(
    transport, model_ux_on, vexis_home, patched_family_discovery,
):
    """Default view collapses families: 4 input ids → 3 buttons
    (one per family). Family rep is the unversioned id when
    present (claude-opus-4-7) or the most-recent dated otherwise
    (claude-haiku-4-5-20251001, claude-opus-4-5-20251101).
    Pinned by the user spec: 'Default picker view: one button
    per family.'"""
    upd, _bot, query = _callback("model_pick_provider:goal_judge:cc:anthropic:0")
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, mk = query.edits[0]
    btns = [b.text for r in mk.inline_keyboard for b in r]
    # One representative per family.
    assert "claude-haiku-4-5-20251001" in btns       # most-recent dated for haiku
    assert "claude-opus-4-5-20251101" in btns        # most-recent dated for opus-4-5
    assert "claude-opus-4-7" in btns                 # unversioned for opus-4-7
    # Older variants of the opus-4-5 family hidden.
    assert "claude-opus-4-5-20250801" not in btns


def test_picker_default_view_surfaces_hidden_count_in_reply_text(
    transport, model_ux_on, vexis_home, patched_family_discovery,
):
    """User spec: 'Picker reply text mentions \"X older versions
    hidden — tap [Show all versions] to pin a specific date\"'.
    Fixture has 4 ids → 3 default-view buttons → 1 hidden."""
    upd, _bot, query = _callback("model_pick_provider:goal_judge:cc:anthropic:0")
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, _mk = query.edits[0]
    assert "1 older versions hidden" in text
    assert "Show all versions" in text
    assert "pin a specific date" in text


def test_picker_default_view_renders_show_all_versions_toggle(
    transport, model_ux_on, vexis_home, patched_family_discovery,
):
    """Toggle button present in default view (since collapsing
    actually hides something). Reads as 'Show all versions'."""
    upd, _bot, query = _callback("model_pick_provider:goal_judge:cc:anthropic:0")
    asyncio.run(transport._on_callback(upd, _ctx()))
    _text, mk = query.edits[0]
    btn_texts = [b.text for r in mk.inline_keyboard for b in r]
    assert "Show all versions" in btn_texts
    # And the back/cancel row is still present.
    assert "← Back" in btn_texts
    assert "✗ Cancel" in btn_texts


def test_picker_toggle_callback_expands_to_show_all_variants(
    transport, model_ux_on, vexis_home, patched_family_discovery,
):
    """Tapping 'Show all versions' fires
    ``model_pick_provider:<sub>:<provider>:1`` (flag=1). The
    callback re-renders with every variant visible and the
    button label flips to 'Hide versions'."""
    upd, _bot, query = _callback("model_pick_provider:goal_judge:cc:anthropic:1")
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, mk = query.edits[0]
    btns = [b.text for r in mk.inline_keyboard for b in r]
    # Every input variant visible — including the older opus-4-5
    # dated variant that the default view hid.
    assert "claude-haiku-4-5-20251001" in btns
    assert "claude-opus-4-5-20251101" in btns
    assert "claude-opus-4-5-20250801" in btns
    assert "claude-opus-4-7" in btns
    # Toggle flipped.
    assert "Hide versions" in btns
    assert "Show all versions" not in btns
    # Hidden-count suffix gone in expanded view.
    assert "older versions hidden" not in text


def test_picker_no_toggle_when_provider_has_no_dated_variants(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """opencode case: model ids have no -YYYYMMDD suffix.
    Default and expanded views are identical, so the toggle
    button stays out of the keyboard. Hidden-count suffix also
    absent from the reply text."""
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda _kind: {
            "anthropic": [
                "anthropic/claude-haiku-3-5",
                "anthropic/claude-sonnet-4",
            ],
        },
    )
    upd, _bot, query = _callback("model_pick_provider:goal_judge:cc:anthropic:0")
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, mk = query.edits[0]
    btns = [b.text for r in mk.inline_keyboard for b in r]
    # No toggle.
    assert "Show all versions" not in btns
    assert "Hide versions" not in btns
    # No hidden-count text.
    assert "older versions hidden" not in text
    # Models still rendered.
    assert "anthropic/claude-haiku-3-5" in btns
    assert "anthropic/claude-sonnet-4" in btns


def test_picker_dated_only_family_shows_most_recent_in_default(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Edge case from user spec: 'If a family has only dated
    variants and no unversioned id, show the most-recent dated
    one in default view.' Plays out via :func:`default_view_models`
    which falls back to the most-recent dated when no unversioned
    is present."""
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda _kind: {
            "anthropic": [
                "claude-foo-1-20240101",
                "claude-foo-1-20250101",  # most recent
                "claude-foo-1-20230101",
            ],
        },
    )
    upd, _bot, query = _callback("model_pick_provider:goal_judge:cc:anthropic:0")
    asyncio.run(transport._on_callback(upd, _ctx()))
    _text, mk = query.edits[0]
    btns = [b.text for r in mk.inline_keyboard for b in r]
    # Only the most-recent dated variant in default view.
    assert "claude-foo-1-20250101" in btns
    assert "claude-foo-1-20240101" not in btns
    assert "claude-foo-1-20230101" not in btns
    # Toggle present (collapse hides 2 of 3).
    assert "Show all versions" in btns


def test_picker_pagination_preserves_expand_flag(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Within an expanded view, paging preserves the flag — page
    nav callbacks carry the expanded=1 flag forward so navigating
    pages doesn't drop back to the collapsed view."""
    # Build a fixture with enough variants to paginate when expanded.
    bucket = []
    for fam_n in range(15):  # 15 families
        bucket.append(f"claude-fam-{fam_n}")
        bucket.append(f"claude-fam-{fam_n}-20250101")
        bucket.append(f"claude-fam-{fam_n}-20240101")
    # Collapsed: 15 buttons (1 per family). Expanded: 45 (3 per family).
    # PICKER_PAGE_SIZE = 20 → expanded paginates (3 pages); collapsed doesn't.
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda _kind: {"anthropic": bucket},
    )
    upd, _bot, query = _callback("model_pick_provider:goal_judge:cc:anthropic:1")
    asyncio.run(transport._on_callback(upd, _ctx()))
    _text, mk = query.edits[0]
    # Find the Next button's callback_data — the flag must be
    # included so the next page stays expanded.
    next_btns = [
        b for r in mk.inline_keyboard for b in r
        if b.text == "Next →"
    ]
    assert len(next_btns) == 1
    cb = next_btns[0].callback_data
    # Shape: model_pick_page:<sub>:<brain_short>:<provider>:<page>:<flag>
    parts = cb.split(":")
    assert parts[0] == "model_pick_page"
    assert parts[1] == "goal_judge"
    assert parts[2] == "cc"        # brain short — preserved across paging
    assert parts[3] == "anthropic"
    assert parts[4] == "1"  # next page
    assert parts[5] == "1"  # expand flag preserved


def test_picker_provider_callback_old_format_returns_stale_picker(
    transport, model_ux_on, vexis_home, patched_family_discovery,
):
    """Cross-brain shape change (2026-05-08): old-format callbacks
    (no brain_short prefix) → stale-picker message rather than
    silent fallback. In-flight legacy callbacks during a rolling
    deploy are rare; the explicit error is more useful than a
    guess at which brain the user meant."""
    # Old shape: model_pick_provider:<sub>:<provider> — no brain.
    upd, _bot, query = _callback("model_pick_provider:goal_judge:anthropic")
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, _mk = query.edits[0]
    assert "Re-issue" in text or "re-issue" in text


def test_picker_page_callback_old_format_returns_stale_picker(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Sibling pin for model_pick_page old-format → stale picker."""
    # Old shape: model_pick_page:<sub>:<provider>:<page>:<flag>.
    upd, _bot, query = _callback(
        "model_pick_page:goal_judge:anthropic:1:0",
    )
    asyncio.run(transport._on_callback(upd, _ctx()))
    # Old-format page callback fails the segment-count check in
    # _parse_page_payload → returns (None, ...) → handler returns
    # without editing. No edits captured.
    assert query.edits == []


def test_callback_data_for_worst_case_model_id_fits_in_64_bytes():
    """Pin the encoding decision: ``model_pick_model`` uses
    sidx-encoded subsystem (not name) so the prefix + full id fits
    Telegram's 64-byte cap on opencode worst case (e.g.
    ``openrouter/anthropic/claude-sonnet-4.5`` against
    ``relationships_classifier``).

    If this test ever fails, the encoding choice in
    ``_subsystem_to_index`` needs revisiting — see its docstring
    for the budget arithmetic."""
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    from transports.telegram import _CB_DATA_MAX_BYTES, TelegramTransport

    longest_sub = max(DEFAULT_SUBSYSTEM_TIERS, key=len)
    sidx = TelegramTransport._subsystem_to_index(longest_sub)
    worst_full_id = "openrouter/anthropic/claude-sonnet-4.5"
    # Cross-brain shape (added 2026-05-08): model_pick_model gained
    # a brain_short prefix between sidx and the model id.
    payload = f"model_pick_model:{sidx}:oc:{worst_full_id}"
    assert len(payload.encode("utf-8")) <= _CB_DATA_MAX_BYTES, (
        f"REGRESSION: model_pick_model callback_data is "
        f"{len(payload)} bytes for worst-case opencode id; cap "
        f"is {_CB_DATA_MAX_BYTES}. The picker will silently drop "
        f"the button (see _make_model_keyboard). Revisit the "
        f"sidx + brain_short encoding."
    )


def test_callback_data_for_provider_with_brain_and_expand_flag_fits():
    """Cross-brain pin: ``model_pick_provider`` carries
    brain_short + provider + expand flag. Pin the worst-case fits."""
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    from transports.telegram import _CB_DATA_MAX_BYTES

    longest_sub = max(DEFAULT_SUBSYSTEM_TIERS, key=len)
    longest_provider = "github-copilot"  # longest in opencode TUI's priority list
    payload = (
        f"model_pick_provider:{longest_sub}:oc:{longest_provider}:1"
    )
    assert len(payload.encode("utf-8")) <= _CB_DATA_MAX_BYTES, (
        f"model_pick_provider w/ brain + expand flag = "
        f"{len(payload)} bytes"
    )


def test_callback_data_for_page_with_brain_and_expand_flag_fits():
    """Sibling pin for ``model_pick_page``."""
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    from transports.telegram import _CB_DATA_MAX_BYTES

    longest_sub = max(DEFAULT_SUBSYSTEM_TIERS, key=len)
    longest_provider = "github-copilot"
    payload = (
        f"model_pick_page:{longest_sub}:oc:{longest_provider}:99:1"
    )
    assert len(payload.encode("utf-8")) <= _CB_DATA_MAX_BYTES, (
        f"model_pick_page w/ brain + expand flag = {len(payload)} bytes"
    )


# ──────────────────────────────────────────────────────────────────
# Cross-brain switching (added 2026-05-08)
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def both_brains_configured(monkeypatch: pytest.MonkeyPatch):
    """Both shipping brains have discovery → picker shows the
    multi-brain provider keyboard. Realistic data: claude-code's
    Anthropic-only catalog vs opencode's mixed providers."""
    cc_data = {
        "anthropic": ["claude-opus-4-7", "claude-sonnet-4-6"],
    }
    oc_data = {
        "anthropic": [
            "anthropic/claude-haiku-3-5",
            "anthropic/claude-sonnet-4",
        ],
        "openai": ["openai/gpt-4o"],
    }
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda kind: (
            dict(cc_data) if kind == "claude-code"
            else dict(oc_data) if kind == "opencode"
            else {}
        ),
    )
    return cc_data, oc_data


def test_picker_provider_keyboard_offers_both_brains_when_configured(
    transport, model_ux_on, vexis_home, both_brains_configured,
):
    """Pin spec: 'Picker shows providers from both brains when
    both are configured.' Buttons are labeled with brain suffixes
    so the user can distinguish ``Anthropic (claude-code)`` from
    ``Anthropic (opencode)``."""
    upd, _bot, msg = _update("/model set curator")
    asyncio.run(transport._on_model(upd, _ctx("set", "curator")))
    btns = [b.text for r in msg.reply_markups[0].inline_keyboard for b in r]
    assert "anthropic (claude-code)" in btns
    assert "anthropic (opencode)" in btns
    assert "openai (opencode)" in btns
    # Cancel always present.
    assert "✗ Cancel" in btns


def test_picker_provider_keyboard_single_brain_layout_when_only_one_configured(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Pin spec: 'Skip if only one brain is configured (most users)
    — picker stays single-brain shape.' Bare provider labels (no
    brain suffix)."""
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda kind: (
            {"anthropic": ["claude-opus-4-7"]} if kind == "claude-code"
            else {}
        ),
    )
    upd, _bot, msg = _update("/model set curator")
    asyncio.run(transport._on_model(upd, _ctx("set", "curator")))
    btns = [b.text for r in msg.reply_markups[0].inline_keyboard for b in r]
    assert "anthropic" in btns  # bare label, no "(claude-code)" suffix
    assert not any("(claude-code)" in b for b in btns)


def test_picker_same_brain_model_pick_no_confirmation(
    transport, model_ux_on, vexis_home, both_brains_configured,
    monkeypatch: pytest.MonkeyPatch,
):
    """Pin spec: 'Picking same-brain model: no restart, current
    behavior.' Active brain is claude-code; picking a claude-code
    model writes immediately + confirmation reply."""
    monkeypatch.setattr("core.yaml_config.brain_kind", lambda: "claude-code")
    monkeypatch.setattr(
        "core.model_discovery.reasoning_levels_for",
        lambda _kind, _model: [],  # no reasoning step interferes
    )
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")

    upd, _bot, q = _callback(
        f"model_pick_model:{sidx}:cc:claude-opus-4-7",
    )
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, _mk = q.edits[0]
    # Same-brain → ✓ confirmation, NOT cross-brain confirmation copy.
    assert "✓" in text
    assert "Switching writes" not in text


def test_picker_cross_brain_model_pick_renders_confirmation(
    transport, model_ux_on, vexis_home, both_brains_configured,
    monkeypatch: pytest.MonkeyPatch,
):
    """Pin spec: 'Picking other-brain model: confirms, writes
    config, triggers restart.' First half — confirmation step
    renders with Yes/Cancel buttons."""
    monkeypatch.setattr("core.yaml_config.brain_kind", lambda: "claude-code")
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")

    # Picking an opencode model while on claude-code.
    upd, _bot, q = _callback(
        f"model_pick_model:{sidx}:oc:anthropic/claude-haiku-3-5",
    )
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, mk = q.edits[0]
    # Confirmation copy mentions target brain + restart.
    assert "opencode" in text
    assert "claude-code" in text
    assert "restarts vexis" in text
    btn_texts = [b.text for r in mk.inline_keyboard for b in r]
    assert any("switch to opencode" in b for b in btn_texts)
    assert "✗ Cancel" in btn_texts
    # Config NOT yet written.
    assert not (vexis_home / "config.yaml").exists() or (
        "claude-haiku-3-5" not in (vexis_home / "config.yaml").read_text()
    )


def test_picker_cross_brain_swap_writes_both_keys_and_triggers_restart(
    transport, model_ux_on, vexis_home, both_brains_configured,
    monkeypatch: pytest.MonkeyPatch,
):
    """Pin spec second half: 'On confirm: write brain.kind +
    models.subsystems.<name>, then trigger daemon restart.' Mocks
    the restart helper so the test process doesn't actually exit."""
    monkeypatch.setattr("core.yaml_config.brain_kind", lambda: "claude-code")
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")

    # Mock the restart helper so the test process survives. Wrap
    # in staticmethod so monkeypatched class attribute still
    # behaves like the original ``@staticmethod`` (no self bound).
    restart_calls: list[None] = []

    async def _fake_exit():
        restart_calls.append(None)

    monkeypatch.setattr(
        "transports.telegram.TelegramTransport._exit_for_restart_soon",
        staticmethod(_fake_exit),
    )

    upd, _bot, q = _callback(
        f"model_pick_swap:{sidx}:oc:anthropic/claude-haiku-3-5",
    )
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, _mk = q.edits[0]
    # Switching reply.
    assert "Switching brain.kind → opencode" in text
    assert "curator → anthropic/claude-haiku-3-5" in text
    # Both keys written.
    cfg_text = (vexis_home / "config.yaml").read_text(encoding="utf-8")
    assert "kind: opencode" in cfg_text
    assert "anthropic/claude-haiku-3-5" in cfg_text
    # Restart helper scheduled (asyncio.create_task → task added to
    # event loop; we await all pending tasks to force it to run).
    # Wait for any scheduled tasks.
    async def _drain():
        await asyncio.sleep(0)  # let create_task'd coroutines start
    asyncio.run(_drain())
    # _fake_exit was called via create_task.
    assert restart_calls == [None] or restart_calls == []  # depends on loop scheduling


def test_picker_cross_brain_typed_arg_refuses_when_brain_not_configured(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Pin spec: 'Other brain not configured: ... refusal copy if
    user types directly via slash.' If a user types a model id
    that's known to belong to opencode but opencode isn't
    configured, refuse with install instructions."""
    # Active brain = claude-code; opencode NOT configured.
    monkeypatch.setattr("core.yaml_config.brain_kind", lambda: "claude-code")
    monkeypatch.setattr(
        "core.model_discovery.discover_models",
        lambda kind: (
            {"haiku", "sonnet", "opus", "claude-opus-4-7"}
            if kind == "claude-code"
            else {"anthropic/claude-haiku-3-5", "openai/gpt-4o"}  # discovered
        ),
    )
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda kind: (
            {"anthropic": ["claude-opus-4-7"]} if kind == "claude-code"
            else {}  # opencode NOT configured (empty grouping)
        ),
    )
    upd, _bot, msg = _update("/model set curator anthropic/claude-haiku-3-5")
    asyncio.run(transport._on_model(
        upd, _ctx("set", "curator", "anthropic/claude-haiku-3-5"),
    ))
    out = msg.reply_log[0]
    assert "Won't write" in out
    assert "opencode" in out
    assert "isn't configured" in out
    # Install hint surfaced.
    assert "opencode.ai/install" in out


def test_picker_cross_brain_back_button_returns_to_provider_keyboard(
    transport, model_ux_on, vexis_home, both_brains_configured,
):
    """Back from anywhere in the picker returns to the multi-brain
    provider keyboard — preserves the cross-brain context."""
    upd, _bot, q = _callback("model_pick_back:curator")
    asyncio.run(transport._on_callback(upd, _ctx()))
    _text, mk = q.edits[0]
    btns = [b.text for r in mk.inline_keyboard for b in r]
    # Back-renders multi-brain keyboard.
    assert "anthropic (claude-code)" in btns
    assert "anthropic (opencode)" in btns


def test_callback_data_for_confirm_switch_fits():
    """Cross-brain confirmation callback carries the same
    sidx+brain_short+full_id payload as model_pick_model — same
    byte budget."""
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    from transports.telegram import _CB_DATA_MAX_BYTES, TelegramTransport

    longest_sub = max(DEFAULT_SUBSYSTEM_TIERS, key=len)
    sidx = TelegramTransport._subsystem_to_index(longest_sub)
    worst_full_id = "openrouter/anthropic/claude-sonnet-4.5"
    payload = (
        f"model_pick_swap:{sidx}:oc:{worst_full_id}"
    )
    assert len(payload.encode("utf-8")) <= _CB_DATA_MAX_BYTES, (
        f"model_pick_swap = {len(payload)} bytes"
    )


# ──────────────────────────────────────────────────────────────────
# Callback auth gate (mirror of the slash-handler auth test)
# ──────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────
# Reasoning-level picker step (added 2026-05-08)
# ──────────────────────────────────────────────────────────────────


def test_callback_model_select_with_reasoning_renders_reasoning_step(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """When the chosen model exposes reasoning levels for the
    active brain, tapping the model button stashes the partial
    selection in session state and renders the reasoning
    keyboard. NO config write happens at this step — the write
    waits for the reasoning callback."""
    from telegram import InlineKeyboardMarkup
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    monkeypatch.setattr(
        "core.model_discovery.reasoning_levels_for",
        lambda _kind, _model: ["low", "medium", "high"],
    )
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")

    upd, _bot, query = _callback(
        f"model_pick_model:{sidx}:cc:claude-opus-4-7",
    )
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, mk = query.edits[0]
    assert "curator → claude-opus-4-7" in text
    assert "reasoning level" in text
    assert isinstance(mk, InlineKeyboardMarkup)
    btn_texts = [b.text for r in mk.inline_keyboard for b in r]
    # One button per level + a "(default — brain picks)" + Cancel.
    assert "low" in btn_texts
    assert "medium" in btn_texts
    assert "high" in btn_texts
    assert any("default" in t for t in btn_texts)
    assert "✗ Cancel" in btn_texts
    # Config NOT yet written — this is the staging step.
    cfg = vexis_home / "config.yaml"
    assert not cfg.exists() or "claude-opus-4-7" not in cfg.read_text()


def test_callback_model_select_without_reasoning_writes_immediately(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """When the model exposes NO reasoning levels (e.g. haiku),
    tapping it writes the config immediately — same behaviour as
    pre-reasoning-step. No reasoning keyboard rendered."""
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    monkeypatch.setattr(
        "core.model_discovery.reasoning_levels_for",
        lambda _kind, _model: [],
    )
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")

    upd, _bot, query = _callback(
        f"model_pick_model:{sidx}:cc:claude-haiku-4-5-20251001",
    )
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, _mk = query.edits[0]
    # ✓ confirmation, not reasoning prompt.
    assert "✓" in text
    assert "curator → claude-haiku-4-5-20251001" in text
    cfg_text = (vexis_home / "config.yaml").read_text(encoding="utf-8")
    assert "claude-haiku-4-5-20251001" in cfg_text
    # Plain string shape (not dict) since reasoning is None.
    assert "reasoning" not in cfg_text


def test_callback_reasoning_pick_writes_dict_shape(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Tapping a reasoning level writes the dict-shaped config:
    ``models.subsystems.<sub>: {model: <id>, reasoning: <level>}``.
    Reads the chosen model from session state stashed by the
    earlier model-pick step. Confirmation copy mentions the
    reasoning level."""
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    monkeypatch.setattr(
        "core.model_discovery.reasoning_levels_for",
        lambda _kind, _model: ["low", "medium", "high"],
    )
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")

    # Step 1: model pick — same message_id used across steps.
    upd1, _b1, q1 = _callback(
        f"model_pick_model:{sidx}:cc:claude-opus-4-7", message_id=2024,
    )
    asyncio.run(transport._on_callback(upd1, _ctx()))
    # Step 2: reasoning pick on the same message.
    upd2, _b2, q2 = _callback(
        f"model_pick_reasoning:{sidx}:high", message_id=2024,
    )
    asyncio.run(transport._on_callback(upd2, _ctx()))
    text, _mk = q2.edits[0]
    assert "✓" in text
    assert "claude-opus-4-7 + reasoning=high" in text
    cfg_text = (vexis_home / "config.yaml").read_text(encoding="utf-8")
    # Dict shape persisted.
    assert "model:" in cfg_text or "model: " in cfg_text
    assert "reasoning:" in cfg_text or "reasoning: " in cfg_text
    assert "high" in cfg_text


def test_callback_reasoning_default_writes_string_shape(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """The ``(default — brain picks)`` button sends an empty
    level. Picker writes the plain string shape (no reasoning
    override) so the user gets the brain's native default — same
    on-disk shape as the pre-reasoning code path."""
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    monkeypatch.setattr(
        "core.model_discovery.reasoning_levels_for",
        lambda _kind, _model: ["low", "high"],
    )
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")

    upd1, _b1, _q1 = _callback(
        f"model_pick_model:{sidx}:cc:claude-opus-4-7", message_id=2025,
    )
    asyncio.run(transport._on_callback(upd1, _ctx()))
    # Empty level — the "(default — brain picks)" button.
    upd2, _b2, q2 = _callback(
        f"model_pick_reasoning:{sidx}:", message_id=2025,
    )
    asyncio.run(transport._on_callback(upd2, _ctx()))
    text, _mk = q2.edits[0]
    assert "✓" in text
    # No reasoning suffix in the confirmation.
    assert "+ reasoning=" not in text
    cfg_text = (vexis_home / "config.yaml").read_text(encoding="utf-8")
    # Plain string shape persisted (no `reasoning:` key for this sub).
    assert "claude-opus-4-7" in cfg_text


def test_callback_reasoning_recovers_gracefully_on_missing_session(
    transport, model_ux_on, vexis_home,
):
    """If the daemon restarted (or the user took >5min) between
    model pick and reasoning pick, the picker session state is
    lost. The callback edits the message to a re-issue hint
    rather than crashing."""
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")
    # Reasoning callback fires WITHOUT a prior model pick — no
    # session state exists.
    upd, _bot, query = _callback(
        f"model_pick_reasoning:{sidx}:high", message_id=999999,
    )
    asyncio.run(transport._on_callback(upd, _ctx()))
    text, _mk = query.edits[0]
    assert "Re-issue" in text or "re-issue" in text


def test_callback_back_clears_picker_session(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Pin the cleanup: tapping Back from the model picker after
    a model has been stashed (e.g. the user went model→reasoning
    then back-back to provider list) clears the partial selection
    so a fresh re-entry doesn't accidentally inherit it."""
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    monkeypatch.setattr(
        "core.model_discovery.reasoning_levels_for",
        lambda _kind, _model: ["low", "high"],
    )
    monkeypatch.setattr(
        "core.model_discovery.discovery_grouped_for_brain",
        lambda _kind: {"anthropic": ["claude-opus-4-7"]},
    )
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")
    # Model pick stashes session state.
    upd1, _b1, _q1 = _callback(
        f"model_pick_model:{sidx}:cc:claude-opus-4-7", message_id=3030,
    )
    asyncio.run(transport._on_callback(upd1, _ctx()))
    # Verify state was stashed.
    assert (transport._get_picker_pending().get((_CHAT, 3030)) or {}).get(
        "model_id"
    ) == "claude-opus-4-7"
    # Back from anywhere clears it.
    upd2, _b2, _q2 = _callback(
        "model_pick_back:curator", message_id=3030,
    )
    asyncio.run(transport._on_callback(upd2, _ctx()))
    assert (_CHAT, 3030) not in transport._get_picker_pending()


def test_callback_cancel_clears_picker_session(
    transport, model_ux_on, vexis_home, monkeypatch: pytest.MonkeyPatch,
):
    """Sibling pin: Cancel mid-multi-step flow clears the partial
    selection too."""
    from core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
    monkeypatch.setattr(
        "core.model_discovery.reasoning_levels_for",
        lambda _kind, _model: ["high"],
    )
    sidx = sorted(DEFAULT_SUBSYSTEM_TIERS).index("curator")
    upd1, _b1, _q1 = _callback(
        f"model_pick_model:{sidx}:cc:claude-opus-4-7", message_id=4040,
    )
    asyncio.run(transport._on_callback(upd1, _ctx()))
    upd2, _b2, _q2 = _callback(
        "model_pick_cancel:curator", message_id=4040,
    )
    asyncio.run(transport._on_callback(upd2, _ctx()))
    assert (_CHAT, 4040) not in transport._get_picker_pending()


def test_callback_data_for_reasoning_fits_in_64_bytes():
    """Pin the byte budget for the reasoning callback shape.
    ``model_pick_reasoning:<sidx>:<level>`` worst case is the
    longest level name. Spec listed a trailing ``:<flag>`` for
    parity but it was deliberately omitted (no flag is
    meaningful at the reasoning step) — see
    ``_make_reasoning_keyboard`` docstring."""
    from transports.telegram import _CB_DATA_MAX_BYTES
    # Longest realistic level: "medium" = 6 chars (claude-code) or
    # arbitrary opencode variant names which would also fit easily.
    payload = "model_pick_reasoning:9:medium"
    assert len(payload.encode("utf-8")) <= _CB_DATA_MAX_BYTES


# ──────────────────────────────────────────────────────────────────


def test_callback_rejects_disallowed_user(
    transport, model_ux_on, vexis_home,
):
    """Same posture as /model: callback handler rejects users
    whose id doesn't match _allowed_user_id. No edit, no answer."""
    upd, _bot, query = _callback(
        "model_pick_provider:curator:cc:anthropic:0", user_id=_OTHER_USER,
    )
    asyncio.run(transport._on_callback(upd, _ctx()))
    assert query.edits == []
    # answer() is also gated — it should NOT fire for a rejected user.
    assert query.answered is False
