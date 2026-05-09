"""Per-brain model discovery.

Two surfaces:

  - ``discover_claude_code_models()`` — calls the live Anthropic
    /v1/models endpoint with the user's claude-code OAuth token
    (or ANTHROPIC_API_KEY env override) and returns the full
    set + aliases. Falls back to the hardcoded
    :data:`MODEL_DISCOVERY_CLAUDE_CODE` constant on any failure
    (no auth, network error, timeout, non-200, parse failure,
    expired OAuth token). The fallback is ALWAYS available so the
    picker never empties out — minor staleness beats an empty
    list. Validator rule 6 stays at warning severity for
    claude-code precisely because the live list is the source of
    truth and our fallback can lag.

  - ``discover_opencode_models()`` — runs ``opencode models``
    subprocess (timeout 10s), parses stdout, returns the set.
    Returns empty set on missing binary (claude-code-only users)
    or persistent timeout — gracefully degrades the validator's
    rule 6 to silent-skip rather than blocking.

Both are cached in-process for 5 minutes. The cache is shared
across the slash command's ``/model list <brain>`` and the
dashboard's available-models dropdown so the two surfaces stay
in sync. Cache invalidation via :func:`invalidate_discovery_cache`
(called by the dashboard's refresh button + by tests + by
:func:`refresh_claude_code_models` /
:func:`refresh_opencode_models`).

Design citation: ``.plans/model-management-ux-research.md`` §4
"Model discovery" + §6 Day 4. Live claude-code discovery
(2026-05-07) replaces the previous hardcoded-only behavior so
users no longer wait on a vexis PR to pick a newly-released
Anthropic model.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Family grouping (picker UX)
# ──────────────────────────────────────────────────────────────────


# Anthropic ships dated model variants like ``claude-haiku-4-5-20251001``
# alongside (or in lieu of) an unversioned ``claude-haiku-4-5``. The
# picker's "Show all versions" toggle uses these helpers to collapse
# dated variants into one row per family in the default view.
#
# Date suffix is exactly ``-YYYYMMDD$`` (8 trailing digits). Anchored
# to end-of-string so middle-of-name digit runs (e.g. a hypothetical
# ``claude-test-20250101-experimental``) don't trip detection.
# Anchored to ``-`` so single-digit version suffixes like
# ``claude-opus-4-1`` don't collapse into ``claude-opus-4``.
_DATED_MODEL_SUFFIX_RE = re.compile(r"-(\d{8})$")


def family_id_for(model_id: str) -> str:
    """Return the unversioned family id for a model.

    Strips a trailing ``-YYYYMMDD`` if present; otherwise returns the
    id unchanged. Used by the picker to group dated variants together
    and by :func:`_discover_claude_code_models_uncached` to synthesize
    family ids that the live API doesn't expose directly."""
    match = _DATED_MODEL_SUFFIX_RE.search(model_id)
    return model_id[: match.start()] if match else model_id


def group_models_by_family(
    models: Iterable[str],
) -> dict[str, list[str]]:
    """Group models by family id.

    Each family's value is sorted: the unversioned id first (if
    present in the input), then dated variants in descending date
    order so the most-recent dated variant is second. Empty input
    returns an empty dict."""
    families: dict[str, list[str]] = {}
    for m in models:
        families.setdefault(family_id_for(m), []).append(m)
    for family, ids in families.items():
        unversioned = [i for i in ids if i == family]
        dated = sorted(
            (i for i in ids if i != family), reverse=True,
        )
        families[family] = unversioned + dated
    return families


def default_view_models(models: Iterable[str]) -> list[str]:
    """One entry per family — the family's most-recent valid id.

    For families with an unversioned id present in the input, that's
    the default button (these are the latest-generation models like
    ``claude-opus-4-7`` that Anthropic exposes without a date suffix
    in /v1/models). For older families that only ship as dated
    variants (e.g. ``claude-haiku-4-5-20251001`` with no
    ``claude-haiku-4-5`` in the input), the most-recent dated variant
    surfaces instead. Output sorted by family id for stable button
    ordering.

    The "unversioned id from input" path is what Anthropic returns
    naturally for the latest-generation models; we DO NOT synthesize
    unversioned ids from dated variants because Anthropic retires the
    unversioned alias for superseded families (probed 2026-05-07:
    ``claude-opus-4`` and ``claude-sonnet-4`` both 404). See the
    explanatory comment in
    :func:`_discover_claude_code_models_uncached` for the full
    rationale."""
    families = group_models_by_family(models)
    return [ids[0] for _family, ids in sorted(families.items())]


def expanded_view_models(models: Iterable[str]) -> list[str]:
    """Every model id, grouped by family, family-block by family-block.

    Within each family: unversioned first, then dated variants in
    descending date order. Order across families: alphabetical by
    family id. Same source as :func:`default_view_models`; collapses
    to the same list when the input has no dated variants."""
    families = group_models_by_family(models)
    out: list[str] = []
    for _family, ids in sorted(families.items()):
        out.extend(ids)
    return out


# ──────────────────────────────────────────────────────────────────
# claude-code: live /v1/models discovery + hardcoded fallback
# ──────────────────────────────────────────────────────────────────


# Hardcoded fallback shipped with vexis. Used WHENEVER live
# discovery fails (no auth, network down, API timeout, parse
# failure, expired OAuth). Updated opportunistically; staleness
# is acceptable because it's only the safety net — the live API
# is the primary source. Keep aliases at the top so callers that
# never reach the live path still get the alias steering.
MODEL_DISCOVERY_CLAUDE_CODE: list[str] = [
    # Aliases — claude-code resolves these to the latest model
    # in that family. Always present in the fallback regardless
    # of what the live API returns.
    "haiku",
    "sonnet",
    "opus",
    # Full names — last-known set. Drift between Anthropic
    # releases and vexis PRs is bounded by the live discovery
    # path; these only matter when /v1/models is unreachable.
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-1",
    "claude-haiku-3-5",
    "claude-sonnet-3-7",
    "claude-opus-4",
]

# Aliases ALWAYS get unioned with live results — the API returns
# full ids only, but claude-code's CLI accepts the bare aliases
# (``--model haiku``) and the slash typed-arg path needs them in
# the validated set so it doesn't trip rule 6. The picker filters
# aliases at render time; this stays at the discovery layer.
_CLAUDE_CODE_ALIASES = frozenset({"haiku", "sonnet", "opus"})


# OAuth token path (claude-code stores subscription credentials
# here). The file is JSON: ``{"claudeAiOauth": {"accessToken":
# "...", "expiresAt": ..., ...}}``. We read accessToken; we
# don't refresh — that's claude-code's responsibility (run any
# ``claude`` command and it refreshes if needed).
_CLAUDE_OAUTH_PATH = Path.home() / ".claude" / ".credentials.json"

_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models?limit=1000"
_ANTHROPIC_VERSION = "2023-06-01"
_CLAUDE_CODE_DISCOVERY_TIMEOUT_SECONDS = 10.0


def _read_claude_oauth_token() -> str | None:
    """Read the OAuth bearer from ~/.claude/.credentials.json.

    Returns None for any failure mode — file missing (claude-code
    not installed / never authed), JSON parse error, missing
    field. The caller treats None as "no OAuth available, try the
    next auth path".

    NOTE: we don't check ``expiresAt`` here because (a) the API
    will 401 cleanly if the token is stale, and (b) checking it
    would race with claude-code's background refresh — the token
    on disk could be valid even if our timestamp comparison says
    otherwise. Let the API be the authority on token validity."""
    try:
        with _CLAUDE_OAUTH_PATH.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    return token if isinstance(token, str) and token else None


def _build_anthropic_request_headers() -> dict[str, str] | None:
    """Build the headers for /v1/models. Auth precedence:

      1. ``ANTHROPIC_API_KEY`` env var — uses x-api-key header.
         Power-user opt-in; takes precedence so users can probe
         a different account than their claude-code OAuth.
      2. claude-code OAuth bearer at ``~/.claude/.credentials.json``.

    Returns None when neither source is available — caller falls
    back to the hardcoded list."""
    headers: dict[str, str] = {"anthropic-version": _ANTHROPIC_VERSION}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key
        return headers
    token = _read_claude_oauth_token()
    if token is None:
        return None
    headers["Authorization"] = f"Bearer {token}"
    return headers


def _discover_claude_code_models_uncached() -> set[str]:
    """One-shot live discovery + fallback. Caller handles caching.

    On ANY failure → log warning + return the hardcoded fallback
    set (with aliases). This guarantees the picker / validator
    never see an empty claude-code discovery and the user can
    keep working through transient network blips. Verbose
    failure mode in the warning so a daemon log post-mortem can
    distinguish auth-missing from network-down from parse-error."""
    headers = _build_anthropic_request_headers()
    if headers is None:
        log.warning(
            "claude-code live discovery: no auth available "
            "(ANTHROPIC_API_KEY unset and no OAuth at %s); "
            "falling back to hardcoded list. Run `claude` once "
            "to authenticate, or set ANTHROPIC_API_KEY.",
            _CLAUDE_OAUTH_PATH,
        )
        return _claude_code_fallback()

    req = urllib.request.Request(_ANTHROPIC_MODELS_URL, headers=headers)
    try:
        with urllib.request.urlopen(
            req, timeout=_CLAUDE_CODE_DISCOVERY_TIMEOUT_SECONDS,
        ) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # 401 = expired OAuth or wrong env key; 403 = scope; 5xx
        # = upstream. All fall back; log surfaces the code so a
        # 401 specifically can hint at re-auth.
        log.warning(
            "claude-code live discovery: HTTP %d from /v1/models; "
            "falling back to hardcoded list.%s",
            exc.code,
            (
                " (Token may be expired — run `claude` to refresh.)"
                if exc.code == 401 else ""
            ),
        )
        return _claude_code_fallback()
    except urllib.error.URLError as exc:
        log.warning(
            "claude-code live discovery: network error (%s); "
            "falling back to hardcoded list.", exc.reason,
        )
        return _claude_code_fallback()
    except (TimeoutError, OSError) as exc:
        log.warning(
            "claude-code live discovery: timeout / OS error (%s); "
            "falling back to hardcoded list.", exc,
        )
        return _claude_code_fallback()

    try:
        payload = json.loads(body)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ValueError("response missing 'data' list")
        ids = {
            entry["id"] for entry in data
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        }
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        log.warning(
            "claude-code live discovery: response parse failed (%s); "
            "falling back to hardcoded list.", exc,
        )
        return _claude_code_fallback()

    if not ids:
        log.warning(
            "claude-code live discovery: API returned 0 models; "
            "falling back to hardcoded list.",
        )
        return _claude_code_fallback()

    # NOTE: do NOT synthesize unversioned family ids from dated
    # variants. /v1/models returns the dated variant only for
    # older Anthropic generations (e.g. ``claude-haiku-4-5-20251001``
    # but NOT ``claude-haiku-4-5``); we initially synthesized the
    # unversioned form to give users an auto-tracking-latest
    # default in the picker. Probe (2026-05-07 against the live
    # API) showed that synthesis produces invalid ids:
    #   - ``claude-opus-4`` → HTTP 404 not_found_error
    #   - ``claude-sonnet-4`` → HTTP 404 not_found_error
    # Anthropic appears to retire the unversioned alias for
    # superseded families (4-1, 4-5, 4-6, 4-7 superseded the
    # unversioned 4 alias). Synthesizing those ids would surface
    # invalid model names in the picker that the validator and
    # spawn would then reject. Better to surface the most-recent
    # DATED variant as the family representative — pinned but
    # always valid. The picker's ``default_view_models`` already
    # handles the no-unversioned case by picking most-recent
    # dated, so the policy lives there. The trade-off (no
    # auto-tracking-latest for older families) is acceptable;
    # users who want auto-tracking can use the bare aliases
    # (``haiku``/``sonnet``/``opus``) via the typed-arg path.

    # Live ids + always-present aliases. Aliases never appear in
    # /v1/models (it returns concrete ids only) but the typed-arg
    # path on the slash + the validator's rule 6 need them in the
    # discovered set or `claude --model sonnet` would warn-flag.
    log.info(
        "claude-code live discovery: %d model id(s) from /v1/models",
        len(ids),
    )
    return ids | set(_CLAUDE_CODE_ALIASES)


def _claude_code_fallback() -> set[str]:
    """The hardcoded fallback set (aliases + last-known full names).
    Extracted so tests can import it directly + the live path has
    one obvious return shape."""
    return set(MODEL_DISCOVERY_CLAUDE_CODE)


def discover_claude_code_models() -> set[str]:
    """Return the discovered claude-code model identifiers.

    Hits the live /v1/models endpoint (5-min in-process cache);
    falls back to the hardcoded list on any failure. Always
    includes the bare aliases ``haiku``/``sonnet``/``opus``
    regardless of which path returned. The cache is shared with
    other discovery surfaces and is bustable via
    :func:`refresh_claude_code_models` (the
    in-process helper /model refresh and the dashboard's
    refresh button both call)."""
    return _cached("claude-code", _discover_claude_code_models_uncached)


def refresh_claude_code_models() -> set[str]:
    """Bust the claude-code discovery cache and re-fetch live.
    Called by ``/model refresh`` on claude-code AND by the
    dashboard's refresh button on a claude-code brain.

    Returns the freshly-discovered set (which may be the
    hardcoded fallback if the live fetch fails — same posture as
    the cached path)."""
    invalidate_discovery_cache("claude-code")
    invalidate_discovery_cache("claude-code-capabilities")
    return discover_claude_code_models()


# ──────────────────────────────────────────────────────────────────
# Per-model capability discovery (reasoning levels)
# ──────────────────────────────────────────────────────────────────


# /v1/models response carries a ``capabilities.effort`` block per
# model: ``{supported: bool, low: {supported: bool}, medium: {...},
# high: {...}, max: {...}}``. Some models (haiku) have
# ``effort.supported: false`` with no per-level keys; others
# (opus-4-7) have all four levels supported. Probe (2026-05-07)
# confirmed the levels match the claude CLI's ``--effort`` flag
# values. We extract levels DYNAMICALLY by iterating effort.keys()
# rather than against a hardcoded tuple — if Anthropic ever adds a
# new level (xhigh, ultra, etc.) it'll surface in the picker
# without a code change.

# Meta-keys we skip when iterating ``effort.keys()`` — these aren't
# levels, they're presence flags / metadata.
_EFFORT_META_KEYS = frozenset({"supported"})


def _extract_claude_code_reasoning_levels(
    model_entry: dict,
    cli_levels: list[str] | None = None,
) -> list[str]:
    """Resolve the reasoning-level list for one model.

    Two-source strategy:

      1. Per-model gate from the API: ``capabilities.effort.supported``
         tells us *whether* this model accepts reasoning at all.
         haiku-style models have ``supported: false`` → empty list →
         picker hides the reasoning sub-step.

      2. Level vocabulary from ``cli_levels`` when provided. The
         claude CLI is the canonical source for valid level names —
         ``/v1/models`` is incomplete (e.g. ships low/medium/high/max
         but not xhigh, even though ``claude --effort xhigh`` works).
         Caller should pass the cached
         :func:`discover_claude_code_effort_levels` result here.
         When ``cli_levels`` is None or empty (CLI probe failed),
         we fall back to the API-listed levels so the picker
         degrades gracefully rather than going empty.

    No hardcoded level list in this function — both sources are
    discovered live.
    """
    if not isinstance(model_entry, dict):
        return []
    caps = model_entry.get("capabilities")
    if not isinstance(caps, dict):
        return []
    effort = caps.get("effort")
    if not isinstance(effort, dict) or not effort.get("supported"):
        return []

    # CLI vocabulary is the source of truth when available.
    if cli_levels:
        return list(cli_levels)

    # Fallback: walk the API's effort.* keys dynamically.
    out: list[str] = []
    for level, sub in effort.items():
        if level in _EFFORT_META_KEYS:
            continue
        if isinstance(sub, dict) and sub.get("supported"):
            out.append(level)
    return out


# Pattern for parsing the ``--effort`` line in ``claude --help``.
# The CLI prints this on a single (possibly wrapped) line:
#
#   --effort <level>   Effort level for the current session (low, medium, high, xhigh, max)
#
# We anchor on ``--effort <level>`` to avoid catching unrelated
# parens elsewhere in the help text. ``re.S`` keeps the match
# robust to line wrapping that separates the flag from the
# parenthesised list.
_CLAUDE_CODE_EFFORT_HELP_RE = re.compile(
    r"--effort\s+<level>.*?\(([^)]+)\)", re.S,
)


def _parse_claude_code_effort_help(help_text: str) -> list[str]:
    """Extract the canonical ``--effort`` level list from
    ``claude --help`` output. Pure parser — no I/O — so tests can
    drive synthetic help text without spawning the binary.

    Returns an empty list when the schema doesn't match. Caller
    treats empty as "couldn't discover via CLI; fall through to
    whatever the API listed".
    """
    m = _CLAUDE_CODE_EFFORT_HELP_RE.search(help_text)
    if not m:
        return []
    raw = m.group(1)
    levels = [v.strip() for v in raw.split(",")]
    return [v for v in levels if v]


def _discover_claude_code_effort_levels_uncached() -> list[str]:
    """Source-of-truth probe for valid ``--effort`` levels.

    The CLI accept-set is canonical because it's what we actually
    spawn — pass a level the CLI rejects and the subprocess errors.
    Anthropic's ``/v1/models`` endpoint is *incomplete* relative to
    the CLI: at the time of writing it advertises low/medium/high/max
    but the CLI also accepts ``xhigh`` (verified directly:
    ``claude --effort xhigh -p "hi"`` returns successfully on
    reasoning-capable models). Trusting only the API would silently
    drop xhigh from the picker — exactly the regression the user
    flagged.

    We probe the CLI's help once per cache window (5 min). On any
    failure (binary not on PATH, output schema changed, parse
    miss) return an empty list — callers fall back to the API's
    per-model level list. No hardcoded fallback in this layer:
    the absence of a CLI probe does not mean a fixed level set
    is correct.
    """
    try:
        proc = subprocess.run(
            ["claude", "--help"],
            capture_output=True,
            text=True,
            timeout=_CLAUDE_CODE_DISCOVERY_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    return _parse_claude_code_effort_help(proc.stdout)


def discover_claude_code_effort_levels() -> list[str]:
    """Cached canonical ``--effort`` level list. Empty when the
    CLI couldn't be probed; callers should fall back to API-listed
    per-model levels in that case."""
    return _cached(
        "claude-code-effort",
        _discover_claude_code_effort_levels_uncached,
    )


def _extract_claude_code_context_info(model_entry: dict) -> dict[str, object]:
    """Pull context window + max output + display name from a
    /v1/models entry. All fields are optional in the API response;
    missing values become None so the picker can show '—' rather
    than crash. Source: live ``/v1/models`` response fields
    ``max_input_tokens``, ``max_tokens``, ``display_name``.
    """
    if not isinstance(model_entry, dict):
        return {
            "display_name": None, "max_input_tokens": None, "max_tokens": None,
        }

    def _int_or_none(value: object) -> int | None:
        return value if isinstance(value, int) and value > 0 else None

    def _str_or_none(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    return {
        "display_name": _str_or_none(model_entry.get("display_name")),
        "max_input_tokens": _int_or_none(model_entry.get("max_input_tokens")),
        "max_tokens": _int_or_none(model_entry.get("max_tokens")),
    }


def _discover_claude_code_capabilities_uncached() -> dict[str, dict]:
    """One-shot capability fetch. Same auth + fallback posture as
    :func:`_discover_claude_code_models_uncached` — if any failure
    fires, return an empty dict (callers treat empty as "no
    capability data; skip the reasoning step")."""
    headers = _build_anthropic_request_headers()
    if headers is None:
        return {}
    req = urllib.request.Request(_ANTHROPIC_MODELS_URL, headers=headers)
    try:
        with urllib.request.urlopen(
            req, timeout=_CLAUDE_CODE_DISCOVERY_TIMEOUT_SECONDS,
        ) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, OSError):
        return {}
    try:
        payload = json.loads(body)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return {}
    except json.JSONDecodeError:
        return {}
    # Fetch the CLI-canonical effort levels once per discovery
    # cycle. The same list applies to every reasoning-capable
    # model — claude-code's CLI doesn't differentiate by model.
    # An empty list here means CLI probe failed; the per-model
    # extraction falls back to API-listed levels.
    cli_levels = discover_claude_code_effort_levels()

    out: dict[str, dict] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str):
            continue
        info = _extract_claude_code_context_info(entry)
        out[model_id] = {
            "reasoning_levels": _extract_claude_code_reasoning_levels(
                entry, cli_levels=cli_levels,
            ),
            "display_name": info["display_name"],
            "max_input_tokens": info["max_input_tokens"],
            "max_tokens": info["max_tokens"],
        }
    return out


def discover_claude_code_capabilities() -> dict[str, dict]:
    """Per-model capability map for claude-code, keyed by model id.

    Each value is ``{reasoning_levels: list[str]}``. Cached for 5
    minutes via :func:`_cached`. Returns an empty dict on any
    discovery failure — callers (picker, validator) treat the
    empty case as "no capability data, skip reasoning step".

    Aliases (haiku/sonnet/opus) inherit empty capability lists
    because /v1/models doesn't return them; the picker filters
    aliases out before consulting capabilities anyway."""
    return _cached(
        "claude-code-capabilities",
        _discover_claude_code_capabilities_uncached,
    )


# Opencode capability discovery — parse ``opencode models --verbose``
# output. The verbose format is multi-block: each block is a
# ``provider/model_id`` header line followed by a pretty-printed
# JSON object on subsequent lines. We parse via a state machine:
# read header, then read until balanced braces, parse the JSON
# block. ``variants`` keys are the supported reasoning levels;
# ``capabilities.reasoning: false`` (or absent) means the model
# doesn't support reasoning at all. CLI flag is ``--variant
# <name>``.


def _parse_opencode_verbose(text: str) -> dict[str, dict]:
    """Extract per-model capability data from ``opencode models
    --verbose`` output.

    Returns ``{provider/model_id: {reasoning_levels: [...]}}``
    keyed by full id (matches what
    :func:`discover_opencode_models` returns).

    Robust to malformed blocks — silently skips any block that
    doesn't parse rather than crashing the whole discovery."""
    out: dict[str, dict] = {}
    lines = text.splitlines()
    header_re = re.compile(r"^[a-z0-9._/-]+$")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # Heuristic: a header line is a single token of
        # provider/model id chars, followed immediately by '{'.
        if (
            line
            and header_re.match(line)
            and i + 1 < n
            and lines[i + 1].lstrip().startswith("{")
        ):
            full_id = line.strip()
            # Capture the JSON block via brace counting (handles
            # nested objects in the metadata).
            depth = 0
            j = i + 1
            block_lines: list[str] = []
            while j < n:
                cur = lines[j]
                block_lines.append(cur)
                depth += cur.count("{") - cur.count("}")
                j += 1
                if depth == 0:
                    break
            try:
                meta = json.loads("\n".join(block_lines))
            except json.JSONDecodeError:
                i = j
                continue
            variants = meta.get("variants")
            reasoning_levels: list[str] = []
            if isinstance(variants, dict) and variants:
                # Only keep variants that look reasoning-shaped.
                # Each variant's value should be a dict with
                # ``thinking`` or ``reasoningEffort``. Skip variants
                # whose values are non-dict (defensive).
                for name, payload in variants.items():
                    if not isinstance(payload, dict):
                        continue
                    if "thinking" in payload or "reasoningEffort" in payload:
                        reasoning_levels.append(name)

            # opencode reports context + max output under ``limit``,
            # display name as ``name``. Same shape we extract from
            # claude-code's /v1/models — keeps the wire format
            # consistent across brains.
            display_name: str | None = None
            max_input_tokens: int | None = None
            max_tokens: int | None = None
            name_raw = meta.get("name")
            if isinstance(name_raw, str) and name_raw.strip():
                display_name = name_raw.strip()
            limit = meta.get("limit")
            if isinstance(limit, dict):
                ctx = limit.get("context")
                if isinstance(ctx, int) and ctx > 0:
                    max_input_tokens = ctx
                out_lim = limit.get("output")
                if isinstance(out_lim, int) and out_lim > 0:
                    max_tokens = out_lim

            out[full_id] = {
                "reasoning_levels": sorted(reasoning_levels),
                "display_name": display_name,
                "max_input_tokens": max_input_tokens,
                "max_tokens": max_tokens,
            }
            i = j
            continue
        i += 1
    return out


def _discover_opencode_capabilities_uncached() -> dict[str, dict]:
    """One-shot ``opencode models --verbose`` fetch. Same failure
    posture as :func:`_discover_opencode_models_uncached`; empty
    dict on any error so the picker skips the reasoning step."""
    try:
        proc = subprocess.run(
            ["opencode", "models", "--verbose"],
            capture_output=True,
            text=True,
            timeout=_OPENCODE_DISCOVERY_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {}
    if proc.returncode != 0:
        return {}
    return _parse_opencode_verbose(proc.stdout)


def discover_opencode_capabilities() -> dict[str, dict]:
    """Per-model capability map for opencode, keyed by full
    ``provider/model_id``. Same shape and posture as
    :func:`discover_claude_code_capabilities`."""
    return _cached(
        "opencode-capabilities",
        _discover_opencode_capabilities_uncached,
    )


# ──────────────────────────────────────────────────────────────────
# Brain-configured detection (cross-brain picker, 2026-05-08)
# ──────────────────────────────────────────────────────────────────


def is_brain_configured(brain_kind: str) -> bool:
    """Return True iff the brain has at least one provider with
    discovered models — the proxy for "binary installed AND auth
    present". Reuses :func:`discovery_grouped_for_brain` so the
    cache is shared and discovery never fires twice for the same
    check.

    Used by the picker to decide whether to offer cross-brain
    switching (only when the OTHER brain has at least one
    discoverable model). The non-active brain's discovery hits
    its respective subprocess/API; failure → empty grouping →
    treated as "not configured", which is the desired UX (don't
    offer a brain you can't switch to).

    ``null`` brain returns True trivially (it never has discovery
    but it's the test fake — pickers shouldn't be invoked under
    null in production)."""
    if brain_kind == "null":
        return True
    grouped = discovery_grouped_for_brain(brain_kind)
    return any(grouped.values())


def configured_brains() -> list[str]:
    """List of currently-configured brain kinds (subset of
    ``["claude-code", "opencode"]``, excluding ``null``).

    Used by the picker's provider step to decide single-brain vs.
    multi-brain provider keyboard rendering. Empty list when
    neither brain has discovery (rare — usually means a fresh
    install before authentication completes)."""
    return [
        k for k in ("claude-code", "opencode")
        if is_brain_configured(k)
    ]


def model_belongs_to_brain(model_id: str) -> str | None:
    """Return the brain kind that has ``model_id`` in its
    discovered set, or ``None`` when no configured brain has it.

    Used by the typed-arg slash path to detect the case where a
    user types a model id that belongs to a brain they don't have
    configured — refusal copy then points at install/auth
    instructions for the missing brain instead of letting the
    write proceed and the spawn fail.

    When multiple brains have the same id (rare; could happen if
    a future brain reuses Anthropic ids verbatim), claude-code
    wins by precedence — first-match in the iteration order."""
    for kind in ("claude-code", "opencode"):
        models = discover_models(kind)
        if model_id in models:
            return kind
    return None


def reasoning_levels_for(brain_kind: str, model_id: str) -> list[str]:
    """Return the reasoning levels supported by ``model_id`` on
    ``brain_kind``. Empty list = no reasoning support OR
    capability data unavailable; in either case the picker skips
    the reasoning step for that model.

    Brain dispatch matches :func:`discover_models`. Unknown brains
    return empty (no reasoning step)."""
    if brain_kind == "claude-code":
        caps = discover_claude_code_capabilities()
    elif brain_kind == "opencode":
        caps = discover_opencode_capabilities()
    else:
        return []
    entry = caps.get(model_id)
    if not isinstance(entry, dict):
        return []
    levels = entry.get("reasoning_levels")
    return list(levels) if isinstance(levels, list) else []


# ──────────────────────────────────────────────────────────────────
# opencode: live `opencode models` subprocess
# ──────────────────────────────────────────────────────────────────


_OPENCODE_DISCOVERY_TIMEOUT_SECONDS = 10.0


def _discover_opencode_models_uncached() -> set[str]:
    """One-shot subprocess call. Caller handles caching.

    Failure modes (each → empty set, advisory-only impact on
    rule 6 in the validator):

      - ``FileNotFoundError`` (``opencode`` binary missing — the
        common case for claude-code-only users).
      - ``subprocess.TimeoutExpired`` (binary present but slow;
        models.dev cache fetch under contention).
      - Non-zero exit (auth issue or models.dev unreachable).
      - Empty stdout (no providers configured).
    """
    try:
        proc = subprocess.run(
            ["opencode", "models"],
            capture_output=True,
            text=True,
            timeout=_OPENCODE_DISCOVERY_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return set()
    except subprocess.TimeoutExpired:
        log.warning(
            "opencode models discovery timed out after %.1fs",
            _OPENCODE_DISCOVERY_TIMEOUT_SECONDS,
        )
        return set()
    except OSError as exc:
        log.warning("opencode models discovery OS error: %s", exc)
        return set()

    if proc.returncode != 0:
        log.warning(
            "opencode models discovery exited %d: %s",
            proc.returncode, (proc.stderr or "").strip(),
        )
        return set()

    return {
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip()
    }


# ──────────────────────────────────────────────────────────────────
# 5-minute per-brain cache
# ──────────────────────────────────────────────────────────────────


_CACHE_TTL_SECONDS = 5 * 60.0
_DISCOVERY_CACHE: dict[str, tuple[float, set[str]]] = {}


def discover_opencode_models() -> set[str]:
    """Cached opencode model list. Refreshes every 5 minutes per
    process; clearable via :func:`invalidate_discovery_cache`."""
    return _cached("opencode", _discover_opencode_models_uncached)


def discover_models(brain_kind: str) -> set[str]:
    """Brain-agnostic dispatch. Returns the empty set for unknown
    brain kinds (e.g. ``null``) rather than raising — the
    validator's rule 6 silently skips when the discovered set is
    empty."""
    if brain_kind == "claude-code":
        return discover_claude_code_models()
    if brain_kind == "opencode":
        return discover_opencode_models()
    return set()


def discovery_for_validator(brain_kinds: Iterable[str]) -> dict[str, set[str]]:
    """Build the ``available_models_per_brain`` dict the validator
    expects. Helper so callers don't have to remember the shape."""
    return {b: discover_models(b) for b in brain_kinds}


# ──────────────────────────────────────────────────────────────────
# Provider-grouped discovery — Day 1 of model picker UX
# ──────────────────────────────────────────────────────────────────


# Providers shown first in picker UIs. Vexis is anthropic-centric
# (claude-code is the default brain), so anthropic leads. Remaining
# providers fall through to alphabetical so the surface is stable
# without needing a hardcoded full-priority list — we don't want to
# encode opinions about openai vs github-copilot ordering here when
# the user's own provider config decides what's actually available.
_PROVIDER_PRIORITY: tuple[str, ...] = ("anthropic",)

# Bucket name for opencode models that lack a provider prefix
# (defensive — current ``opencode models`` output always
# provider-prefixes, but a future format change shouldn't crash the
# picker. Surfaces under "other" so the user can still see the model
# even if grouping is incomplete).
_FALLBACK_PROVIDER_BUCKET = "other"


def _sort_providers(providers: Iterable[str]) -> list[str]:
    """Apply ``_PROVIDER_PRIORITY`` first, then alphabetical for the
    rest. Stable shape across cache hits + test runs."""
    seen = set(providers)
    out: list[str] = []
    for p in _PROVIDER_PRIORITY:
        if p in seen:
            out.append(p)
            seen.discard(p)
    out.extend(sorted(seen))
    return out


def discover_claude_code_models_by_provider() -> dict[str, list[str]]:
    """Return the discovered claude-code models grouped under the
    sole provider ``anthropic``. claude-code only routes to
    Anthropic models, so the grouping is implicit — the API shape
    matches opencode's so the dashboard / picker can render either
    brain through the same provider-grouped widget.

    Sources from :func:`discover_claude_code_models` so the live
    /v1/models path drives both surfaces (flat + grouped) — they
    share the 5-min cache and the same fallback-on-failure
    posture.

    Within-provider order is lexicographic (predictable API shape).
    The Day 2 picker filters aliases (``haiku``/``sonnet``/``opus``)
    out for its button rendering per the alias-omission decision in
    ``.plans/model-picker-ux-research.md`` §5; the typed-arg path
    keeps aliases."""
    return {"anthropic": sorted(discover_claude_code_models())}


def discover_opencode_models_by_provider() -> dict[str, list[str]]:
    """Group the cached flat set from
    :func:`discover_opencode_models` by provider prefix.

    Parse via ``str.partition("/")`` so multi-slash ids keep the
    full identifier intact in the value list — e.g.
    ``openrouter/anthropic/claude-3.5-haiku`` lands under
    ``openrouter`` with the full string preserved. The opencode
    CLI accepts the full id when spawning, so we MUST round-trip
    it byte-identical.

    Edge cases:
      - Model id with no ``/``: lands in ``"other"`` bucket. Real
        ``opencode models`` always provider-prefixes; this branch
        exists so a future format drift surfaces a visible bucket
        rather than crashing the dashboard.
      - Duplicate full id in the input set: impossible via the
        set-semantics of the underlying cache, but if observed
        anyway (e.g. monkey-patched in a test) the second
        occurrence is dropped silently — sets dedupe at the
        source. Logged at WARNING if the de-dup was non-trivial
        (i.e. the input contained the same full id twice via
        non-set construction).

    Within-provider order is lexicographic. Provider order is
    ``_sort_providers`` (anthropic first, then alphabetical) so the
    picker's first button row is consistent across users."""
    flat = discover_opencode_models()
    grouped: dict[str, list[str]] = {}
    for full_id in flat:
        prefix, sep, _ = full_id.partition("/")
        provider = prefix if sep else _FALLBACK_PROVIDER_BUCKET
        grouped.setdefault(provider, []).append(full_id)
    # Sort within each provider, then re-emit dict in
    # provider-priority order so iteration order matches the
    # picker's button order.
    return {
        provider: sorted(grouped[provider])
        for provider in _sort_providers(grouped.keys())
    }


def discovery_grouped_for_brain(brain_kind: str) -> dict[str, list[str]]:
    """Brain-agnostic dispatch matching :func:`discover_models`.

    Returns ``{}`` for brains without discovery (``null``, unknown
    future kinds) so callers can iterate over the result without
    branching on brain kind. Validator + dashboard / picker both
    treat empty dicts as "no provider grouping available — fall
    back to flat list" rather than crashing."""
    if brain_kind == "claude-code":
        return discover_claude_code_models_by_provider()
    if brain_kind == "opencode":
        return discover_opencode_models_by_provider()
    return {}


def discovery_grouped_for_validator(
    brain_kinds: Iterable[str],
) -> dict[str, dict[str, list[str]]]:
    """Provider-grouped sibling of :func:`discovery_for_validator`.
    Built for ``_models_payload`` in ``core/web_server.py`` so the
    dashboard's Day 2 ``<optgroup>`` dropdown gets pre-grouped data
    in one round-trip per brain rather than parsing on the
    frontend."""
    return {b: discovery_grouped_for_brain(b) for b in brain_kinds}


def invalidate_discovery_cache(brain_kind: str | None = None) -> None:
    """Clear the cache. ``brain_kind=None`` clears all entries; a
    string clears just that brain's. Called by the dashboard's
    POST /api/v1/models/discovery/refresh + by tests."""
    if brain_kind is None:
        _DISCOVERY_CACHE.clear()
    else:
        _DISCOVERY_CACHE.pop(brain_kind, None)


def refresh_opencode_models() -> set[str]:
    """Force-refresh opencode models AND run
    ``opencode models --refresh`` to refresh opencode's own
    models.dev cache. Returns the fresh list. Used by the
    dashboard's refresh button so a user adding a provider
    sees the new models without restarting vexis."""
    invalidate_discovery_cache("opencode")
    try:
        subprocess.run(
            ["opencode", "models", "--refresh"],
            capture_output=True,
            timeout=_OPENCODE_DISCOVERY_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        # Non-fatal — the next discovery call still tries the
        # cached models.dev list (or whatever's reachable).
        log.warning(
            "opencode models --refresh failed (%s); proceeding with "
            "cached discovery", exc,
        )
    return discover_opencode_models()


# ──────────────────────────────────────────────────────────────────
# Internal cache machinery
# ──────────────────────────────────────────────────────────────────


def _cached(key: str, fetcher) -> set[str]:
    now = time.monotonic()
    entry = _DISCOVERY_CACHE.get(key)
    if entry is not None:
        cached_at, value = entry
        if now - cached_at < _CACHE_TTL_SECONDS:
            return value
    value = fetcher()
    _DISCOVERY_CACHE[key] = (now, value)
    return value


__all__ = [
    "MODEL_DISCOVERY_CLAUDE_CODE",
    "configured_brains",
    "default_view_models",
    "discover_claude_code_capabilities",
    "discover_claude_code_models",
    "discover_claude_code_models_by_provider",
    "discover_models",
    "discover_opencode_capabilities",
    "discover_opencode_models",
    "discover_opencode_models_by_provider",
    "discovery_for_validator",
    "discovery_grouped_for_brain",
    "discovery_grouped_for_validator",
    "expanded_view_models",
    "family_id_for",
    "group_models_by_family",
    "invalidate_discovery_cache",
    "is_brain_configured",
    "model_belongs_to_brain",
    "reasoning_levels_for",
    "refresh_claude_code_models",
    "refresh_opencode_models",
]
