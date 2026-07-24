"""Provider-neutral foreground-turn usage events.

Brain adapters translate their native CLI payloads into this compact shape.
Downstream consumers can therefore compare Claude Code, Codex, OpenCode, and
future brains without knowing provider schemas or model names.
"""

from __future__ import annotations

from typing import Any


def _token(value: Any) -> int:
    """Return a bounded non-negative integer for a provider token field."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return 0


def build_usage_event(
    *,
    input_tokens: Any = 0,
    cache_read_tokens: Any = 0,
    cache_write_tokens: Any = 0,
    output_tokens: Any = 0,
    reasoning_tokens: Any = 0,
    total_tokens: Any = None,
    reported_cost_usd: Any = None,
    cache_tokens_are_additive: bool = False,
) -> dict | None:
    """Build one normalized ``usage`` event, or ``None`` when empty.

    ``reported_cost_usd_micros`` is explicitly provider-reported cost, not a
    Vexis estimate. Subscription-backed CLIs may report zero even when tokens
    were consumed; consumers must not relabel it as an invoice amount.
    """
    input_count = _token(input_tokens)
    cache_read_count = _token(cache_read_tokens)
    cache_write_count = _token(cache_write_tokens)
    output_count = _token(output_tokens)
    reasoning_count = _token(reasoning_tokens)
    total_count = _token(total_tokens)
    if total_tokens is None:
        # OpenAI-style providers include cached tokens in ``input_tokens``;
        # Anthropic reports cache reads/writes as separate input buckets.
        # Adapters opt into the latter without making downstream consumers
        # guess based on a model/provider name.
        total_count = input_count + output_count
        if cache_tokens_are_additive:
            total_count += cache_read_count + cache_write_count
    cost_micros = 0
    if (
        not isinstance(reported_cost_usd, bool)
        and isinstance(reported_cost_usd, (int, float))
        and reported_cost_usd >= 0
    ):
        cost_micros = round(float(reported_cost_usd) * 1_000_000)

    if not any((
        input_count,
        cache_read_count,
        cache_write_count,
        output_count,
        reasoning_count,
        total_count,
        cost_micros,
    )):
        return None

    return {
        "type": "usage",
        "input_tokens": input_count,
        "cache_read_tokens": cache_read_count,
        "cache_write_tokens": cache_write_count,
        "output_tokens": output_count,
        "reasoning_tokens": reasoning_count,
        "total_tokens": total_count,
        "reported_cost_usd_micros": cost_micros,
    }
