"""Resolution of the dashboard frontend bundle path.

Background: prior to v0.1.4, ``main.py`` hard-coded
``Path(__file__).resolve().parent.parent / "web" / "dist"`` which only
exists in source checkouts. Pipx installs put the package under
``site-packages/vexis_agent/`` where ``../web/dist`` is a nonexistent
sibling — every dashboard hit returned ``frontend not built``.

The fix bundles the built frontend under ``vexis_agent/web_dist/``
(included via ``[tool.setuptools.package-data]``) and adds a resolver
that prefers the bundled location, falling back to ``<repo>/web/dist``
for editable / source-checkout installs so dev workflow still works.

These tests pin both code paths so a future refactor can't silently
re-break the pipx install.
"""

from __future__ import annotations

from pathlib import Path

from vexis_agent import main as vexis_main


def test_resolve_web_dist_prefers_bundled_when_index_html_present(
    tmp_path, monkeypatch
) -> None:
    """Bundled location takes priority — that's the path pipx users
    hit. The resolver checks for ``index.html`` (a known-stable
    artifact name across vite builds) rather than just ``exists()``,
    so an empty placeholder dir doesn't fool it."""
    fake_pkg = tmp_path / "vexis_agent"
    fake_pkg.mkdir()
    fake_main = fake_pkg / "main.py"
    fake_main.write_text("# fake")
    bundled = fake_pkg / "web_dist"
    bundled.mkdir()
    (bundled / "index.html").write_text("<html></html>")

    monkeypatch.setattr(vexis_main, "__file__", str(fake_main))

    resolved = vexis_main._resolve_web_dist()
    assert resolved == bundled


def test_resolve_web_dist_falls_back_to_source_checkout(
    tmp_path, monkeypatch
) -> None:
    """Editable install: package is under <repo>/vexis_agent/, no
    bundled web_dist (because the source repo's vexis_agent/web_dist
    is gitignored or hasn't been populated yet for dev work). The
    resolver should find <repo>/web/dist."""
    repo = tmp_path / "repo"
    pkg = repo / "vexis_agent"
    pkg.mkdir(parents=True)
    fake_main = pkg / "main.py"
    fake_main.write_text("# fake")
    # No vexis_agent/web_dist/ — but a sibling web/dist exists
    source_dist = repo / "web" / "dist"
    source_dist.mkdir(parents=True)
    (source_dist / "index.html").write_text("<html></html>")

    monkeypatch.setattr(vexis_main, "__file__", str(fake_main))

    resolved = vexis_main._resolve_web_dist()
    assert resolved == source_dist


def test_resolve_web_dist_returns_bundled_path_when_neither_exists(
    tmp_path, monkeypatch
) -> None:
    """Nothing built anywhere → return the bundled path so the
    eventual ``frontend not built`` 404 points at the location the
    user expects for a healthy install. Prevents confusing dev
    error messages that point at a nested source checkout location."""
    fake_pkg = tmp_path / "vexis_agent"
    fake_pkg.mkdir()
    fake_main = fake_pkg / "main.py"
    fake_main.write_text("# fake")

    monkeypatch.setattr(vexis_main, "__file__", str(fake_main))

    resolved = vexis_main._resolve_web_dist()
    assert resolved == fake_pkg / "web_dist"


def test_bundled_web_dist_actually_ships_in_this_checkout() -> None:
    """Smoke test the live repo: ``vexis_agent/web_dist/index.html``
    must exist or the wheel built from this checkout will reproduce
    the v0.1.0 ``frontend not built`` bug. Also pins package-data
    coverage in pyproject.toml — if someone removes the include
    pattern, this test won't fire (the file's still there) but the
    resolver still works for editable installs, so this is the
    single canary that asserts "release-ready"."""
    pkg_dir = Path(vexis_main.__file__).resolve().parent
    bundled = pkg_dir / "web_dist"
    assert bundled.exists(), (
        f"vexis_agent/web_dist/ is missing — run "
        f"'cd web && npm run build && cp -r web/dist {bundled}' "
        f"before tagging a release."
    )
    assert (bundled / "index.html").exists(), (
        f"{bundled}/index.html missing — bundle is incomplete; "
        f"the dashboard will 404 'frontend not built' on every hit."
    )
