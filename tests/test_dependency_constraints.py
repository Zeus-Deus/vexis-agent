"""Packaging invariants for pyproject.toml direct dependencies.

CI once resolved MCP 2.x transitively, which dropped
``mcp.server.fastmcp.FastMCP`` (imported by
vexis_agent/tools/browser/mcp_server.py) and broke the install. This
guards that pyproject.toml declares a direct ``mcp`` dependency
compatible with the 1.28.x line Vexis actually imports.
"""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _mcp_requirements():
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    reqs = [Requirement(dep) for dep in deps]
    return [r for r in reqs if r.name == "mcp"]


def test_exactly_one_direct_mcp_dependency():
    mcp_reqs = _mcp_requirements()
    assert len(mcp_reqs) == 1, (
        "pyproject.toml [project].dependencies must declare exactly one "
        "direct 'mcp' requirement (found "
        f"{len(mcp_reqs)}) — vexis_agent/tools/browser/mcp_server.py "
        "imports mcp.server.fastmcp.FastMCP directly."
    )


def test_mcp_dependency_allows_1x_and_excludes_2x():
    req = _mcp_requirements()[0]
    assert req.specifier.contains(Version("1.28.1"), prereleases=True), (
        "mcp requirement must be satisfied by 1.28.1 (the version Vexis "
        "is compatible with)"
    )
    assert not req.specifier.contains(Version("2.0.0"), prereleases=True), (
        "mcp requirement must exclude 2.0.0 — mcp.server.fastmcp.FastMCP "
        "is unavailable there until an adapter migration"
    )
