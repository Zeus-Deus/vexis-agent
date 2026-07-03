"""Tailscale Serve plumbing — missing-binary degradation.

A host with no ``tailscale`` binary (the headless web-only container
built by Dockerfile.web-only is the canonical case) must degrade
exactly like a tailscale outage: ``WebDashboard.start()`` catches
``_TailscaleError``, logs a WARNING, and keeps serving on localhost.
Before ``_run_tailscale`` translated the spawn's ``FileNotFoundError``
into ``_TailscaleError``, the exception escaped ``start()``'s handler
and killed the daemon at boot.
"""

from __future__ import annotations

import asyncio

import pytest

from vexis_agent.core import web_server


def test_missing_tailscale_binary_raises_tailscale_error(monkeypatch):
    """FileNotFoundError from the exec seam translates to the localised
    ``_TailscaleError`` so every caller's existing handler applies."""

    async def _missing_binary(binary, argv, timeout):
        raise FileNotFoundError(2, "No such file or directory", "tailscale")

    monkeypatch.setattr(web_server, "run_subprocess", _missing_binary)
    with pytest.raises(web_server._TailscaleError, match="not on PATH"):
        asyncio.run(web_server._tailscale_dns())


def test_tailscale_nonzero_exit_still_raises_tailscale_error(monkeypatch):
    """The pre-existing rc!=0 path is untouched by the translation
    wrapper — a present-but-unhappy tailscale still raises the same
    localised error."""

    async def _unhappy(binary, argv, timeout):
        return 1, b"", b"Logged out."

    monkeypatch.setattr(web_server, "run_subprocess", _unhappy)
    with pytest.raises(web_server._TailscaleError, match="rc=1"):
        asyncio.run(web_server._tailscale_dns())
