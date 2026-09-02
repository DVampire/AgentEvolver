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

import asyncio

import pytest

from agentevolver.message.types import (
    AssistantMessage,
    ContentPartText,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from agentevolver.model.llm_hub.serializer import LLMHubChatSerializer
from agentevolver.model.openrouter.serializer import OpenRouterChatSerializer
from agentevolver.model.types import TokenUsage

# ---------------------------------------------------------------------------
# from test_cache_breakpoint.py
# ---------------------------------------------------------------------------
SERIALIZERS = [LLMHubChatSerializer, OpenRouterChatSerializer]

TURN = (
    "<capability-context><tool-context>- bash: run</tool-context></capability-context>"
    "<agent-context><step-info>step 7 of 40</step-info></agent-context>"
)


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_the_breakpoint_lands_after_the_catalog(serializer):
    # TURN's first live-zone block is <step-info>, so the breakpoint lands there and the
    # catalog (and the <agent-context> opener) ride in the cached prefix.
    blocks = serializer.serialize_message(HumanMessage(content=TURN))["content"]

    assert "</capability-context>" in blocks[0]["text"], "the catalog must be cached"
    assert "<step-info>" not in blocks[0]["text"], "the live step-info must not be cached"
    assert blocks[0]["cache_control"]["type"] == "ephemeral"
    assert "step 7 of 40" in blocks[1]["text"]
    assert "cache_control" not in blocks[1], "the volatile half must not be a breakpoint"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_the_breakpoint_includes_the_task_and_plan_before_the_first_live_block(serializer):
    """The stable prefix is catalog + task + plan; the breakpoint goes before <constraints>.

    Measured, the user turn was byte-identical for ~54% of its length but the old breakpoint
    at </capability-context> sat at ~14%, so the task (byte-stable every step) was re-read in
    full. Splitting before the first live block — <constraints> — caches it and the plan.
    """
    turn = (
        "<capability-context><tool-context>- bash</tool-context></capability-context>"
        "<agent-context>"
        "<task>reconstruct the program</task>"
        "<plan>build it</plan>"
        "<constraints>step 7 of 40</constraints><recent-steps>...</recent-steps>"
        "</agent-context>"
    )
    blocks = serializer.serialize_message(HumanMessage(content=turn))["content"]
    assert "<task>reconstruct the program</task>" in blocks[0]["text"], "task must be cached"
    assert "<plan>build it</plan>" in blocks[0]["text"], "plan must be cached"
    assert blocks[0]["cache_control"]["type"] == "ephemeral"
    assert blocks[1]["text"].startswith("<constraints>")
    assert "cache_control" not in blocks[1], "the live budget onward must not be cached"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_the_cached_prefix_becomes_its_own_message(serializer):
    """The relay caches per message, not per block: a stable block sharing a user message with
    volatile content caches nothing (measured 0 vs ~6.9k cached on a controlled probe). So the
    serialized turn must be TWO user messages — the cached stable prefix alone, then the rest —
    not one message of two blocks."""
    turn = (
        "<capability-context><tool-context>- bash</tool-context></capability-context>"
        "<agent-context><task>reconstruct</task><constraints>step 7 of 40</constraints>"
        "<recent-steps>m</recent-steps></agent-context>"
    )
    out = serializer.serialize_messages([HumanMessage(content=turn)])
    assert len(out) == 2, "the stable prefix and the volatile state must be separate messages"
    assert out[0]["role"] == "user" and out[1]["role"] == "user"
    # message 0 is the cached stable prefix (catalog + task), and it is a single cached block
    assert len(out[0]["content"]) == 1
    assert out[0]["content"][0]["cache_control"]["type"] == "ephemeral"
    assert "<task>reconstruct</task>" in out[0]["content"][0]["text"]
    # message 1 is the live state, uncached
    assert "cache_control" not in out[1]["content"][0]
    assert out[1]["content"][0]["text"].startswith("<constraints>")


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_a_plain_or_single_block_user_message_stays_one_message(serializer):
    # No split marker → one message; a catalog with nothing after it → one cached block, one message.
    plain = serializer.serialize_messages([HumanMessage(content="the file did not exist")])
    assert len(plain) == 1
    only = "<capability-context><tool-context>- bash</tool-context></capability-context>\n"
    one = serializer.serialize_messages([HumanMessage(content=only)])
    assert len(one) == 1 and one[0]["content"][0]["cache_control"]["type"] == "ephemeral"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_an_explicitly_cached_anchor_is_cached_in_full(serializer):
    anchor = HumanMessage(
        content="<capability-context>tools</capability-context><session-anchor>task</session-anchor>",
        cache=True,
    )
    out = serializer.serialize_messages([anchor])
    assert len(out) == 1
    assert "<session-anchor>task</session-anchor>" in out[0]["content"][0]["text"]
    assert out[0]["content"][0]["cache_control"]["type"] == "ephemeral"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_a_session_anchor_caches_stable_task_before_checkpoint(serializer):
    anchor = HumanMessage(content="<task>fix it</task>", cache=True)
    checkpoint = HumanMessage(content=("<memory-checkpoint>latest summary</memory-checkpoint>"))
    out = serializer.serialize_messages([anchor, checkpoint])

    assert len(out) == 2
    assert "<task>fix it</task>" in out[0]["content"][0]["text"]
    assert "latest summary" in str(out[1]["content"])
    assert out[0]["content"][0]["cache_control"]["type"] == "ephemeral"
    assert "<memory-checkpoint>" in out[1]["content"]
    assert "cache_control" not in out[1]


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_an_assistant_history_turn_gets_a_rolling_breakpoint_when_marked(serializer):
    """`derive_context` marks the last frozen turn with `.cache`; the serializer caches it.

    The rolling boundary lives on the assistant. This is accepted even when its visible
    content is empty and tool results follow it.
    """
    asst = serializer.serialize_message(
        AssistantMessage(content="I will rebuild the flag loop", cache=True)
    )
    assert isinstance(asst["content"], list)
    assert asst["content"][0]["cache_control"]["type"] == "ephemeral"


def test_llm_hub_does_not_put_an_unverified_breakpoint_on_tool_role():
    tool = LLMHubChatSerializer.serialize_message(
        ToolMessage(tool_call_id="c1", content="the reference printed X", cache=True)
    )

    assert tool["content"] == "the reference printed X"


def test_llm_hub_can_cache_an_empty_assistant_tool_call_turn():
    asst = LLMHubChatSerializer.serialize_message(AssistantMessage(content="", cache=True))

    assert asst["content"][0]["text"] == ""
    assert asst["content"][0]["cache_control"]["type"] == "ephemeral"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_an_unmarked_history_turn_stays_a_plain_string(serializer):
    # Only the LAST frozen turn is marked; the rest must not each become a breakpoint.
    tool = serializer.serialize_message(ToolMessage(tool_call_id="c1", content="output"))
    assert tool["content"] == "output"
    asst = serializer.serialize_message(AssistantMessage(content="thinking"))
    assert asst["content"] == "thinking"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_a_turn_without_a_catalog_gets_no_breakpoint(serializer):
    """Every other turn in the conversation.

    A breakpoint after content that changes each step caches nothing and spends a cache
    *write* to discover it — strictly worse than not asking.
    """
    out = serializer.serialize_message(HumanMessage(content="the file did not exist"))
    assert out["content"] == "the file did not exist"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_an_explicit_live_layer_never_infers_an_extra_breakpoint(serializer):
    """ContextBuilder has already split this suffix from the fixed task anchor."""
    live = HumanMessage(content=TURN, context_layer="live")
    serialized = serializer.serialize_message(live)

    assert "cache_control" not in str(serialized)


def test_native_anthropic_live_layer_never_infers_an_extra_breakpoint():
    from agentevolver.model.anthropic.serializer import AnthropicChatSerializer

    serialized = AnthropicChatSerializer.serialize(HumanMessage(content=TURN, context_layer="live"))

    assert "cache_control" not in str(serialized)


def test_claude_relay_request_stays_within_four_cache_breakpoints():
    """Pin the exact layout that produced `found 5` during the SWE Pro smoke run."""
    from agentevolver.agent.context.capabilities import _SchemaTool
    from agentevolver.model.llm_hub.chat import ChatLLMHub

    tool = _SchemaTool(
        name="read_file_tool",
        description="read",
        function_calling={
            "type": "function",
            "function": {
                "name": "read_file_tool",
                "description": "read",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    )
    messages = [
        SystemMessage(content="stable system", context_layer="fixed"),
        HumanMessage(content="stable task", cache=True, context_layer="fixed"),
        AssistantMessage(content="read files", cache=True, context_layer="recent"),
        ToolMessage(tool_call_id="c1", content="result", context_layer="recent"),
        HumanMessage(content=TURN, context_layer="live"),
    ]
    built = asyncio.run(
        ChatLLMHub(
            model="claude-opus-5",
            reasoning={},
        )._build_params(messages, tools=[tool], stream=True)
    )

    def count(value):
        if isinstance(value, dict):
            return int("cache_control" in value) + sum(count(v) for v in value.values())
        if isinstance(value, list):
            return sum(count(v) for v in value)
        return 0

    assert count(built) == 4


def test_non_claude_relay_never_receives_anthropic_cache_extensions():
    """DeepSeek/GPT routes must not be probed with another provider's vocabulary."""
    from agentevolver.model.llm_hub.chat import ChatLLMHub

    messages = [
        SystemMessage(content="stable system", context_layer="fixed"),
        HumanMessage(content="stable task", cache=True, context_layer="fixed"),
        HumanMessage(content="live", context_layer="live"),
    ]
    built = asyncio.run(
        ChatLLMHub(
            model="deepseek-v4-flash",
            reasoning={},
        )._build_params(messages, stream=True)
    )

    assert "cache_control" not in str(built)


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
        {
            "input_tokens": 100,
            "output_tokens": 5,
            "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 20,
        },
        {
            "input_tokens": 100,
            "context_input_tokens": 200,
            "output_tokens": 5,
            "cache_read_tokens": 80,
            "cache_write_tokens": 20,
        },
    ),
    "chat-completions": (
        {
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 80},
        },
        {
            "input_tokens": 20,
            "context_input_tokens": 100,
            "output_tokens": 5,
            "cache_read_tokens": 80,
        },
    ),
    "responses": (
        {
            "input_tokens": 57,
            "output_tokens": 29,
            "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 17},
        },
        {
            "input_tokens": 0,
            "context_input_tokens": 57,
            "output_tokens": 29,
            "cache_read_tokens": 40,
            "cache_write_tokens": 17,
        },
    ),
    "gemini": (
        # As the google provider normalizes it — the protobuf field is mapped there
        # because `from_raw` never sees a protobuf.
        {"prompt_token_count": 100, "candidates_token_count": 5, "cache_read_input_tokens": 85},
        {
            "input_tokens": 15,
            "context_input_tokens": 100,
            "output_tokens": 5,
            "cache_read_tokens": 85,
        },
    ),
    "openrouter-with-cost": (
        {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.0042},
        {"input_tokens": 10, "output_tokens": 2, "cost": 0.0042},
    ),
}


@pytest.mark.parametrize("surface", sorted(OBSERVED))
def test_a_surface_is_read_exactly_as_it_reports(surface):
    raw, expected = OBSERVED[surface]
    usage = TokenUsage.from_raw(raw)
    assert usage is not None
    for field, value in expected.items():
        assert getattr(usage, field) == value, (
            f"{surface}: {field} read as {getattr(usage, field)}, not {value} — an "
            f"unknown spelling reads as zero and cannot be told from a real zero"
        )


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

    serializer = next(v for v in vars(google_chat).values() if hasattr(v, "_usage_dict"))
    normalized = serializer._usage_dict(_Usage())
    assert TokenUsage.from_raw(normalized).cache_read_tokens == 85, (
        "the provider dropped cached_content_token_count; every Gemini prompt would "
        "report as a full re-read"
    )


def test_every_cache_field_is_reachable_from_some_surface():
    """A field no catalogued surface fills is either dead or untested — say which.

    `cost` is deliberately absent from most surfaces; the relays that report it are the
    only ones that can.
    """
    reached = set()
    for raw, _ in OBSERVED.values():
        usage = TokenUsage.from_raw(raw)
        for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            if getattr(usage, field):
                reached.add(field)
    assert reached == {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    }, (
        f"no catalogued surface exercises {sorted({'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'} - reached)}"
    )


# --------------------------------------------------------------------------- #
# One definition, not three
# --------------------------------------------------------------------------- #
def test_the_ttl_is_defined_in_exactly_one_place():
    """It was copied into three serializers, and nothing compared them.

    Three copies of a constant are three chances to change two of them. The drift would
    show up as one provider quietly losing its cache while the others keep theirs — no
    error, no failing test, just a bill that stops going down. A grep is the whole check
    because the failure is textual: the moment someone writes the literal again, this
    goes red and points at the single source.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "agentevolver" / "model"
    definitions = [
        p
        for p in root.rglob("*.py")
        if re.search(r"^CACHE_TTL\s*=", p.read_text(encoding="utf-8"), re.M)
    ]

    assert [p.name for p in definitions] == ["types.py"], (
        f"CACHE_TTL is defined in {[str(p.relative_to(root)) for p in definitions]}; it "
        f"belongs in model/types.py alone, and every serializer imports it from there"
    )


def test_every_serializer_that_sets_a_breakpoint_uses_that_one_value():
    """Importing the constant is not enough — a literal could sit beside the import."""
    from agentevolver.model.types import CACHE_TTL

    for serializer in SERIALIZERS:
        blocks = serializer.serialize_message(HumanMessage(content=TURN))["content"]
        assert blocks[0]["cache_control"]["ttl"] == CACHE_TTL, (
            f"{serializer.__name__} sends a TTL that is not the shared constant"
        )


# ---------------------------------------------------------------------------
# The frequency tiers: what caches and what does not.
#
# agent_context.html sorts blocks by how often they change. The CACHED zone holds only
# the reliably-stable blocks — catalog, task, inherited-context, plan — ordered
# most-stable first, and the breakpoint sits just before <constraints>, the first live
# block. Everything from <constraints> on is the LIVE zone, re-sent uncached every step:
# step-info, WORKING-MEMORY (it churns whenever history is compacted, so it is not
# cached), recent-steps, workspace, errors. The agent also freezes the catalog and
# appends a <capability-context-changes> delta to the very end when evolution changes a
# capability — kept in the live zone so evolving a capability never invalidates the
# task/plan cache.
# ---------------------------------------------------------------------------
_EVOLVED_TURN = (
    "<capability-context><tool-context>- bash: run</tool-context></capability-context>"
    "<agent-context>"
    "<task>reconstruct the program</task>"
    "<plan>1. inspect 2. build</plan>"
    "<constraints>step 7 of 40</constraints>"
    "<step-info>step 7</step-info>"
    "<working-memory>## Working Memory\n- decided to hardcode the magic bytes</working-memory>"
    "<recent-steps>## Recent Steps\n- ran compile.sh</recent-steps>"
    "<workspace>./compile.sh</workspace>"
    "</agent-context>"
    "\n\n<capability-context-changes>\n  <skill-context>\n    now available: reverse_elf\n"
    "  </skill-context>\n</capability-context-changes>"
)


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_stable_blocks_cached_and_live_zone_stays_volatile(serializer):
    out = serializer.serialize_messages([HumanMessage(content=_EVOLVED_TURN)])
    assert len(out) == 2, "stable prefix and volatile state must be separate messages"
    cached = out[0]["content"][0]["text"]
    volatile = "".join(part["text"] for part in out[1]["content"])

    # only the reliably-stable blocks ride in the cached prefix — not working-memory
    for tag in ("<capability-context>", "<task>", "<plan>"):
        assert tag in cached, f"{tag} must be in the cached prefix"
    assert "<working-memory>" not in cached, (
        "working-memory churns on compaction — it must stay in the live zone, not the cache"
    )
    assert out[0]["content"][0]["cache_control"]["type"] == "ephemeral"

    # the whole live zone — working-memory, recent-steps, and the evolution delta — is outside it
    for tag in (
        "<constraints>",
        "<working-memory>",
        "<recent-steps>",
        "<capability-context-changes>",
    ):
        assert tag in volatile, f"{tag} must be in the live zone"
    assert "<capability-context-changes>" not in cached, (
        "the evolution delta must stay on the volatile side so evolving a capability "
        "does not invalidate the task/plan cache"
    )
    assert "cache_control" not in out[1], "the volatile half must not be a breakpoint"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_a_tag_name_in_a_comment_does_not_move_the_breakpoint(serializer):
    """The breakpoint is found by parsing tags, so a live-block tag NAME written inside an
    HTML comment (or in prose) is ignored — only the real opening tag counts.

    This is the regression: the old string-search split cut at the FIRST '<constraints>'
    substring, which the layout comment inside <agent-context> contains, so it cached the
    catalog alone and left task + plan uncached on every step.
    """
    turn = (
        "<capability-context><tool-context>- bash</tool-context></capability-context>"
        "<agent-context>"
        "<!-- layout note: blocks before <constraints> are cached; <working-memory> is live -->"
        "<task>reconstruct</task>"
        "<plan>build it</plan>"
        "<constraints>step 7 of 40</constraints>"
        "<recent-steps>ran x</recent-steps>"
        "</agent-context>"
    )
    blocks = serializer.serialize_message(HumanMessage(content=turn))["content"]
    cached = blocks[0]["text"]
    # the real breakpoint is the <constraints> block, not its mention in the comment:
    # task and plan are cached, and the live budget value is not
    assert "<task>reconstruct</task>" in cached and "<plan>build it</plan>" in cached
    assert "step 7 of 40" not in cached, "the live budget must be past the breakpoint"
    assert "step 7 of 40" in blocks[1]["text"]


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_a_user_turn_wrapped_as_a_content_part_still_caches(serializer):
    """The prompt renderer hands the user turn as a single ContentPartText in a LIST, not a
    bare str. The split has to see through that wrapping.

    This is the regression that shipped: the split only fired for `str` content, so in
    production — where the turn is always `[ContentPartText(...)]` — the whole user turn
    (catalog + task + plan) serialized uncached and only the system message cached. Every
    other cache test used bare strings and so never caught it.
    """
    turn = (
        "<capability-context><tool-context>- bash</tool-context></capability-context>"
        "<agent-context><task>reconstruct</task><plan>build it</plan>"
        "<constraints>step 7 of 40</constraints><recent-steps>m</recent-steps></agent-context>"
    )
    wrapped = HumanMessage(content=[ContentPartText(text=turn)])
    out = serializer.serialize_messages([wrapped])
    assert len(out) == 2, "the list-wrapped turn must still split into cached prefix + live rest"
    cached = out[0]["content"][0]
    assert cached["cache_control"]["type"] == "ephemeral"
    assert (
        "<task>reconstruct</task>" in cached["text"] and "<plan>build it</plan>" in cached["text"]
    )
    assert "step 7 of 40" not in cached["text"], "the live budget must be past the breakpoint"
    assert "cache_control" not in out[1], "the volatile half must not be a breakpoint"


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_a_turn_with_a_non_text_part_is_left_unsplit(serializer):
    """A list carrying an image (or any non-text part) cannot become one cached text block,
    so it passes through the ordinary multi-part path rather than being force-split."""
    from agentevolver.message.types import ContentPartImage, ImageURL

    parts = [
        ContentPartText(text="look at this"),
        ContentPartImage(image_url=ImageURL(url="data:image/png;base64,AAAA")),
    ]
    out = serializer.serialize_messages([HumanMessage(content=parts)])
    assert len(out) == 1, "a turn with an image is one message, not a forced split"
