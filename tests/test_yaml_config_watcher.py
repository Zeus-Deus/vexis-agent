"""yaml_config helpers for the Codemux watcher cadence knobs.

Pins the defaults the docstring promises so a drive-by change to
``DEFAULT_WATCHER_POLL_INTERVAL_SECONDS`` (or its sibling) trips a
test instead of silently lengthening notification latency for
everyone on the next release.

Range clamps are pinned the same way: an out-of-band value falls
back to the default with a warning. Symptoms of a regression: the
user types ``poll_interval_seconds: 0.0`` to disable polling
entirely, the loop tight-spins, the laptop catches fire. Don't.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from vexis_agent.core import yaml_config


def _patch_config(tmp_path: Path, body: str | None):
    if body is not None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(body, encoding="utf-8")

    def fake_vexis_dir() -> Path:
        return tmp_path

    return mock.patch(
        "vexis_agent.core.yaml_config.vexis_dir",
        side_effect=fake_vexis_dir,
    )


def test_poll_interval_default(tmp_path):
    with _patch_config(tmp_path, body=None):
        assert yaml_config.watcher_poll_interval_seconds() == 5.0


def test_oscillation_default(tmp_path):
    with _patch_config(tmp_path, body=None):
        assert yaml_config.watcher_oscillation_window_seconds() == 60.0


def test_poll_interval_override_accepted(tmp_path):
    body = "watcher:\n  poll_interval_seconds: 2.5\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.watcher_poll_interval_seconds() == 2.5


def test_oscillation_override_accepted(tmp_path):
    body = "watcher:\n  oscillation_window_seconds: 300\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.watcher_oscillation_window_seconds() == 300.0


def test_poll_interval_below_floor_falls_back(tmp_path, caplog):
    import logging
    body = "watcher:\n  poll_interval_seconds: 0.1\n"
    with _patch_config(tmp_path, body=body):
        with caplog.at_level(logging.WARNING, logger="vexis_agent.core.yaml_config"):
            assert yaml_config.watcher_poll_interval_seconds() == 5.0
    assert any(
        "watcher.poll_interval_seconds" in r.getMessage()
        for r in caplog.records
    )


def test_poll_interval_above_ceiling_falls_back(tmp_path):
    body = "watcher:\n  poll_interval_seconds: 9999\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.watcher_poll_interval_seconds() == 5.0


def test_oscillation_zero_allowed_for_disable(tmp_path):
    """``oscillation_window_seconds: 0`` is a documented "disable
    debounce" — every transition pings. Floor is 0.0, not 1.0."""
    body = "watcher:\n  oscillation_window_seconds: 0\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.watcher_oscillation_window_seconds() == 0.0


def test_bool_value_does_not_silently_coerce(tmp_path):
    """``true`` would otherwise coerce to 1.0 via int subclass."""
    body = "watcher:\n  poll_interval_seconds: true\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.watcher_poll_interval_seconds() == 5.0


def test_garbage_value_falls_back(tmp_path):
    body = "watcher:\n  poll_interval_seconds: not-a-number\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.watcher_poll_interval_seconds() == 5.0
