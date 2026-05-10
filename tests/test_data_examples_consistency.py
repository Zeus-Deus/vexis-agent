"""Phase 4 — keep the repo-root example files in sync with their
package-data twins.

The wheel ships ``vexis_agent/data/{config.example.yaml,dotenv.example}``
(read by ``vexis-agent setup`` via ``importlib.resources``). The
human-readable counterparts at the repo root (``config.example.yaml``,
``.env.example``) exist for users browsing on GitHub.

Drift between the two would mean the wizard writes one schema while
the docs show another. This test fails fast when they diverge.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_config_example_matches_repo_root() -> None:
    repo_copy = (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8")
    pkg_copy = (
        REPO_ROOT / "vexis_agent" / "data" / "config.example.yaml"
    ).read_text(encoding="utf-8")
    assert repo_copy == pkg_copy, (
        "config.example.yaml diverged between repo root and "
        "vexis_agent/data/. When you edit one, edit the other (or "
        "make the repo-root copy a symlink — but symlinks won't ship "
        "in a wheel, so the duplication is intentional)."
    )


def test_dotenv_example_matches_repo_root() -> None:
    repo_copy = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    pkg_copy = (
        REPO_ROOT / "vexis_agent" / "data" / "dotenv.example"
    ).read_text(encoding="utf-8")
    assert repo_copy == pkg_copy, (
        "Repo-root .env.example diverged from "
        "vexis_agent/data/dotenv.example."
    )
