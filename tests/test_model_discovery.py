"""Model discovery tests.

Coverage:
- claude-code live discovery against /v1/models (mocked HTTP):
  happy path, fallback on missing auth, fallback on network
  error, fallback on HTTP 401 / non-200, fallback on parse error,
  caching, refresh-busts-cache, ANTHROPIC_API_KEY env precedence,
  aliases always unioned with live ids.
- opencode: subprocess success, FileNotFoundError, TimeoutExpired,
  non-zero exit, empty output
- 5-minute cache: cached calls don't re-run subprocess; expired
  entries refresh
- invalidate_discovery_cache + refresh helpers (both brains)
- discover_models dispatch (claude-code, opencode, unknown)
- discovery_for_validator helper shape
- provider-grouped helpers (Day 1 of model picker UX)

Design citation: ``.plans/model-management-ux-research.md`` §6 Day 4
+ live-discovery work (2026-05-07).
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from core import model_discovery as md


# ──────────────────────────────────────────────────────────────────
# Test setup — clear cache between tests
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_cache():
    md.invalidate_discovery_cache()
    yield
    md.invalidate_discovery_cache()


# ──────────────────────────────────────────────────────────────────
# claude-code: live /v1/models discovery + hardcoded fallback
# ──────────────────────────────────────────────────────────────────


# Helpers — all live-path tests need to control auth + the HTTP
# response. Using monkeypatch for env / token + patch on
# urllib.request.urlopen for the network layer.


def _fake_http_response(payload: dict, status: int = 200) -> MagicMock:
    """Mimic the context-manager interface of urlopen's return."""
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=MagicMock(read=lambda: body, status=status))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _api_payload(*ids: str) -> dict:
    """Shape the real Anthropic /v1/models response — ``data: [{id, ...}]``."""
    return {
        "data": [{"type": "model", "id": i, "display_name": i} for i in ids],
        "has_more": False,
    }


@pytest.fixture
def force_oauth_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provide an OAuth token without depending on a real
    ~/.claude/.credentials.json. Used by happy-path + cache tests.

    Overrides the conftest autouse ``_block_claude_code_live_discovery``
    by re-monkeypatching ``_read_claude_oauth_token`` to return a
    test token. Tests that use this fixture also need to patch
    ``urllib.request.urlopen`` to control the API response."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        md, "_read_claude_oauth_token", lambda: "test-oauth-token-aaaa",
    )
    return "test-oauth-token-aaaa"


@pytest.fixture
def no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both auth paths missing — discovery falls back to hardcoded.

    Inherits the conftest autouse blocker's behavior; this fixture
    is a marker that the test specifically depends on the no-auth
    fallback path so the intent is explicit at the call site."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_discover_claude_code_models_live_happy_path(
    force_oauth_token, caplog,
):
    """API returns 3 ids → discovery returns those PLUS the
    bare aliases (always unioned). Test pins the union: the API
    returns full ids only, but the typed-arg slash path needs
    aliases in the validated set or `claude --model sonnet`
    would trip rule 6."""
    payload = _api_payload(
        "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5",
    )
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_http_response(payload),
    ):
        models = md.discover_claude_code_models()
    # Live ids present.
    assert "claude-opus-4-7" in models
    assert "claude-sonnet-4-6" in models
    assert "claude-haiku-4-5" in models
    # Aliases always unioned (the API never returns them).
    assert "haiku" in models
    assert "sonnet" in models
    assert "opus" in models


def test_discover_claude_code_models_live_uses_oauth_bearer(
    force_oauth_token,
):
    """Auth precedence pin: when ANTHROPIC_API_KEY isn't set, the
    request carries the OAuth bearer in the Authorization header."""
    captured: list[urllib.request.Request] = []

    def _capture(req, **_kw):
        captured.append(req)
        return _fake_http_response(_api_payload("claude-opus-4-7"))

    with patch("urllib.request.urlopen", side_effect=_capture):
        md.discover_claude_code_models()
    assert len(captured) == 1
    req = captured[0]
    assert req.get_header("Authorization") == "Bearer test-oauth-token-aaaa"
    # OAuth path must NOT carry x-api-key.
    assert req.get_header("X-api-key") is None
    # anthropic-version always present.
    assert req.get_header("Anthropic-version") == md._ANTHROPIC_VERSION


def test_discover_claude_code_models_live_env_key_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
):
    """ANTHROPIC_API_KEY env trumps OAuth — power-user opt-in for
    probing a different account than claude-code's logged-in user."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-env-key")
    monkeypatch.setattr(
        md, "_read_claude_oauth_token",
        lambda: (_ for _ in ()).throw(  # type: ignore[misc]
            AssertionError("OAuth path called when env key was set"),
        ),
    )
    captured: list[urllib.request.Request] = []

    def _capture(req, **_kw):
        captured.append(req)
        return _fake_http_response(_api_payload("claude-opus-4-7"))

    with patch("urllib.request.urlopen", side_effect=_capture):
        md.discover_claude_code_models()
    req = captured[0]
    assert req.get_header("X-api-key") == "sk-test-env-key"
    assert req.get_header("Authorization") is None


def test_discover_claude_code_models_live_fallback_on_auth_missing(
    no_auth, caplog,
):
    """No ANTHROPIC_API_KEY and no OAuth file → fall back to the
    hardcoded list (aliases + last-known full names) and log a
    warning. urlopen is NOT called — auth gate fails fast before
    network."""
    import logging
    caplog.set_level(logging.WARNING, logger="core.model_discovery")
    with patch("urllib.request.urlopen") as urlopen_spy:
        models = md.discover_claude_code_models()
    assert urlopen_spy.call_count == 0
    assert models == md._claude_code_fallback()
    assert "haiku" in models
    assert "claude-haiku-4-5" in models
    assert any("no auth available" in r.message for r in caplog.records)


def test_discover_claude_code_models_live_fallback_on_network_error(
    force_oauth_token, caplog,
):
    """urlopen raises URLError (DNS down, connection refused) →
    fall back to hardcoded list + log warning."""
    import logging
    caplog.set_level(logging.WARNING, logger="core.model_discovery")
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("Name or service not known"),
    ):
        models = md.discover_claude_code_models()
    assert models == md._claude_code_fallback()
    assert any(
        "network error" in r.message.lower() for r in caplog.records
    )


def test_discover_claude_code_models_live_fallback_on_401_with_reauth_hint(
    force_oauth_token, caplog,
):
    """401 = expired OAuth or bad key. Falls back AND the warning
    includes a re-auth hint so a daemon-log post-mortem is
    actionable."""
    import logging
    caplog.set_level(logging.WARNING, logger="core.model_discovery")
    err = urllib.error.HTTPError(
        url=md._ANTHROPIC_MODELS_URL, code=401, msg="Unauthorized",
        hdrs=None, fp=None,  # type: ignore[arg-type]
    )
    with patch("urllib.request.urlopen", side_effect=err):
        models = md.discover_claude_code_models()
    assert models == md._claude_code_fallback()
    msgs = " ".join(r.message for r in caplog.records)
    assert "HTTP 401" in msgs
    assert "expired" in msgs.lower() or "refresh" in msgs.lower()


def test_discover_claude_code_models_live_fallback_on_5xx(
    force_oauth_token,
):
    """Upstream API hiccup → fall back. No re-auth hint (not a 401)."""
    err = urllib.error.HTTPError(
        url=md._ANTHROPIC_MODELS_URL, code=503, msg="Service Unavailable",
        hdrs=None, fp=None,  # type: ignore[arg-type]
    )
    with patch("urllib.request.urlopen", side_effect=err):
        models = md.discover_claude_code_models()
    assert models == md._claude_code_fallback()


def test_discover_claude_code_models_live_fallback_on_parse_error(
    force_oauth_token, caplog,
):
    """Malformed JSON / unexpected shape → fall back rather than crash."""
    import logging
    caplog.set_level(logging.WARNING, logger="core.model_discovery")
    bad_response = MagicMock()
    bad_response.__enter__ = MagicMock(
        return_value=MagicMock(read=lambda: b"not json at all"),
    )
    bad_response.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=bad_response):
        models = md.discover_claude_code_models()
    assert models == md._claude_code_fallback()
    assert any(
        "parse failed" in r.message for r in caplog.records
    )


def test_discover_claude_code_models_live_fallback_on_empty_data(
    force_oauth_token, caplog,
):
    """API returns valid JSON but data: [] (shouldn't happen, but
    defensive). Treat as failure → fall back."""
    import logging
    caplog.set_level(logging.WARNING, logger="core.model_discovery")
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_http_response({"data": [], "has_more": False}),
    ):
        models = md.discover_claude_code_models()
    assert models == md._claude_code_fallback()
    assert any("returned 0 models" in r.message for r in caplog.records)


def test_discover_claude_code_models_live_caches_within_5_minutes(
    force_oauth_token,
):
    """Two calls within the TTL hit the API once."""
    payload = _api_payload("claude-opus-4-7")
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_http_response(payload),
    ) as urlopen_spy:
        first = md.discover_claude_code_models()
        second = md.discover_claude_code_models()
    assert first == second
    assert urlopen_spy.call_count == 1


def test_refresh_claude_code_models_busts_cache(force_oauth_token):
    """``refresh_claude_code_models()`` invalidates the cache and
    re-fetches. /model refresh on claude-code calls this; the
    dashboard's refresh button calls this. Pin both effects:
    cache cleared + second urlopen call fires."""
    payload_a = _api_payload("claude-opus-4-7")
    payload_b = _api_payload("claude-opus-4-8")  # post-release
    responses = [
        _fake_http_response(payload_a),
        _fake_http_response(payload_b),
    ]
    with patch("urllib.request.urlopen", side_effect=responses) as urlopen_spy:
        first = md.discover_claude_code_models()
        assert "claude-opus-4-7" in first
        # Without refresh, second call would return cached first.
        # With refresh, the new payload comes through.
        refreshed = md.refresh_claude_code_models()
        assert "claude-opus-4-8" in refreshed
        assert "claude-opus-4-7" not in refreshed
    assert urlopen_spy.call_count == 2


def test_refresh_claude_code_models_returns_fallback_on_failure(
    no_auth,
):
    """Refresh tolerates failures gracefully — returns the
    fallback set rather than crashing the caller."""
    result = md.refresh_claude_code_models()
    assert result == md._claude_code_fallback()


def test_read_claude_oauth_token_missing_file_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
):
    """Defensive: if ~/.claude/.credentials.json doesn't exist
    (claude-code not installed / never authed), return None
    cleanly so the caller can fall back."""
    monkeypatch.setattr(md, "_CLAUDE_OAUTH_PATH", tmp_path / "missing.json")
    assert md._read_claude_oauth_token() is None


def test_read_claude_oauth_token_malformed_json_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
):
    p = tmp_path / "creds.json"
    p.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(md, "_CLAUDE_OAUTH_PATH", p)
    assert md._read_claude_oauth_token() is None


def test_read_claude_oauth_token_happy_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
):
    """Pin the on-disk shape we read from
    ~/.claude/.credentials.json."""
    p = tmp_path / "creds.json"
    p.write_text(
        json.dumps({
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-test-token",
                "expiresAt": 9999999999000,
            },
        }), encoding="utf-8",
    )
    monkeypatch.setattr(md, "_CLAUDE_OAUTH_PATH", p)
    assert md._read_claude_oauth_token() == "sk-ant-oat01-test-token"


# ──────────────────────────────────────────────────────────────────
# opencode: subprocess success
# ──────────────────────────────────────────────────────────────────


def _fake_completed(stdout: str, returncode: int = 0) -> MagicMock:
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.stderr = ""
    cp.returncode = returncode
    return cp


def test_opencode_success_returns_parsed_set():
    fake_stdout = (
        "anthropic/claude-haiku-3-5\n"
        "anthropic/claude-sonnet-4\n"
        "openai/gpt-4o\n"
    )
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ):
        models = md.discover_opencode_models()
    assert models == {
        "anthropic/claude-haiku-3-5",
        "anthropic/claude-sonnet-4",
        "openai/gpt-4o",
    }


def test_opencode_strips_blank_lines():
    fake_stdout = "anthropic/x\n\n  \nopenai/y\n"
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ):
        models = md.discover_opencode_models()
    assert models == {"anthropic/x", "openai/y"}


# ──────────────────────────────────────────────────────────────────
# opencode: failure modes (each → empty set)
# ──────────────────────────────────────────────────────────────────


def test_opencode_missing_binary_returns_empty_set():
    """The realistic case for claude-code-only users. Empty set
    → validator's rule 6 silently skips the membership check."""
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        models = md.discover_opencode_models()
    assert models == set()


def test_opencode_timeout_returns_empty_set_and_warns(caplog):
    """Binary present but slow (models.dev cache miss + slow
    upstream). Log warning so the user knows discovery degraded."""
    import logging
    caplog.set_level(logging.WARNING)
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="opencode", timeout=10),
    ):
        models = md.discover_opencode_models()
    assert models == set()
    assert any("timed out" in r.message for r in caplog.records)


def test_opencode_non_zero_exit_returns_empty_set():
    """Auth issue or models.dev unreachable. Validator's rule 6
    degrades gracefully."""
    with patch(
        "subprocess.run", return_value=_fake_completed("", returncode=1),
    ):
        models = md.discover_opencode_models()
    assert models == set()


def test_opencode_empty_output_returns_empty_set():
    with patch(
        "subprocess.run", return_value=_fake_completed(""),
    ):
        models = md.discover_opencode_models()
    assert models == set()


# ──────────────────────────────────────────────────────────────────
# 5-minute cache behaviour
# ──────────────────────────────────────────────────────────────────


def test_opencode_cached_within_5_minutes():
    """Two calls within the TTL hit the subprocess once."""
    fake_stdout = "anthropic/x\nopenai/y\n"
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ) as run_spy:
        first = md.discover_opencode_models()
        second = md.discover_opencode_models()
    assert first == second
    assert run_spy.call_count == 1


def test_invalidate_clears_specific_brain():
    fake_stdout = "anthropic/x\n"
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ) as run_spy:
        md.discover_opencode_models()  # populates
        md.invalidate_discovery_cache("opencode")
        md.discover_opencode_models()  # re-fetches
    assert run_spy.call_count == 2


def test_invalidate_all_clears_every_brain():
    fake_stdout = "anthropic/x\n"
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ) as run_spy:
        md.discover_opencode_models()
        md.invalidate_discovery_cache(None)
        md.discover_opencode_models()
    assert run_spy.call_count == 2


def test_refresh_opencode_models_invalidates_and_calls_refresh(monkeypatch):
    """refresh_opencode_models busts the cache AND runs
    ``opencode models --refresh`` so models.dev's own cache
    refreshes too. Both subprocess calls fire on success."""
    fake_stdout = "anthropic/x\n"
    calls: list[list[str]] = []

    def _fake_run(argv, *_a, **_k):
        calls.append(list(argv))
        return _fake_completed(fake_stdout)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    md.discover_opencode_models()  # populates cache (1 call)
    result = md.refresh_opencode_models()
    assert result == {"anthropic/x"}
    # Three calls expected: initial discover, refresh subprocess,
    # and the post-refresh discover.
    assert len(calls) == 3
    assert calls[1] == ["opencode", "models", "--refresh"]


def test_refresh_tolerates_subprocess_failure():
    """If ``opencode models --refresh`` fails, the function still
    returns the (possibly empty) discovery result. Refresh is
    best-effort."""
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        result = md.refresh_opencode_models()
    assert result == set()


# ──────────────────────────────────────────────────────────────────
# discover_models dispatch + discovery_for_validator helper
# ──────────────────────────────────────────────────────────────────


def test_discover_models_dispatches_per_brain():
    cc = md.discover_models("claude-code")
    assert "haiku" in cc
    with patch("subprocess.run", return_value=_fake_completed("a/b\n")):
        oc = md.discover_models("opencode")
    assert oc == {"a/b"}


def test_discover_models_unknown_brain_returns_empty():
    """Validator's rule 6 silently skips when the discovered set
    is empty — matches the unknown-brain case."""
    assert md.discover_models("future-brain") == set()
    assert md.discover_models("null") == set()


def test_discovery_for_validator_builds_dict():
    with patch("subprocess.run", return_value=_fake_completed("a/b\n")):
        d = md.discovery_for_validator(["claude-code", "opencode"])
    assert "claude-code" in d
    assert "opencode" in d
    assert "haiku" in d["claude-code"]
    assert d["opencode"] == {"a/b"}


# ──────────────────────────────────────────────────────────────────
# Provider-grouped discovery — Day 1 of model picker UX
# ──────────────────────────────────────────────────────────────────


def test_claude_code_grouped_returns_anthropic_bucket(no_auth):
    """All claude-code models route to Anthropic — single-bucket
    grouping under ``anthropic`` so the picker / dashboard can
    render either brain through the same provider-grouped widget.

    Forced to fallback path (no_auth) so this test is
    deterministic regardless of whether the test environment
    has real Anthropic credentials available."""
    grouped = md.discover_claude_code_models_by_provider()
    assert set(grouped.keys()) == {"anthropic"}
    # Aliases present in the bucket (Day 2 picker filters them out
    # for button rendering; Day 1 just exposes the data).
    assert "haiku" in grouped["anthropic"]
    assert "sonnet" in grouped["anthropic"]
    # Fallback-list full names also present.
    assert "claude-haiku-4-5" in grouped["anthropic"]


def test_claude_code_grouped_within_provider_lexicographic(no_auth):
    """Pin the within-provider order so the dashboard / picker can
    rely on it without re-sorting."""
    grouped = md.discover_claude_code_models_by_provider()
    bucket = grouped["anthropic"]
    assert bucket == sorted(bucket)


def test_claude_code_grouped_does_not_call_subprocess(no_auth):
    """Pin: discovery doesn't shell out. (Important because the
    validator calls this on every dashboard request.) The live
    path uses urllib.request, which is why this test only
    asserts no subprocess — not no network."""
    with patch("subprocess.run") as run_spy:
        md.discover_claude_code_models_by_provider()
    assert not run_spy.called


def test_opencode_grouped_parses_provider_prefix():
    """``provider/model_id`` lines split on first ``/``; provider
    becomes the bucket key, full id stays in the value list."""
    fake_stdout = (
        "anthropic/claude-haiku-3-5\n"
        "anthropic/claude-sonnet-4\n"
        "openai/gpt-4o\n"
    )
    with patch("subprocess.run", return_value=_fake_completed(fake_stdout)):
        grouped = md.discover_opencode_models_by_provider()
    assert grouped == {
        "anthropic": [
            "anthropic/claude-haiku-3-5",
            "anthropic/claude-sonnet-4",
        ],
        "openai": ["openai/gpt-4o"],
    }


def test_opencode_grouped_preserves_multi_slash_full_id():
    """Real-world: openrouter exposes ``openrouter/anthropic/claude-3.5-haiku``
    — two slashes. ``str.partition('/')`` correctly returns
    provider=``openrouter``, full id intact in the value list. The
    opencode CLI accepts the full id when spawning so byte-identical
    round-trip is required."""
    fake_stdout = (
        "openrouter/anthropic/claude-3.5-haiku\n"
        "openrouter/anthropic/claude-opus-4.7\n"
        "anthropic/claude-haiku-3-5\n"
    )
    with patch("subprocess.run", return_value=_fake_completed(fake_stdout)):
        grouped = md.discover_opencode_models_by_provider()
    assert grouped["openrouter"] == [
        "openrouter/anthropic/claude-3.5-haiku",
        "openrouter/anthropic/claude-opus-4.7",
    ]
    assert grouped["anthropic"] == ["anthropic/claude-haiku-3-5"]


def test_opencode_grouped_anthropic_first_then_alphabetical():
    """Provider order: anthropic first (vexis is anthropic-centric,
    default brain is claude-code), then alphabetical for the rest.
    Stable shape across cache hits + test runs."""
    fake_stdout = (
        "zenith/x\n"
        "openai/gpt-4o\n"
        "anthropic/claude-sonnet-4\n"
        "github-copilot/y\n"
    )
    with patch("subprocess.run", return_value=_fake_completed(fake_stdout)):
        grouped = md.discover_opencode_models_by_provider()
    assert list(grouped.keys()) == [
        "anthropic", "github-copilot", "openai", "zenith",
    ]


def test_opencode_grouped_no_slash_lands_in_other_bucket():
    """Defensive: a model id without a provider prefix lands in
    ``other`` rather than crashing. Real ``opencode models`` always
    provider-prefixes; this branch protects against future format
    drift."""
    fake_stdout = "bare-model-no-prefix\nanthropic/claude\n"
    with patch("subprocess.run", return_value=_fake_completed(fake_stdout)):
        grouped = md.discover_opencode_models_by_provider()
    assert "other" in grouped
    assert grouped["other"] == ["bare-model-no-prefix"]
    assert grouped["anthropic"] == ["anthropic/claude"]


def test_opencode_grouped_empty_returns_empty_dict():
    """No providers configured / binary missing → empty dict.
    Callers (validator, picker) treat empty as 'no grouping
    available — fall back' rather than crashing."""
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        grouped = md.discover_opencode_models_by_provider()
    assert grouped == {}


def test_opencode_grouped_uses_existing_flat_cache():
    """Pin the design choice: grouped helper parses from the
    cached flat-set output, NOT a second subprocess call. One
    cache layer, two views."""
    fake_stdout = "anthropic/x\nopenai/y\n"
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ) as run_spy:
        md.discover_opencode_models()      # populates cache
        md.discover_opencode_models_by_provider()  # parses from cache
        md.discover_opencode_models_by_provider()  # still cached
    assert run_spy.call_count == 1


def test_opencode_grouped_within_provider_lexicographic():
    """Pin within-provider sort so the picker / dashboard can rely
    on it without re-sorting."""
    fake_stdout = (
        "anthropic/zzz\n"
        "anthropic/aaa\n"
        "anthropic/mmm\n"
    )
    with patch("subprocess.run", return_value=_fake_completed(fake_stdout)):
        grouped = md.discover_opencode_models_by_provider()
    assert grouped["anthropic"] == [
        "anthropic/aaa", "anthropic/mmm", "anthropic/zzz",
    ]


# ──────────────────────────────────────────────────────────────────
# discovery_grouped_for_brain dispatch + discovery_grouped_for_validator
# ──────────────────────────────────────────────────────────────────


def test_discovery_grouped_for_brain_dispatches():
    cc = md.discovery_grouped_for_brain("claude-code")
    assert "anthropic" in cc
    with patch("subprocess.run", return_value=_fake_completed("openai/x\n")):
        oc = md.discovery_grouped_for_brain("opencode")
    assert oc == {"openai": ["openai/x"]}


def test_discovery_grouped_for_brain_unknown_returns_empty():
    """BrainNull and any future brain without discovery → empty
    dict so callers don't have to branch on brain kind."""
    assert md.discovery_grouped_for_brain("null") == {}
    assert md.discovery_grouped_for_brain("future-brain") == {}


# ──────────────────────────────────────────────────────────────────
# Family grouping (picker UX)
# ──────────────────────────────────────────────────────────────────


def test_family_id_strips_trailing_date_suffix():
    assert md.family_id_for("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert md.family_id_for("claude-opus-4-1-20250805") == "claude-opus-4-1"
    assert md.family_id_for("claude-opus-4-20250514") == "claude-opus-4"


def test_family_id_passes_through_unversioned():
    assert md.family_id_for("claude-opus-4-7") == "claude-opus-4-7"
    assert md.family_id_for("claude-sonnet-4-6") == "claude-sonnet-4-6"
    # Single-digit version suffix is NOT a date — must not collapse.
    assert md.family_id_for("claude-opus-4-1") == "claude-opus-4-1"
    assert md.family_id_for("claude-opus-4") == "claude-opus-4"


def test_family_id_passes_through_opencode_format():
    """opencode uses provider/model_id with dotted versions, not
    -YYYYMMDD. Family helper must be a no-op on those."""
    assert md.family_id_for("anthropic/claude-3.5-haiku") == "anthropic/claude-3.5-haiku"
    assert md.family_id_for("openai/gpt-4o") == "openai/gpt-4o"


def test_group_by_family_collapses_dated_variants():
    grouped = md.group_models_by_family([
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-7",
    ])
    assert grouped == {
        "claude-haiku-4-5": ["claude-haiku-4-5", "claude-haiku-4-5-20251001"],
        "claude-opus-4-7": ["claude-opus-4-7"],
    }


def test_group_by_family_dated_only_no_unversioned():
    """Edge case: a family with no unversioned id (e.g. older
    Anthropic generations only return dated variants from
    /v1/models). Group is just the dated variant(s)."""
    grouped = md.group_models_by_family([
        "claude-opus-4-5-20251101",
    ])
    assert grouped == {
        "claude-opus-4-5": ["claude-opus-4-5-20251101"],
    }


def test_group_by_family_sorts_dated_descending():
    """Within a family, dated variants come AFTER the unversioned
    id (if present) in descending date order. Most-recent-first
    means the picker's expanded view shows the freshest pin first."""
    grouped = md.group_models_by_family([
        "claude-foo-1-20240101",
        "claude-foo-1-20250101",
        "claude-foo-1",
        "claude-foo-1-20230101",
    ])
    assert grouped["claude-foo-1"] == [
        "claude-foo-1",            # unversioned first
        "claude-foo-1-20250101",   # then dated descending
        "claude-foo-1-20240101",
        "claude-foo-1-20230101",
    ]


def test_default_view_prefers_unversioned_when_present():
    """One entry per family, unversioned wins."""
    view = md.default_view_models([
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-7",
        "claude-sonnet-4-6",
    ])
    assert view == [
        "claude-haiku-4-5",  # unversioned wins over dated
        "claude-opus-4-7",
        "claude-sonnet-4-6",
    ]


def test_default_view_falls_back_to_most_recent_dated():
    """When a family has only dated variants (no unversioned id
    in the input set), the default view shows the most-recent
    dated variant. Pinned by the user's spec: 'If a family has
    only dated variants and no unversioned id (edge case), show
    the most-recent dated one in default view.'"""
    view = md.default_view_models([
        "claude-foo-1-20240101",
        "claude-foo-1-20250101",   # most recent
        "claude-foo-1-20230101",
    ])
    assert view == ["claude-foo-1-20250101"]


def test_default_view_empty_input_returns_empty():
    assert md.default_view_models([]) == []


def test_expanded_view_shows_everything_grouped():
    view = md.expanded_view_models([
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-7",
    ])
    # Family blocks alphabetical; within each: unversioned first.
    assert view == [
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-7",
    ]


def test_expanded_view_collapses_to_default_when_no_dated_variants():
    """opencode case: model ids have no -YYYYMMDD suffix at all.
    Default and expanded views are identical, which is what the
    picker uses to decide not to render the toggle button."""
    inputs = ["anthropic/claude-3.5-haiku", "openai/gpt-4o"]
    assert md.default_view_models(inputs) == md.expanded_view_models(inputs)


# ──────────────────────────────────────────────────────────────────
# Live discovery does NOT synthesize unversioned family ids
# ──────────────────────────────────────────────────────────────────


def test_live_discovery_does_not_synthesize_unversioned_family_ids(
    force_oauth_token,
):
    """Pin the no-synthesis policy: probe of Anthropic's
    /v1/messages (2026-05-07) showed that ``claude-opus-4`` and
    ``claude-sonnet-4`` return HTTP 404 not_found_error —
    Anthropic retires the unversioned alias when a family is
    superseded by sub-versions. Synthesizing those would produce
    invalid model ids in the picker. Discovery returns the API
    response verbatim + bare aliases; the picker's
    ``default_view_models`` falls back to most-recent dated for
    families without an unversioned id in the input."""
    payload = _api_payload(
        "claude-opus-4-7",            # already unversioned (kept)
        "claude-haiku-4-5-20251001",  # dated only — NOT synthesized
        "claude-opus-4-5-20251101",   # dated only — NOT synthesized
    )
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_http_response(payload),
    ):
        models = md.discover_claude_code_models()
    # Live ids present.
    assert "claude-opus-4-7" in models
    assert "claude-haiku-4-5-20251001" in models
    assert "claude-opus-4-5-20251101" in models
    # Aliases unioned.
    assert "haiku" in models
    assert "sonnet" in models
    assert "opus" in models
    # Synthesized family ids NOT present (would be invalid claude-code
    # targets — Anthropic doesn't maintain the unversioned alias).
    assert "claude-haiku-4-5" not in models
    assert "claude-opus-4-5" not in models
    # Total: 3 live + 3 aliases.
    assert len(models) == 6


def test_live_discovery_returns_api_response_verbatim_plus_aliases(
    force_oauth_token,
):
    """Sanity-check sibling: all-unversioned API response passes
    through untouched + aliases unioned. No surprises in either
    direction (no additions, no drops)."""
    payload = _api_payload("claude-opus-4-7", "claude-sonnet-4-6")
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_http_response(payload),
    ):
        models = md.discover_claude_code_models()
    expected = {"claude-opus-4-7", "claude-sonnet-4-6", "haiku", "sonnet", "opus"}
    assert models == expected


# ──────────────────────────────────────────────────────────────────
# Brain-configured detection (cross-brain picker, 2026-05-08)
# ──────────────────────────────────────────────────────────────────


def test_is_brain_configured_returns_true_when_provider_grouping_nonempty(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        md, "discovery_grouped_for_brain",
        lambda kind: (
            {"anthropic": ["claude-opus-4-7"]} if kind == "claude-code"
            else {}
        ),
    )
    assert md.is_brain_configured("claude-code") is True
    assert md.is_brain_configured("opencode") is False


def test_is_brain_configured_null_brain_always_true():
    """null is the test fake — pickers shouldn't render under null
    in production but the helper returns True trivially."""
    assert md.is_brain_configured("null") is True


def test_configured_brains_returns_subset(monkeypatch: pytest.MonkeyPatch):
    """Excludes ``null``; orders claude-code first, then opencode."""
    monkeypatch.setattr(
        md, "discovery_grouped_for_brain",
        lambda kind: (
            {"anthropic": ["x"]} if kind in ("claude-code", "opencode")
            else {}
        ),
    )
    assert md.configured_brains() == ["claude-code", "opencode"]


def test_configured_brains_only_one(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        md, "discovery_grouped_for_brain",
        lambda kind: {"anthropic": ["x"]} if kind == "claude-code" else {},
    )
    assert md.configured_brains() == ["claude-code"]


def test_model_belongs_to_brain_finds_owner(monkeypatch: pytest.MonkeyPatch):
    """Resolves a typed model id to the brain that has it in the
    discovered set. Used by the typed-arg slash refusal path."""
    monkeypatch.setattr(
        md, "discover_models",
        lambda kind: (
            {"claude-opus-4-7"} if kind == "claude-code"
            else {"anthropic/claude-haiku-3-5"} if kind == "opencode"
            else set()
        ),
    )
    assert md.model_belongs_to_brain("claude-opus-4-7") == "claude-code"
    assert (
        md.model_belongs_to_brain("anthropic/claude-haiku-3-5")
        == "opencode"
    )
    assert md.model_belongs_to_brain("totally-unknown") is None


# ──────────────────────────────────────────────────────────────────
# Per-model capability discovery (reasoning levels)
# ──────────────────────────────────────────────────────────────────


def _api_payload_with_capabilities(*entries: dict) -> dict:
    return {"data": list(entries), "has_more": False}


def test_claude_code_capabilities_extracts_supported_effort_levels(
    force_oauth_token,
):
    """``capabilities.effort.{level}.supported = true`` lands in
    the per-model reasoning_levels list. Models with
    ``effort.supported = false`` get an empty list.

    Note: this exercises the API-FALLBACK path (CLI probe stubbed
    to empty). The CLI-canonical path is tested separately in
    ``tests/test_voice_call_discovery.py`` — that's where the
    ``xhigh`` regression is pinned. Here we want to confirm that
    even when the CLI is unavailable, the API extraction still
    works correctly per-model."""
    payload = _api_payload_with_capabilities(
        {
            "id": "claude-opus-4-7",
            "capabilities": {
                "effort": {
                    "supported": True,
                    "low": {"supported": True},
                    "medium": {"supported": True},
                    "high": {"supported": True},
                    "max": {"supported": True},
                },
            },
        },
        {
            "id": "claude-haiku-4-5-20251001",
            "capabilities": {"effort": {"supported": False}},
        },
    )
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_http_response(payload),
    ), patch(
        # Stub the CLI probe so this test exercises the
        # API-fallback path (the test's original intent — predates
        # the CLI source of truth).
        "core.model_discovery._discover_claude_code_effort_levels_uncached",
        return_value=[],
    ):
        caps = md.discover_claude_code_capabilities()
    assert sorted(caps["claude-opus-4-7"]["reasoning_levels"]) == [
        "high", "low", "max", "medium",
    ]
    assert caps["claude-haiku-4-5-20251001"]["reasoning_levels"] == []


def test_claude_code_capabilities_handles_missing_capabilities_block(
    force_oauth_token,
):
    """Defensive: an entry without a capabilities block (shouldn't
    happen against the real API but defensive). Returns empty
    reasoning_levels for it."""
    payload = _api_payload_with_capabilities(
        {"id": "claude-foo", "display_name": "Foo"},  # no capabilities
    )
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_http_response(payload),
    ):
        caps = md.discover_claude_code_capabilities()
    assert caps["claude-foo"]["reasoning_levels"] == []


def test_claude_code_capabilities_returns_empty_on_failure(no_auth):
    """No auth → empty dict (not the hardcoded fallback). Picker
    treats empty as 'no capability data, skip the reasoning step'."""
    caps = md.discover_claude_code_capabilities()
    assert caps == {}


def test_reasoning_levels_for_unknown_model_returns_empty(force_oauth_token):
    """A model id not in the capability map → empty list. Picker
    skips the reasoning step rather than crashing."""
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_http_response(_api_payload("claude-opus-4-7")),
    ):
        levels = md.reasoning_levels_for("claude-code", "claude-mythical-9000")
    assert levels == []


def test_reasoning_levels_for_unknown_brain_returns_empty():
    """Future brains without discovery hooks → empty list."""
    assert md.reasoning_levels_for("future-brain", "anything") == []
    assert md.reasoning_levels_for("null", "anything") == []


def test_opencode_capabilities_parses_variants_keys():
    """Pin the opencode parser: ``variants`` keys become reasoning
    levels for each model. Variants whose payloads don't look
    reasoning-shaped (no ``thinking`` / ``reasoningEffort``) are
    skipped."""
    fake_stdout = """\
github-copilot/claude-opus-4.5
{
  "id": "claude-opus-4.5",
  "providerID": "github-copilot",
  "variants": {
    "max": {"thinking": {"type": "enabled", "budgetTokens": 31999}},
    "high": {"thinking": {"type": "enabled", "budgetTokens": 16000}}
  }
}
opencode/nemotron-3-super-free
{
  "id": "nemotron-3-super-free",
  "providerID": "opencode",
  "variants": {
    "low": {"reasoningEffort": "low"},
    "medium": {"reasoningEffort": "medium"},
    "high": {"reasoningEffort": "high"}
  }
}
anthropic/claude-no-variants
{
  "id": "claude-no-variants",
  "variants": {}
}
"""
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ):
        caps = md.discover_opencode_capabilities()
    assert caps["github-copilot/claude-opus-4.5"]["reasoning_levels"] == [
        "high", "max",
    ]
    assert caps["opencode/nemotron-3-super-free"]["reasoning_levels"] == [
        "high", "low", "medium",
    ]
    # Model with empty variants block: no reasoning levels.
    assert caps["anthropic/claude-no-variants"]["reasoning_levels"] == []


def test_opencode_capabilities_skips_malformed_blocks():
    """Defensive: a block whose JSON is malformed (mid-line truncation
    or whatever) gets silently skipped rather than crashing the whole
    discovery. Other models still parse."""
    fake_stdout = """\
provider/good-model
{
  "id": "good-model",
  "variants": {"high": {"thinking": {"budgetTokens": 1}}}
}
provider/bad-model
{
  "id": "bad-model"
  this isn't valid JSON
provider/another-good
{
  "id": "another-good",
  "variants": {"low": {"reasoningEffort": "low"}}
}
"""
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ):
        caps = md.discover_opencode_capabilities()
    # The good models parsed.
    assert "provider/good-model" in caps
    # The bad block didn't crash discovery.


def test_opencode_capabilities_missing_binary_returns_empty():
    """Binary missing → empty dict. Same posture as
    ``discover_opencode_models``."""
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        caps = md.discover_opencode_capabilities()
    assert caps == {}


def test_reasoning_levels_for_dispatches_per_brain(force_oauth_token):
    """Brain dispatch: claude-code → /v1/models capabilities;
    opencode → opencode parser. Pin the routing."""
    api_payload = _api_payload_with_capabilities({
        "id": "claude-opus-4-7",
        "capabilities": {
            "effort": {
                "supported": True,
                "low": {"supported": True},
                "high": {"supported": True},
            },
        },
    })
    oc_stdout = """\
github-copilot/claude-opus-4.5
{
  "id": "claude-opus-4.5",
  "variants": {"max": {"thinking": {"budgetTokens": 1}}}
}
"""
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_http_response(api_payload),
    ), patch(
        # Stub CLI probe to empty — exercise the API-fallback path.
        # The CLI-canonical path is tested elsewhere
        # (tests/test_voice_call_discovery.py).
        "core.model_discovery._discover_claude_code_effort_levels_uncached",
        return_value=[],
    ):
        cc_levels = md.reasoning_levels_for(
            "claude-code", "claude-opus-4-7",
        )
    assert cc_levels == ["low", "high"]
    with patch(
        "subprocess.run", return_value=_fake_completed(oc_stdout),
    ):
        oc_levels = md.reasoning_levels_for(
            "opencode", "github-copilot/claude-opus-4.5",
        )
    assert oc_levels == ["max"]


def test_discovery_grouped_for_validator_builds_dict():
    """Sibling of ``discovery_for_validator`` with provider grouping.
    Used by ``_models_payload`` to populate the dashboard's
    ``available_models_by_provider`` field in one call."""
    with patch("subprocess.run", return_value=_fake_completed("anthropic/c\n")):
        d = md.discovery_grouped_for_validator(["claude-code", "opencode", "null"])
    assert set(d.keys()) == {"claude-code", "opencode", "null"}
    assert "anthropic" in d["claude-code"]
    assert d["opencode"] == {"anthropic": ["anthropic/c"]}
    assert d["null"] == {}
