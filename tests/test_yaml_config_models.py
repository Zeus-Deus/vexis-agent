"""Tests for the [models] block in core/yaml_config.

The block governs which model each internal claude -p subprocess
runs against. resolve_model_flag is the small but load-bearing
translator that turns config strings into argv flags.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from core import yaml_config


# --------------------------------------------------------------------
# resolve_model_flag — pure function, no fs
# --------------------------------------------------------------------


def test_resolve_model_flag_default_returns_empty():
    """The 'default' sentinel produces no --model flag so claude -p
    falls back to the account default. Empty list (not None) lets
    callers splat into argv unconditionally."""
    assert yaml_config.resolve_model_flag("default") == []
    assert yaml_config.resolve_model_flag("DEFAULT") == []
    assert yaml_config.resolve_model_flag("Default") == []


def test_resolve_model_flag_empty_inputs_return_empty():
    """Empty / whitespace / None / non-string inputs all collapse to
    'no override' — the safe choice when config is missing/malformed."""
    assert yaml_config.resolve_model_flag("") == []
    assert yaml_config.resolve_model_flag("   ") == []
    assert yaml_config.resolve_model_flag(None) == []  # type: ignore[arg-type]
    assert yaml_config.resolve_model_flag(123) == []  # type: ignore[arg-type]


def test_resolve_model_flag_short_aliases():
    assert yaml_config.resolve_model_flag("sonnet") == ["--model", "sonnet"]
    assert yaml_config.resolve_model_flag("haiku") == ["--model", "haiku"]
    assert yaml_config.resolve_model_flag("opus") == ["--model", "opus"]


def test_resolve_model_flag_full_model_id():
    """Users can specify a pinned model id (e.g. for reproducible
    eval runs); resolver passes it through verbatim."""
    assert yaml_config.resolve_model_flag("claude-sonnet-4-6") == [
        "--model", "claude-sonnet-4-6",
    ]


def test_resolve_model_flag_strips_whitespace():
    """YAML round-trips can leave trailing whitespace; strip it so
    --model 'sonnet ' doesn't become a literal arg with a trailing
    space (which claude -p might or might not accept)."""
    assert yaml_config.resolve_model_flag("  sonnet  ") == ["--model", "sonnet"]


# --------------------------------------------------------------------
# model_*() helpers — read from yaml, fall back to defaults
# --------------------------------------------------------------------


def _patch_config(tmp_path: Path, body: str | None) -> None:
    """Point yaml_config at a tmp ~/.vexis/config.yaml with ``body``.

    body=None means no file (test the missing-file path).
    """
    if body is not None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(body, encoding="utf-8")

    def fake_vexis_dir() -> Path:
        return tmp_path

    return mock.patch("core.yaml_config.vexis_dir", side_effect=fake_vexis_dir)


def test_model_brain_defaults_to_default_sentinel(tmp_path):
    """The brain default is intentionally 'default' — internal calls
    overrride to Sonnet but the brain stays on the account default
    so user conversations track the account's chosen capability tier.
    """
    with _patch_config(tmp_path, body=None):
        assert yaml_config.model_brain() == "default"


def test_internal_subsystems_default_to_sonnet(tmp_path):
    with _patch_config(tmp_path, body=None):
        assert yaml_config.model_learning_review() == "sonnet"
        assert yaml_config.model_coherence_judge() == "sonnet"
        assert yaml_config.model_migration_classifier() == "sonnet"


def test_model_overrides_via_yaml(tmp_path):
    body = """
models:
  brain: opus
  learning_review: haiku
  coherence_judge: claude-sonnet-4-6
  migration_classifier: haiku
"""
    with _patch_config(tmp_path, body=body):
        assert yaml_config.model_brain() == "opus"
        assert yaml_config.model_learning_review() == "haiku"
        assert yaml_config.model_coherence_judge() == "claude-sonnet-4-6"
        assert yaml_config.model_migration_classifier() == "haiku"


def test_model_partial_overrides_keep_defaults(tmp_path):
    """Partial config: override one key, the others keep their
    documented defaults."""
    body = """
models:
  coherence_judge: haiku
"""
    with _patch_config(tmp_path, body=body):
        assert yaml_config.model_brain() == "default"
        assert yaml_config.model_learning_review() == "sonnet"
        assert yaml_config.model_coherence_judge() == "haiku"
        assert yaml_config.model_migration_classifier() == "sonnet"


def test_model_malformed_section_falls_back(tmp_path):
    """A non-dict 'models:' value must not break the helpers — same
    posture as the rest of yaml_config (malformed config never bricks
    the daemon)."""
    body = """
models: "not a dict"
"""
    with _patch_config(tmp_path, body=body):
        assert yaml_config.model_brain() == "default"
        assert yaml_config.model_learning_review() == "sonnet"


def test_model_non_string_value_falls_back(tmp_path):
    """A boolean or list under a model key falls back to the default
    string rather than coercing to something nonsensical."""
    body = """
models:
  learning_review: true
  coherence_judge: [a, b]
"""
    with _patch_config(tmp_path, body=body):
        assert yaml_config.model_learning_review() == "sonnet"
        assert yaml_config.model_coherence_judge() == "sonnet"


def test_model_empty_string_falls_back(tmp_path):
    body = """
models:
  learning_review: ""
"""
    with _patch_config(tmp_path, body=body):
        assert yaml_config.model_learning_review() == "sonnet"
