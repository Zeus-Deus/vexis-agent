from vexis_agent.core.brain.usage import build_usage_event


def test_usage_event_is_provider_neutral_and_uses_microusd():
    assert build_usage_event(
        input_tokens=100,
        cache_read_tokens=40,
        cache_write_tokens=3,
        output_tokens=20,
        reasoning_tokens=5,
        total_tokens=123,
        reported_cost_usd=0.0123456,
    ) == {
        "type": "usage",
        "input_tokens": 100,
        "cache_read_tokens": 40,
        "cache_write_tokens": 3,
        "output_tokens": 20,
        "reasoning_tokens": 5,
        "total_tokens": 123,
        "reported_cost_usd_micros": 12346,
    }


def test_usage_event_drops_empty_or_invalid_provider_payloads():
    assert build_usage_event() is None
    assert build_usage_event(
        input_tokens=-1,
        output_tokens=True,
        reported_cost_usd=-2,
    ) is None


def test_usage_event_derives_provider_appropriate_totals():
    assert build_usage_event(
        input_tokens=100,
        cache_read_tokens=40,
        output_tokens=20,
    )["total_tokens"] == 120
    assert build_usage_event(
        input_tokens=100,
        cache_read_tokens=40,
        cache_write_tokens=3,
        output_tokens=20,
        cache_tokens_are_additive=True,
    )["total_tokens"] == 163
