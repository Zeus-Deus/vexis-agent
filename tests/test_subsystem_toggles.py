"""Issue #39 — per-subsystem modular toggles.

vexis-agent composes itself per deployment: a headless web backend
switches off the personal-assistant subsystems it doesn't use (and the
Telegram transport) purely via config, mirroring the add-on loader's
enabled/disabled model. The load-bearing contract these tests pin:

  * Every new gate DEFAULTS ON. A config that never mentions the key
    behaves byte-for-byte like the pre-issue daemon.
  * The two learning systems are DISTINCT switches — skill/memory
    lessons (``learning.enabled``, keepable) vs relationship/user-fact
    extraction (``relationships.enabled``, droppable).
  * ``transports.telegram`` / ``transports.web`` select the active
    transport(s); Telegram-disabled drops the bot-token requirement.
  * The ``bg_spawn`` control-socket op honours ``background_tasks.enabled``.

Boundary parsing (bare-bool vs nested ``enabled:`` dict, quoted-string
truthiness) lives in ``_bool_or_default`` / ``_transport_enabled`` and is
exercised here so a future refactor can't drift the truthiness rules.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from vexis_agent.core import yaml_config


def _patch_config(tmp_path: Path, body: str | None):
    """Write ``config.yaml`` (when ``body`` is given) and point the
    yaml_config disk reader at ``tmp_path``. Mirrors the helper in
    test_yaml_config_addons.py so these gates are tested the same way
    the add-on gates are."""
    if body is not None:
        (tmp_path / "config.yaml").write_text(body, encoding="utf-8")

    return mock.patch(
        "vexis_agent.core.yaml_config.vexis_dir",
        side_effect=lambda: tmp_path,
    )


# ---------------------------------------------------------------------------
# Defaults: every gate is ON when the key is absent (byte-for-byte boot).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gate",
    [
        "background_tasks_enabled",
        "watcher_enabled",
        "relationships_enabled",
        "transport_telegram_enabled",
        "transport_web_enabled",
    ],
)
def test_gate_defaults_on_with_no_config(tmp_path, gate):
    with _patch_config(tmp_path, body=None):
        assert getattr(yaml_config, gate)() is True


@pytest.mark.parametrize(
    "gate",
    [
        "background_tasks_enabled",
        "watcher_enabled",
        "relationships_enabled",
        "transport_telegram_enabled",
        "transport_web_enabled",
    ],
)
def test_gate_defaults_on_with_unrelated_config(tmp_path, gate):
    body = "memory:\n  memory_char_limit: 2200\n"
    with _patch_config(tmp_path, body=body):
        assert getattr(yaml_config, gate)() is True


# ---------------------------------------------------------------------------
# Explicit off for the simple ``<section>.enabled`` gates.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gate", "body"),
    [
        ("background_tasks_enabled", "background_tasks:\n  enabled: false\n"),
        ("watcher_enabled", "watcher:\n  enabled: false\n"),
        ("relationships_enabled", "relationships:\n  enabled: false\n"),
    ],
)
def test_section_enabled_false(tmp_path, gate, body):
    with _patch_config(tmp_path, body=body):
        assert getattr(yaml_config, gate)() is False


def test_section_enabled_quoted_string_off(tmp_path):
    # YAML loaders sometimes hand back the quoted spelling; the bool
    # coercion must still read it as off.
    body = 'watcher:\n  enabled: "off"\n'
    with _patch_config(tmp_path, body=body):
        assert yaml_config.watcher_enabled() is False


def test_watcher_enabled_independent_of_poll_interval(tmp_path):
    # The watcher section already carries poll/oscillation knobs; the
    # new enabled gate must coexist with them.
    body = "watcher:\n  enabled: false\n  poll_interval_seconds: 5.0\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.watcher_enabled() is False
        assert yaml_config.watcher_poll_interval_seconds() == 5.0


# ---------------------------------------------------------------------------
# Transport selection: bare-bool AND nested-enabled spellings.
# ---------------------------------------------------------------------------


def test_transport_telegram_bare_bool_false(tmp_path):
    body = "transports:\n  telegram: false\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.transport_telegram_enabled() is False
        # web is untouched → still on.
        assert yaml_config.transport_web_enabled() is True


def test_transport_telegram_nested_enabled_false(tmp_path):
    body = "transports:\n  telegram:\n    enabled: false\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.transport_telegram_enabled() is False


def test_transport_web_bare_bool_false(tmp_path):
    body = "transports:\n  web: false\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.transport_web_enabled() is False
        assert yaml_config.transport_telegram_enabled() is True


def test_transports_both_off(tmp_path):
    body = "transports:\n  telegram: false\n  web: false\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.transport_telegram_enabled() is False
        assert yaml_config.transport_web_enabled() is False


def test_transports_section_present_but_empty_defaults_on(tmp_path):
    body = "transports: {}\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.transport_telegram_enabled() is True
        assert yaml_config.transport_web_enabled() is True


# ---------------------------------------------------------------------------
# _bool_or_default truthiness table (the shared parser).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (True, False, True),
        (False, True, False),
        ("true", False, True),
        ("yes", False, True),
        ("on", False, True),
        ("1", False, True),
        ("false", True, False),
        ("no", True, False),
        ("off", True, False),
        ("0", True, False),
        (None, True, True),
        (None, False, False),
        ("garbage", True, True),
        ("garbage", False, False),
        (42, True, True),  # unrecognised type → default
    ],
)
def test_bool_or_default(value, default, expected):
    assert yaml_config._bool_or_default(value, default) is expected


# ---------------------------------------------------------------------------
# load_config: Telegram-optional when the transport is disabled.
# ---------------------------------------------------------------------------


def _clear_telegram_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)


def test_load_config_requires_token_by_default(monkeypatch):
    _clear_telegram_env(monkeypatch)
    from vexis_agent.core.config import load_config

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        load_config()


def test_load_config_telegram_optional_when_disabled(monkeypatch):
    _clear_telegram_env(monkeypatch)
    from vexis_agent.core.config import load_config

    cfg = load_config(require_telegram=False)
    # Inert fallbacks — the web dashboard carries its own auth.
    assert cfg.telegram_bot_token == ""
    assert cfg.telegram_allowed_user_id == 0


def test_load_config_honours_secrets_even_when_optional(monkeypatch):
    # A deployment may keep its Telegram creds set while temporarily
    # flipping the transport off; load_config should still read them.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "777")
    from vexis_agent.core.config import load_config

    cfg = load_config(require_telegram=False)
    assert cfg.telegram_bot_token == "123:abc"
    assert cfg.telegram_allowed_user_id == 777


# ---------------------------------------------------------------------------
# bg_spawn control-socket op honours background_tasks.enabled.
# ---------------------------------------------------------------------------


def test_bg_spawn_blocked_when_disabled(monkeypatch):
    import asyncio

    from vexis_agent import main as main_mod

    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.background_tasks_enabled", lambda: False
    )
    bg = mock.MagicMock()  # spawn must NOT be reached
    dispatch = main_mod._build_dispatch(bg)

    result = asyncio.run(
        dispatch("bg_spawn", {"chat_id": 1, "name": "t", "prompt": "do a thing"})
    )

    assert result["ok"] is False
    assert result["kind"] == "Disabled"
    bg.spawn.assert_not_called()


def test_bg_spawn_passes_gate_when_enabled(monkeypatch):
    # Default-on: the gate lets the op through to arg validation. Bad
    # args (here: a non-int chat_id) prove we got PAST the Disabled gate
    # without having to drive a real spawn.
    import asyncio

    from vexis_agent import main as main_mod

    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.background_tasks_enabled", lambda: True
    )
    bg = mock.MagicMock()
    dispatch = main_mod._build_dispatch(bg)

    result = asyncio.run(
        dispatch("bg_spawn", {"chat_id": "not-an-int", "name": "t", "prompt": "x"})
    )

    assert result["ok"] is False
    assert result["kind"] == "BadRequest"
    bg.spawn.assert_not_called()
