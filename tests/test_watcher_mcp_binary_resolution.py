"""Binary-name resolution for the Codemux MCP stdio client.

Bug context: ``CODEMUX_BINARY`` used to be hardcoded to ``"codemux"``
and the watcher preflight died on hosts where the user's
``~/.vexis/mcp-servers.yaml`` pointed at a different binary (e.g.
a side-loaded ``codemux-remote`` build). This file pins the new
precedence chain:

  1. Explicit ``binary=...`` kwarg (legacy tests rely on this).
  2. ``$VEXIS_CODEMUX_BINARY`` environment variable.
  3. ``command`` field of the ``name: codemux`` entry in
     ``~/.vexis/mcp-servers.yaml``.
  4. Literal ``"codemux"`` fallback (vanilla install).

The autouse ``_isolate_vexis_dir`` conftest fixture redirects
``core.paths.vexis_dir()`` at a per-test tmp dir, so each test can
drop a YAML file under that dir without contaminating the real
``~/.vexis/``. We also clear ``VEXIS_CODEMUX_BINARY`` per-test to
keep the precedence assertions independent of the developer's
shell env.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import pytest

from vexis_agent.core.watcher.mcp_client import (
    CODEMUX_BINARY,
    VEXIS_CODEMUX_BINARY_ENV,
    CodemuxMcpClient,
    _binary_from_mcp_yaml,
    _resolve_binary,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no env override so precedence is unambiguous."""
    monkeypatch.delenv(VEXIS_CODEMUX_BINARY_ENV, raising=False)


def _write_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    """Write a ``mcp-servers.yaml`` under the isolated vexis_dir.

    The autouse conftest fixture patches ``paths.vexis_dir`` to a
    different tmp subdir per test; we re-patch it here to ``tmp_path``
    directly so the test owns the location it wrote to.
    """
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: tmp_path)
    (tmp_path / "mcp-servers.yaml").write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# _resolve_binary precedence
# ---------------------------------------------------------------------------


def test_resolve_default_is_codemux_literal():
    """Vanilla case — no env, no YAML, no override — falls through to ``codemux``."""
    assert _resolve_binary() == CODEMUX_BINARY


def test_resolve_env_var_wins(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(VEXIS_CODEMUX_BINARY_ENV, "/opt/codemux/bin/codemux-remote")
    assert _resolve_binary() == "/opt/codemux/bin/codemux-remote"


def test_resolve_reads_command_from_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _write_yaml(monkeypatch, tmp_path, (
        "servers:\n"
        "  - name: codemux\n"
        "    command: /tmp/fake-codemux-remote\n"
        "    args: [mcp]\n"
    ))
    assert _resolve_binary() == "/tmp/fake-codemux-remote"


def test_resolve_env_beats_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """Precedence pin: env var trumps the YAML lookup."""
    _write_yaml(monkeypatch, tmp_path, (
        "servers:\n"
        "  - name: codemux\n"
        "    command: /tmp/yaml-binary\n"
    ))
    monkeypatch.setenv(VEXIS_CODEMUX_BINARY_ENV, "/tmp/env-binary")
    assert _resolve_binary() == "/tmp/env-binary"


def test_resolve_yaml_beats_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """Precedence pin: YAML command trumps the ``codemux`` fallback."""
    _write_yaml(monkeypatch, tmp_path, (
        "servers:\n"
        "  - name: codemux\n"
        "    command: codemux-from-yaml\n"
    ))
    assert _resolve_binary() == "codemux-from-yaml"


def test_resolve_ignores_yaml_entries_with_other_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """A YAML with only non-codemux entries falls through to the default."""
    _write_yaml(monkeypatch, tmp_path, (
        "servers:\n"
        "  - name: omarchy-kb\n"
        "    command: /usr/bin/omarchy-kb\n"
    ))
    assert _resolve_binary() == CODEMUX_BINARY


def test_resolve_yaml_picks_codemux_when_multiple_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _write_yaml(monkeypatch, tmp_path, (
        "servers:\n"
        "  - name: omarchy-kb\n"
        "    command: /usr/bin/omarchy-kb\n"
        "  - name: codemux\n"
        "    command: codemux-remote\n"
    ))
    assert _resolve_binary() == "codemux-remote"


# ---------------------------------------------------------------------------
# _binary_from_mcp_yaml resilience
# ---------------------------------------------------------------------------


def test_yaml_missing_file_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: tmp_path)
    assert _binary_from_mcp_yaml() is None


def test_yaml_malformed_silently_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """A YAML drift must not crash the resolver — fall through, don't raise."""
    _write_yaml(monkeypatch, tmp_path, "servers: [not a mapping\n")
    # Should not raise; resolver continues to the default.
    assert _binary_from_mcp_yaml() is None
    assert _resolve_binary() == CODEMUX_BINARY


def test_yaml_url_entry_not_mistaken_for_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """Remote (URL-only) codemux entries don't have a ``command`` field."""
    _write_yaml(monkeypatch, tmp_path, (
        "servers:\n"
        "  - name: codemux\n"
        "    url: https://example.com/mcp\n"
    ))
    assert _binary_from_mcp_yaml() is None


# ---------------------------------------------------------------------------
# CodemuxMcpClient construction wires the resolved binary through
# ---------------------------------------------------------------------------


def test_client_explicit_arg_bypasses_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """Spec contract: ``binary=...`` is verbatim, no env / YAML lookup."""
    monkeypatch.setenv(VEXIS_CODEMUX_BINARY_ENV, "should-be-ignored")
    _write_yaml(monkeypatch, tmp_path, (
        "servers:\n"
        "  - name: codemux\n"
        "    command: also-should-be-ignored\n"
    ))
    client = CodemuxMcpClient(binary="codemux")
    assert client._binary == "codemux"


def test_client_no_arg_uses_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _write_yaml(monkeypatch, tmp_path, (
        "servers:\n"
        "  - name: codemux\n"
        "    command: /tmp/fake-codemux-remote\n"
    ))
    client = CodemuxMcpClient()
    assert client._binary == "/tmp/fake-codemux-remote"


def test_client_no_arg_no_yaml_falls_back_to_default():
    """Vanilla setup: no env, no YAML override, no kwarg → ``codemux``."""
    client = CodemuxMcpClient()
    assert client._binary == CODEMUX_BINARY


# ---------------------------------------------------------------------------
# Subprocess spawn uses the resolved binary
# ---------------------------------------------------------------------------


class _FakeProc:
    """Stub for ``asyncio.subprocess.Process`` — never actually I/Os."""

    def __init__(self) -> None:
        self.returncode: Optional[int] = None
        self.stdin = None
        self.stdout = None


def test_ensure_running_spawns_resolved_binary_with_mcp_subcommand(
    monkeypatch: pytest.MonkeyPatch,
):
    """Preflight regression: with ``binary="codemux-remote"`` and a fake
    ``shutil.which`` that returns a path, the client must call
    ``asyncio.create_subprocess_exec(codemux-remote, "mcp", ...)``.

    We stub the initialize handshake too — we're not testing JSON-RPC
    here, just the argv. The handshake stub leaves ``self._proc`` in
    place so the post-init check (returncode None) doesn't fire.
    """
    monkeypatch.setattr(
        "vexis_agent.core.watcher.mcp_client.shutil.which",
        lambda b: f"/usr/local/bin/{b}",
    )

    spawn_calls: list[tuple] = []

    async def _fake_spawn(*args, **kwargs):
        spawn_calls.append(args)
        return _FakeProc()

    monkeypatch.setattr(
        "vexis_agent.core.watcher.mcp_client.asyncio.create_subprocess_exec",
        _fake_spawn,
    )

    async def _no_init(self, proc):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(
        CodemuxMcpClient, "_initialize", _no_init,
    )

    client = CodemuxMcpClient(binary="codemux-remote")

    async def _run() -> None:
        proc = await client._ensure_running()
        assert proc is not None

    asyncio.run(_run())

    assert spawn_calls == [("codemux-remote", "mcp")], (
        f"expected ('codemux-remote', 'mcp'); got {spawn_calls!r}"
    )


def test_ensure_running_preflight_uses_resolved_binary_in_error_message(
    monkeypatch: pytest.MonkeyPatch,
):
    """The PATH-miss error mentions the binary name the client *actually*
    tried — surfaces the misconfiguration clearly instead of always
    saying ``'codemux' not on PATH`` even when the user pointed at
    ``codemux-remote``."""
    monkeypatch.setattr(
        "vexis_agent.core.watcher.mcp_client.shutil.which",
        lambda b: None,
    )

    from vexis_agent.core.watcher.mcp_client import CodemuxMcpUnavailable

    client = CodemuxMcpClient(binary="codemux-remote")

    async def _run() -> None:
        with pytest.raises(CodemuxMcpUnavailable) as exc_info:
            await client._ensure_running()
        assert "codemux-remote" in str(exc_info.value)

    asyncio.run(_run())
