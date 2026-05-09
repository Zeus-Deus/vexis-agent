"""Phase 3 — ``vexis-agent doctor`` checks.

Each check is a small function returning a CheckResult with a Status
(OK / WARN / FAIL) plus optional detail and remediation. The CLI runs
``run_all`` and exits 0 if no FAIL appears (WARN doesn't gate exit —
optional checks like linger or service-installed shouldn't break a
healthy install).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from vexis_agent.daemon import doctor as doc


# ── individual check tests ────────────────────────────────────────


def test_python_version_passes_on_runtime() -> None:
    """The test suite runs on the conda env (Python 3.11+), so this
    check is always OK during pytest. Pin the version-string format
    so future ``sys.version`` shape changes get caught here first."""
    result = doc.check_python_version()
    assert result.status is doc.Status.OK
    assert result.detail.split(".")[0] == str(sys.version_info[0])


def test_config_yaml_warn_when_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VEXIS_HOME", str(tmp_path / "v"))
    result = doc.check_config_yaml()
    assert result.status is doc.Status.WARN
    assert "not found" in result.detail


def test_config_yaml_ok_when_valid(tmp_path, monkeypatch) -> None:
    home = tmp_path / "v"
    home.mkdir()
    (home / "config.yaml").write_text("brain:\n  kind: claude-code\n")
    monkeypatch.setenv("VEXIS_HOME", str(home))
    result = doc.check_config_yaml()
    assert result.status is doc.Status.OK


def test_config_yaml_fails_on_malformed(tmp_path, monkeypatch) -> None:
    home = tmp_path / "v"
    home.mkdir()
    (home / "config.yaml").write_text("brain: [unclosed\n")
    monkeypatch.setenv("VEXIS_HOME", str(home))
    result = doc.check_config_yaml()
    assert result.status is doc.Status.FAIL


def test_secrets_pass_via_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VEXIS_HOME", str(tmp_path / "v"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "12345")
    result = doc.check_secrets()
    assert result.status is doc.Status.OK


def test_secrets_pass_via_dotenv(tmp_path, monkeypatch) -> None:
    home = tmp_path / "v"
    home.mkdir()
    (home / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_ALLOWED_USER_ID=12345\n"
    )
    monkeypatch.setenv("VEXIS_HOME", str(home))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    result = doc.check_secrets()
    assert result.status is doc.Status.OK


def test_secrets_fail_when_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VEXIS_HOME", str(tmp_path / "v"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    result = doc.check_secrets()
    assert result.status is doc.Status.FAIL
    assert "TELEGRAM_BOT_TOKEN" in result.detail
    assert "TELEGRAM_ALLOWED_USER_ID" in result.detail


def test_brain_cli_pass_when_on_path(tmp_path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_claude = bin_dir / "claude"
    fake_claude.write_text("")
    fake_claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("VEXIS_HOME", str(tmp_path / "v"))
    result = doc.check_brain_cli()
    assert result.status is doc.Status.OK


def test_brain_cli_fail_when_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setenv("VEXIS_HOME", str(tmp_path / "v"))
    result = doc.check_brain_cli()
    assert result.status is doc.Status.FAIL


def test_null_brain_kind_skips_cli_check(tmp_path, monkeypatch) -> None:
    """The null brain (test fake) has no CLI to install — doctor
    should report OK rather than chasing a missing binary. ``kind``
    must be quoted in YAML; bare ``null`` is the YAML literal None
    which vexis_agent.core.yaml_config.brain_kind treats as "no
    kind set" → default claude-code."""
    home = tmp_path / "v"
    home.mkdir()
    (home / "config.yaml").write_text('brain:\n  kind: "null"\n')
    monkeypatch.setenv("VEXIS_HOME", str(home))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    result = doc.check_brain_cli()
    assert result.status is doc.Status.OK
    assert "null" in result.name


# ── aggregation ───────────────────────────────────────────────────


def test_overall_exit_zero_when_no_fail() -> None:
    results = [
        doc.CheckResult("a", doc.Status.OK),
        doc.CheckResult("b", doc.Status.WARN, remediation="r"),
    ]
    assert doc.overall_exit_code(results) == 0


def test_overall_exit_one_when_any_fail() -> None:
    results = [
        doc.CheckResult("a", doc.Status.OK),
        doc.CheckResult("b", doc.Status.FAIL),
        doc.CheckResult("c", doc.Status.WARN),
    ]
    assert doc.overall_exit_code(results) == 1


def test_format_results_plain_renders_glyphs() -> None:
    results = [
        doc.CheckResult("python", doc.Status.OK, "3.11.5"),
        doc.CheckResult("brain", doc.Status.FAIL, "claude not found", "install it"),
    ]
    out = doc.format_results(results, color=False)
    assert "✓ python  (3.11.5)" in out
    assert "✗ brain" in out
    assert "→ install it" in out


def test_run_all_returns_one_per_default_check() -> None:
    """Sanity guard: run_all() returns the same number of results as
    DEFAULT_CHECKS has entries. Catches accidental duplication / drops."""
    out = doc.run_all()
    assert len(out) == len(doc.DEFAULT_CHECKS)
