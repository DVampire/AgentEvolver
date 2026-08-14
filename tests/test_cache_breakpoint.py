"""The catalogs must fall inside the cached prefix, not after it.

Only the system message carried a `cache_control` breakpoint, and the capability
catalogs live in the user turn — so in a measured run 58,223 characters, 63% of the
whole prompt, sat past the last cacheable byte and were re-read in full on every step.
The counts to prove it were not recorded either: `_think` kept `output_tokens` and
dropped the rest, so nothing downstream could tell a cache hit from a full re-read.

Two things have to hold together for the breakpoint to be worth anything, and each is
useless alone: the catalogs must be *ahead of* the volatile agent state in the turn
(a cache keeps a prefix, so anything changing per step invalidates all that follows),
and the breakpoint must sit after them.
"""

import pytest

from agentevolver.message.types import HumanMessage
from agentevolver.model.llm_hub.serializer import LLMHubChatSerializer
from agentevolver.model.openrouter.serializer import OpenRouterChatSerializer

SERIALIZERS = [LLMHubChatSerializer, OpenRouterChatSerializer]

TURN = ("<capability-context><tool-context>- bash: run</tool-context></capability-context>"
        "<agent-context><step-info>step 7 of 40</step-info></agent-context>")


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_the_breakpoint_lands_after_the_catalog(serializer):
    blocks = serializer.serialize_message(HumanMessage(content=TURN))["content"]

    assert blocks[0]["text"].endswith("</capability-context>")
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
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
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


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
