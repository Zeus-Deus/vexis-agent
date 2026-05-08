"""Tests for the stale-bundle detector at dashboard startup.

Pin the 2026-05-08 safety net: when ``web/src/`` source files
are newer than the compiled ``web/dist/assets/index-*.js``
bundle, the daemon logs a banner WARNING so the user knows to
run ``npm run build``. Belt-and-suspenders for the pre-commit
hook (which auto-rebuilds on commit but isn't installed in
fresh clones until ``scripts/install.py`` runs).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from core.web_server import _warn_if_dashboard_bundle_stale


def _touch(path: Path, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_detector_silent_when_bundle_is_newer(tmp_path, caplog):
    """Happy path: bundle is newer than every source file → no
    warning. Production state right after ``npm run build``."""
    web = tmp_path / "web"
    src = web / "src"
    dist = web / "dist" / "assets"
    _touch(src / "pages" / "ModelsPage.tsx", mtime=1_700_000_000)
    _touch(dist / "index-abc.js", mtime=1_700_000_100)

    caplog.set_level(logging.WARNING, logger="core.web_server")
    _warn_if_dashboard_bundle_stale(web / "dist")
    assert not any("STALE DASHBOARD BUNDLE" in r.message for r in caplog.records)


def test_detector_warns_when_source_is_newer(tmp_path, caplog):
    """Trip the trap: source edited after bundle build → banner
    WARNING with the offending source path + both mtimes."""
    web = tmp_path / "web"
    src = web / "src"
    dist = web / "dist" / "assets"
    _touch(src / "pages" / "ModelsPage.tsx", mtime=1_700_000_500)
    _touch(dist / "index-abc.js", mtime=1_700_000_100)

    caplog.set_level(logging.WARNING, logger="core.web_server")
    _warn_if_dashboard_bundle_stale(web / "dist")

    msgs = [r.message for r in caplog.records]
    banner = next((m for m in msgs if "STALE DASHBOARD BUNDLE" in m), None)
    assert banner is not None, f"banner not in {msgs}"
    assert "ModelsPage.tsx" in banner
    assert "npm run build" in banner


def test_detector_silent_when_dist_missing(tmp_path, caplog):
    """Fresh checkout, no build yet → silent. The dashboard route
    will 404 until a build happens; no need to spam WARNINGs at
    every restart in that state."""
    web = tmp_path / "web"
    _touch(web / "src" / "pages" / "ModelsPage.tsx")
    # no web/dist created

    caplog.set_level(logging.WARNING, logger="core.web_server")
    _warn_if_dashboard_bundle_stale(web / "dist")
    assert not any("STALE DASHBOARD BUNDLE" in r.message for r in caplog.records)


def test_detector_silent_when_src_missing(tmp_path, caplog):
    """Test fixture or tarball install with no source tree →
    silent. Nothing to compare against."""
    web = tmp_path / "web"
    dist = web / "dist" / "assets"
    _touch(dist / "index-abc.js")
    # no web/src

    caplog.set_level(logging.WARNING, logger="core.web_server")
    _warn_if_dashboard_bundle_stale(web / "dist")
    assert not any("STALE DASHBOARD BUNDLE" in r.message for r in caplog.records)


def test_detector_only_watches_relevant_extensions(tmp_path, caplog):
    """Non-source files (e.g. README, .gitignore) being newer than
    the bundle is irrelevant — only ``.tsx``/``.ts``/``.css``/``.html``
    trigger the check."""
    web = tmp_path / "web"
    src = web / "src"
    dist = web / "dist" / "assets"
    _touch(dist / "index-abc.js", mtime=1_700_000_100)
    # All sources older than bundle.
    _touch(src / "pages" / "ModelsPage.tsx", mtime=1_700_000_000)
    # Newer non-source files — must NOT trigger the warning.
    _touch(src / "README.md", mtime=1_700_999_999)
    _touch(src / ".gitkeep", mtime=1_700_999_999)

    caplog.set_level(logging.WARNING, logger="core.web_server")
    _warn_if_dashboard_bundle_stale(web / "dist")
    assert not any("STALE DASHBOARD BUNDLE" in r.message for r in caplog.records)


def test_detector_finds_newest_source_across_subdirs(tmp_path, caplog):
    """Walks the source tree recursively; the offending file in
    the warning is the newest one found anywhere under
    ``web/src/``."""
    web = tmp_path / "web"
    src = web / "src"
    dist = web / "dist" / "assets"
    _touch(dist / "index-abc.js", mtime=1_700_000_100)
    _touch(src / "components" / "Old.tsx", mtime=1_700_000_050)
    _touch(src / "lib" / "Newer.ts", mtime=1_700_000_500)
    _touch(src / "pages" / "Newest.tsx", mtime=1_700_000_900)

    caplog.set_level(logging.WARNING, logger="core.web_server")
    _warn_if_dashboard_bundle_stale(web / "dist")
    msgs = [r.message for r in caplog.records]
    banner = next((m for m in msgs if "STALE DASHBOARD BUNDLE" in m), None)
    assert banner is not None
    # Newest file referenced.
    assert "Newest.tsx" in banner
    # Older files NOT referenced.
    assert "Old.tsx" not in banner
    assert "Newer.ts" not in banner


def test_detector_handles_empty_dist_assets(tmp_path, caplog):
    """``web/dist/assets/`` exists but is empty (failed build,
    cleaned by hand). Defensive: no comparison possible → silent
    rather than crashing on ``max([])``."""
    web = tmp_path / "web"
    src = web / "src"
    dist = web / "dist" / "assets"
    dist.mkdir(parents=True)
    _touch(src / "pages" / "ModelsPage.tsx", mtime=1_700_000_500)

    caplog.set_level(logging.WARNING, logger="core.web_server")
    _warn_if_dashboard_bundle_stale(web / "dist")
    assert not any("STALE DASHBOARD BUNDLE" in r.message for r in caplog.records)
