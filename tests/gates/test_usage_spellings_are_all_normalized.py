"""Every usage spelling a provider emits must be one `TokenUsage.from_raw` reads.

A key this function does not know produces a zero, and downstream a zero it invented is
indistinguishable from a zero the provider reported. That is not a wrong number you can
spot — it is a whole surface silently classified as uncacheable. It happened three times
in one evening: Gemini's `cached_content_token_count`, the Responses API's
`input_tokens_details.cached_tokens`, and its differently spelled write counter.

The catalog below is the contract. Each row is a real payload shape observed from that
surface, so a provider that changes its spelling fails here rather than reporting free
prompts forever.
"""

import pytest

from agentevolver.model.types import TokenUsage

# surface -> (raw payload as that surface returns it, what it means)
OBSERVED = {
    "anthropic-native": (
        {"input_tokens": 100, "output_tokens": 5,
         "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20},
        {"input_tokens": 100, "output_tokens": 5,
         "cache_read_tokens": 80, "cache_write_tokens": 20}),
    "chat-completions": (
        {"prompt_tokens": 100, "completion_tokens": 5,
         "prompt_tokens_details": {"cached_tokens": 80}},
        {"input_tokens": 100, "output_tokens": 5, "cache_read_tokens": 80}),
    "responses": (
        {"input_tokens": 57, "output_tokens": 29,
         "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 17}},
        {"input_tokens": 57, "output_tokens": 29,
         "cache_read_tokens": 40, "cache_write_tokens": 17}),
    "gemini": (
        # As the google provider normalizes it — the protobuf field is mapped there
        # because `from_raw` never sees a protobuf.
        {"prompt_token_count": 100, "candidates_token_count": 5,
         "cache_read_input_tokens": 85},
        {"input_tokens": 100, "output_tokens": 5, "cache_read_tokens": 85}),
    "openrouter-with-cost": (
        {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.0042},
        {"input_tokens": 10, "output_tokens": 2, "cost": 0.0042}),
}


@pytest.mark.parametrize("surface", sorted(OBSERVED))
def test_a_surface_is_read_exactly_as_it_reports(surface):
    raw, expected = OBSERVED[surface]
    usage = TokenUsage.from_raw(raw)
    assert usage is not None
    for field, value in expected.items():
        assert getattr(usage, field) == value, (
            f"{surface}: {field} read as {getattr(usage, field)}, not {value} — an "
            f"unknown spelling reads as zero and cannot be told from a real zero")


def test_nothing_reported_is_not_the_same_as_nothing_spent():
    """The distinction the aggregation depends on.

    A step whose usage the provider omitted must be skippable; folded in as zero it makes
    a partial total read as authoritative.
    """
    assert TokenUsage.from_raw(None) is None
    assert TokenUsage.from_raw({}) is None


def test_the_gemini_provider_still_maps_its_protobuf_field():
    """`from_raw` never sees Gemini's own name, so the mapping lives at the provider.

    Checked here too: this gate would otherwise pass on a payload the provider had
    stopped producing.
    """
    import agentevolver.model.google.chat as google_chat

    class _Usage:
        prompt_token_count, candidates_token_count = 100, 5
        total_token_count, cached_content_token_count = 105, 85

    serializer = next(v for v in vars(google_chat).values()
                      if hasattr(v, "_usage_dict"))
    normalized = serializer._usage_dict(_Usage())
    assert TokenUsage.from_raw(normalized).cache_read_tokens == 85, (
        "the provider dropped cached_content_token_count; every Gemini prompt would "
        "report as a full re-read")


def test_every_cache_field_is_reachable_from_some_surface():
    """A field no catalogued surface fills is either dead or untested — say which.

    `cost` is deliberately absent from most surfaces; the relays that report it are the
    only ones that can.
    """
    reached = set()
    for raw, _ in OBSERVED.values():
        usage = TokenUsage.from_raw(raw)
        for field in ("input_tokens", "output_tokens",
                      "cache_read_tokens", "cache_write_tokens"):
            if getattr(usage, field):
                reached.add(field)
    assert reached == {"input_tokens", "output_tokens",
                       "cache_read_tokens", "cache_write_tokens"}, \
        f"no catalogued surface exercises {sorted({'input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'} - reached)}"
