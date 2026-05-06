"""Cross-brain test infrastructure for the brain abstraction.

Provides a parameterised ``brain_under_test`` fixture so the same
test body can run against multiple brain implementations:

    @pytest.mark.parametrize(
        "brain_under_test", ["null", "claude_code", "opencode"],
        indirect=True,
    )
    def test_routes_through_brain(brain_under_test):
        ...

Default scope is the null brain — most tests don't need to spawn a
real claude-code or opencode process and the null brain has zero
dependency surface. Tests that opt into the parametrisation gain
coverage across all brain implementations; failures there flag
refactor bugs that broke one brain but not the others.

Phase C Day 3 adds ``"opencode"``. The smoke suite (real binary)
stays opt-in via ``@pytest.mark.brain_smoke{,_opencode}``.

Design citation: ``.plans/brain-abstraction-research.md`` §7
"Cross-brain test suite".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.brain.base import Brain
from core.brain.claude_code import ClaudeCodeBrain
from core.brain.null import BrainNull
from core.brain.opencode import OpenCodeBrain
from core.running_tasks import RunningTasks
from core.sessions import SessionStore


def _make_brain(kind: str, tmp_path: Path) -> Brain:
    """Construct one brain implementation against tmp paths so the
    fixture cannot leak state into the user's real ~/.vexis/.

    BrainNull: trivial — zero deps, zero subprocess.
    ClaudeCodeBrain / OpenCodeBrain: construct against a tmp
        workspace + tmp SessionStore + a fresh RunningTasks. The
        brains are NEVER spawned during the cross-brain tests —
        the parametrised tests assert on inspection-only methods
        or use the brain only for its type-binding to the ABC.
    """
    if kind == "null":
        return BrainNull()
    if kind == "claude_code":
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)
        session = SessionStore(tmp_path / "sessions.json")
        return ClaudeCodeBrain(
            workspace=workspace,
            session=session,
            running_tasks=RunningTasks(),
        )
    if kind == "opencode":
        workspace = tmp_path / "ws-opencode"
        workspace.mkdir(parents=True, exist_ok=True)
        session = SessionStore(tmp_path / "sessions-opencode.json")
        return OpenCodeBrain(
            workspace=workspace,
            session=session,
            running_tasks=RunningTasks(),
        )
    raise ValueError(
        f"unknown brain_under_test param: {kind!r} "
        f"(expected one of: 'null', 'claude_code', 'opencode')"
    )


@pytest.fixture
def brain_under_test(request: pytest.FixtureRequest, tmp_path: Path) -> Brain:
    """Parameterised brain factory. Use with ``indirect=True``:

        @pytest.mark.parametrize(
            "brain_under_test",
            ["null", "claude_code", "opencode"],
            indirect=True,
        )

    The default param when the fixture is consumed without
    parametrisation is ``"null"`` (cheap, no subprocess)."""
    kind = getattr(request, "param", "null")
    return _make_brain(kind, tmp_path)
