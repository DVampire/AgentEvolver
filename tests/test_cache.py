"""What makes a prompt cacheable, and what proves it was cached.

The breakpoint decides which bytes a cache can keep; the usage counters are the only
evidence it kept them. Separating them is how the original defect survived: the catalogs
were 99% reusable and billed in full, because the sole breakpoint sat on the system
message while they lived in the user turn — a fact no number in the logs could express,
since `_think` kept `output_tokens` and dropped the rest.

Both halves are checked here so neither can drift without the other noticing. A spelling
`from_raw` does not know produces a zero indistinguishable from a real one, which writes
off a whole surface as uncacheable with no number ever changing.
"""

import pytest

from agentevolver.message.types import HumanMessage
from agentevolver.model.llm_hub.serializer import LLMHubChatSerializer
from agentevolver.model.openrouter.serializer import OpenRouterChatSerializer
from agentevolver.model.types import TokenUsage


# ---------------------------------------------------------------------------
# from test_cache_breakpoint.py
# ---------------------------------------------------------------------------
SERIALIZERS = [LLMHubChatSerializer, OpenRouterChatSerializer]

TURN = ("<capability-context><tool-context>- bash: run</tool-context></capability-context>"
        "<agent-context><step-info>step 7 of 40</step-info></agent-context>")


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_the_breakpoint_lands_after_the_catalog(serializer):
    blocks = serializer.serialize_message(HumanMessage(content=TURN))["content"]

    assert blocks[0]["text"].endswith("</capability-context>")
    assert blocks[0]["cache_control"]["type"] == "ephemeral"
    assert "step 7 of 40" in blocks[1]["text"]
    assert "cache_control" not in blocks[1], "the volatile half must not be a breakpoint"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_a_turn_without_a_catalog_gets_no_breakpoint(serializer):
    """Every other turn in the conversation.

    A breakpoint after content that changes each step caches nothing and spends a cache
    *write* to discover it — strictly worse than not asking.
    """
    out = serializer.serialize_message(HumanMessage(content="the file did not exist"))
    assert out["content"] == "the file did not exist"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_a_catalog_with_nothing_after_it_is_one_block(serializer):
    """The derived path sends the catalog as its own message; an empty tail adds nothing."""
    only = "<capability-context><tool-context>- bash</tool-context></capability-context>\n"
    blocks = serializer.serialize_message(HumanMessage(content=only))["content"]
    assert len(blocks) == 1
    assert blocks[0]["cache_control"]["type"] == "ephemeral"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_the_split_is_lossless(serializer):
    """Whatever the split does, the model must receive the same bytes it would have."""
    blocks = serializer.serialize_message(HumanMessage(content=TURN))["content"]
    assert "".join(b["text"] for b in blocks) == TURN


def test_templates_put_the_catalog_before_the_agent_state():
    """The ordering the breakpoint depends on, checked against the templates themselves.

    The catalogs used to be rendered *after* `<agent-context>`. A breakpoint after them
    would still have cached nothing, because everything before it included the step
    counter — which changes every step by definition.
    """
    import pathlib

    from agentevolver.prompt.types import parse_prompt_file

    misordered = []
    for path in sorted(pathlib.Path("agentevolver/prompt/default").glob("*.html")):
        template = parse_prompt_file(str(path)).user_template or ""
        if "<capability-context>" not in template or "<agent-context>" not in template:
            continue
        if template.index("<capability-context>") > template.index("<agent-context>"):
            misordered.append(path.name)

    assert not misordered, f"capabilities must precede agent state: {misordered}"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_the_cached_prefix_outlives_a_sub_agent(serializer):
    """Five minutes is shorter than the gap between an orchestrator's own steps.

    It delegates, the sub-agent runs for minutes, and by the orchestrator's next step the
    entry has expired. Measured on `penguins_analysis`: meta_agent wrote 308,469 input
    tokens across three steps and read back zero, while agents whose steps are seconds
    apart hit 36-49% in the same run — the pattern of a TTL, not of an unstable prefix.

    An hour costs 2x base on the write against 1.25x, and reads are 0.1x either way, so
    one otherwise-missed hit already pays for it. The case this fixes misses every hit.
    """
    blocks = serializer.serialize_message(HumanMessage(content=TURN))["content"]
    assert blocks[0]["cache_control"].get("ttl") == "1h"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_the_system_prompt_is_cached_on_the_same_terms(serializer):
    """It is fixed for the whole session — the one part guaranteed worth keeping."""
    from agentevolver.message.types import SystemMessage

    block = serializer.serialize_message(SystemMessage(content="rules"))["content"][0]
    assert block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

# ---------------------------------------------------------------------------
# from test_usage_spellings_are_all_normalized.py
# ---------------------------------------------------------------------------
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

    Checked here too: this would otherwise pass on a payload the provider had
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
