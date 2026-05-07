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

    async def send_message(self, chat_id: int, text: str, **_kw: Any) -> None:
        self.sent_messages.append((chat_id, text))


class _FakeMessage:
    def __init__(self, text: str, chat_id: int, bot: _FakeBot) -> None:
        self.text = text
        self.chat_id = chat_id
        self._bot = bot
        self.reply_log: list[str] = []

    async def reply_text(self, text: str, **_kw: Any) -> None:
        self.reply_log.append(text)
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
