"""Browser config back-compat: ``addons.browser.*`` wins, legacy
``[browser]`` still honoured.

The browser is a bundled add-on, so its config canonically lives under
``addons.browser.*``. Configs written before the extraction used the
top-level ``[browser]`` block; those must keep working unchanged. The
merge lives in ``core.yaml_config._browser_section`` (addon per-key,
legacy fills gaps) so every browser reader — and the dashboard payload
— agrees.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.core import yaml_config


def _point_config(monkeypatch, tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "vexis"
    cfg_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr("vexis_agent.core.yaml_config.vexis_dir", lambda: cfg_dir)
    return cfg_dir / "config.yaml"


def test_legacy_browser_block_still_read(monkeypatch, tmp_path):
    cfg = _point_config(monkeypatch, tmp_path)
    cfg.write_text(
        "browser:\n  headless: false\n  captcha_solver: capsolver\n"
        "  captcha_solver_api_key: legacy_k\n  default_profile: legacyprof\n",
        encoding="utf-8",
    )
    assert yaml_config.browser_headless() is False
    assert yaml_config.browser_captcha_solver() == "capsolver"
    assert yaml_config.browser_captcha_solver_api_key() == "legacy_k"
    assert yaml_config.browser_default_profile() == "legacyprof"


def test_addons_browser_block_read(monkeypatch, tmp_path):
    cfg = _point_config(monkeypatch, tmp_path)
    cfg.write_text(
        "addons:\n  browser:\n    headless: false\n"
        "    captcha_solver: twocaptcha\n    captcha_solver_api_key: addon_k\n",
        encoding="utf-8",
    )
    assert yaml_config.browser_headless() is False
    assert yaml_config.browser_captcha_solver() == "twocaptcha"
    assert yaml_config.browser_captcha_solver_api_key() == "addon_k"


def test_addons_browser_wins_over_legacy_per_key(monkeypatch, tmp_path):
    cfg = _point_config(monkeypatch, tmp_path)
    cfg.write_text(
        "browser:\n  captcha_solver: capsolver\n"
        "  captcha_solver_api_key: legacy_k\n  default_profile: legacyprof\n"
        "addons:\n  browser:\n    captcha_solver: twocaptcha\n",
        encoding="utf-8",
    )
    # addon value wins where set...
    assert yaml_config.browser_captcha_solver() == "twocaptcha"
    # ...legacy fills the gaps the addon block didn't set.
    assert yaml_config.browser_captcha_solver_api_key() == "legacy_k"
    assert yaml_config.browser_default_profile() == "legacyprof"


def test_defaults_when_nothing_configured(monkeypatch, tmp_path):
    _point_config(monkeypatch, tmp_path)
    assert yaml_config.browser_headless() is True
    assert yaml_config.browser_captcha_solver() == "none"
    assert yaml_config.browser_captcha_solver_api_key() is None


# --- navigation_timeout_recycle_threshold knob (issue #55) ----------------


def test_recycle_threshold_default_is_three(monkeypatch, tmp_path):
    _point_config(monkeypatch, tmp_path)
    assert yaml_config.browser_navigation_timeout_recycle_threshold() == 3


def test_recycle_threshold_explicit_zero_disables(monkeypatch, tmp_path):
    cfg = _point_config(monkeypatch, tmp_path)
    cfg.write_text(
        "addons:\n  browser:\n    navigation_timeout_recycle_threshold: 0\n",
        encoding="utf-8",
    )
    # 0 is the one value that survives the minimum=0 floor: feature off.
    assert yaml_config.browser_navigation_timeout_recycle_threshold() == 0


def test_recycle_threshold_negative_and_garbage_fall_back(monkeypatch, tmp_path):
    cfg = _point_config(monkeypatch, tmp_path)
    cfg.write_text(
        "addons:\n  browser:\n    navigation_timeout_recycle_threshold: -5\n",
        encoding="utf-8",
    )
    # Negative is below the minimum -> default 3 (only literal 0 disables).
    assert yaml_config.browser_navigation_timeout_recycle_threshold() == 3
    cfg.write_text(
        "addons:\n  browser:\n    navigation_timeout_recycle_threshold: nope\n",
        encoding="utf-8",
    )
    assert yaml_config.browser_navigation_timeout_recycle_threshold() == 3


def test_recycle_threshold_addon_beats_legacy(monkeypatch, tmp_path):
    cfg = _point_config(monkeypatch, tmp_path)
    cfg.write_text(
        "browser:\n  navigation_timeout_recycle_threshold: 7\n"
        "addons:\n  browser:\n    navigation_timeout_recycle_threshold: 2\n",
        encoding="utf-8",
    )
    assert yaml_config.browser_navigation_timeout_recycle_threshold() == 2
