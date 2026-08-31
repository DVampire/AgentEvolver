"""The capability layout, stated in five places that must not drift apart.

Templates, a stylesheet, a renderer, a splitter, and the documents future agents are
generated from. Merging four blocks into `<capability-context>` meant editing all of them
by hand; the renderer was missed, which flattened the catalog into a wall of text — a
functional break, and nothing went red.

Expectations are derived from the templates rather than restated, so a template adopting a
new leaf drags the stylesheet, the renderer and the splitter into the failure with it.
"""

import asyncio
import re
from pathlib import Path

import pytest

from agentevolver.prompt.types import parse_prompt_file

# ---------------------------------------------------------------------------
# from test_prompt_structure_agrees_across_files.py
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
#: `module/` is where the layout actually lives — `<capability-context>` and its leaves
#: are written once there and pulled into each agent by `<module src>`. It was missing
#: from this list, so `_leaves_used_in_templates` (which reads raw text, not inlined)
#: found its leaves only in `extension/prompt/gaia_answer_agent.html`: a generated,
#: fully-expanded prompt that happened to be committed. Clearing the extension tree
#: emptied the guard, and it said so — which is the one thing that made this visible.
TEMPLATES = (
    sorted((ROOT / "agentevolver" / "prompt" / "default").glob("*.html"))
    + sorted((ROOT / "agentevolver" / "prompt" / "module").glob("*.html"))
    + sorted((ROOT / "extension" / "prompt").glob("*.html"))
)


# Read per test, not at import. Collection happens before session fixtures, so a
# module-level read captures whatever is on disk at that instant — including a mutation
# the repair fixture is about to undo. That exact race cost a commit: a killed run left
# the stylesheet mutated, collection cached it, the repair restored the file, and the
# check still failed against its stale in-memory copy.
def _css() -> str:
    return (ROOT / "agentevolver" / "visual" / "css" / "prompt.css").read_text(encoding="utf-8")


def _js() -> str:
    return (ROOT / "agentevolver" / "visual" / "js" / "prompt.js").read_text(encoding="utf-8")


def _splitter() -> str:
    return (ROOT / "agentevolver" / "agent" / "types.py").read_text(encoding="utf-8")


CONTAINER = "capability-context"


def _inline(text: str, base: Path) -> str:
    """Resolve `<module src>` includes.

    Most templates pull their state block in as `<module src="agent_context.html">`
    rather than writing `<agent-context>`, so a check reading the raw file skips them.
    The ordering check below did exactly that for 28 of 29 templates — it looked like it
    was guarding the layout and was guarding one file.
    """

    def sub(match):
        path = (base / match.group(1)).resolve()
        return path.read_text(encoding="utf-8") if path.exists() else ""

    return re.sub(r'<module src="([^"]+)"></module>', sub, text)


def _leaves_used_in_templates() -> set:
    """Every leaf tag any template nests inside the container. The source of truth."""
    leaves = set()
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        for block in re.finditer(rf"<{CONTAINER}>(.*?)</{CONTAINER}>", text, re.S):
            leaves.update(re.findall(r"<([a-z]+-context)>", block.group(1)))
    return leaves


LEAVES = _leaves_used_in_templates()


def _render_meta(*, evolution_enabled: bool) -> str:
    cfg = parse_prompt_file(str(ROOT / "agentevolver" / "prompt" / "default" / "meta_agent.html"))
    prompt = cfg.to_prompt()
    return asyncio.run(
        prompt.get_system_message(
            {
                "project_root": "/project",
                "package_root": "/package",
                "extension_root": "/extension",
                "workspace_root": "/workspace",
                "log_root": "/log",
                "python_executable": "python",
                "python_version": "3.12",
                "python_env": "agentos",
                "platform": "Linux",
                "evolution_enabled": evolution_enabled,
            },
            reload=True,
        )
    ).text


def test_meta_agent_remains_one_general_direct_working_orchestrator():
    text = _render_meta(evolution_enabled=False)

    for capability in ("tool", "skill", "connector", "plugin", "sub-agent", "workflow"):
        assert capability in text
    assert "edit code" in text and "bash_tool" in text
    assert "not a pure router" in text
    assert "<self-evolution-rules>" not in text


def test_meta_agent_evolution_rules_follow_the_live_roster_flag():
    text = _render_meta(evolution_enabled=True)

    assert "<self-evolution-rules>" in text
    assert "generate_agent" in text
    assert "optimize_agent" in text
    assert "evaluate_agent" in text


def test_default_agents_share_the_stable_to_live_user_layout():
    """Specialized roles may differ, but their cache-sensitive frame may not."""
    for path in sorted((ROOT / "agentevolver" / "prompt" / "default").glob("*_agent.html")):
        text = path.read_text(encoding="utf-8")
        capability = '<module src="../module/capability_context.html"></module>'
        environment = '<module src="../module/environment_context.html"></module>'
        state = '<module src="../module/agent_context.html"></module>'
        assert capability in text, f"{path.name} has no capability catalog"
        assert environment in text, f"{path.name} has no environment catalog"
        assert state in text, f"{path.name} has no live agent state"
        assert text.index(capability) < text.index(environment) < text.index(state), (
            f"{path.name} must render capability -> environment -> agent context"
        )


def test_default_agents_share_the_core_system_contract_modules():
    """Roles specialize behavior without forking the base Agent contract."""
    required = (
        "input_rules",
        "constraint_rules",
        "context_rules",
        "response_protocol",
    )
    for path in sorted((ROOT / "agentevolver" / "prompt" / "default").glob("*_agent.html")):
        text = path.read_text(encoding="utf-8")
        for module in required:
            include = f'<module src="../module/{module}.html"></module>'
            assert include in text, f"{path.name} does not share {module}"


def test_agent_prompts_use_the_native_calling_protocol():
    """Old JSON actions, `finish`, and synthetic env prefixes break native calls."""
    paths = list((ROOT / "agentevolver" / "prompt" / "default").glob("*_agent.html"))
    paths += [
        ROOT / "agentevolver" / "prompt" / "module" / "response_protocol.html",
        ROOT
        / "agentevolver"
        / "skill"
        / "evolving"
        / "generate_skill"
        / "references"
        / "agent"
        / "html_prompt_template.html",
    ]
    forbidden = ("env__", "`finish`", '"type": "text"', '"name": "text"')
    for path in paths:
        text = path.read_text(encoding="utf-8")
        stale = [marker for marker in forbidden if marker in text]
        assert not stale, f"{path.name} still documents obsolete protocol: {stale}"


def test_generated_agent_template_matches_the_default_capability_frame():
    path = (
        ROOT
        / "agentevolver"
        / "skill"
        / "evolving"
        / "generate_skill"
        / "references"
        / "agent"
        / "html_prompt_template.html"
    )
    text = path.read_text(encoding="utf-8")
    for tag in (
        "tool-context",
        "skill-context",
        "connector-context",
        "plugin-context",
        "workflow-context",
        "subagent-context",
    ):
        assert f"<{tag}>" in text
    assert text.index("<capability-context>") < text.index("<environment-context>")
    assert text.index("<environment-context>") < text.index("<agent-context>")
    assert "<memory>" not in text, "history belongs in native turns, not the live user tail"


def test_the_layout_was_actually_found():
    """The parametrized tests below are vacuous if the glob found nothing.

    This asserted `> 20`, which is a census rather than a guard: deleting prompts fails
    it for the one reason that is fine, and a glob pointed at the wrong directory could
    still pass it while there were enough files. Naming templates that must exist says
    what the guard is actually for.
    """
    assert LEAVES, "no template nests anything in <capability-context>"
    found = {p.name for p in TEMPLATES}
    for expected in ("meta_agent.html", "code_agent.html", "generate_agent.html"):
        assert expected in found, f"{expected} missing — TEMPLATES is not the prompt directory"


@pytest.mark.parametrize("leaf", sorted(LEAVES))
def test_the_stylesheet_reaches_the_leaf_at_its_real_depth(leaf):
    """Nesting broke every `div.user > leaf` selector at once.

    A direct-child selector for a leaf that is now a grandchild matches nothing, and a
    stylesheet that matches nothing fails silently by definition.
    """
    assert f"div.user > {leaf}" not in _css(), (
        f"{leaf} is nested in <{CONTAINER}> but the stylesheet still selects it as a "
        f"direct child of div.user; that rule matches nothing"
    )
    assert re.search(rf"{CONTAINER} > {leaf}\b", _css()), (
        f"the stylesheet never reaches {leaf} at its nested depth"
    )


def test_the_renderer_knows_the_container_holds_children():
    """The break that shipped: `_CONTAINER_TAGS` did not list it.

    A container it does not know is rendered as one leaf — the whole catalog flattened
    into a single blob of text, structure gone.
    """
    match = re.search(r"_CONTAINER_TAGS\s*=\s*new Set\(\[(.*?)\]\)", _js(), re.S)
    assert match, "prompt.js no longer declares _CONTAINER_TAGS"
    assert CONTAINER in match.group(1), (
        f"prompt.js does not treat <{CONTAINER}> as a container; its children would be "
        f"flattened into one block of text"
    )


@pytest.mark.parametrize("leaf", sorted(LEAVES))
def test_the_renderer_cards_every_leaf_the_templates_use(leaf):
    match = re.search(r"_CAPABILITY_TAGS\s*=\s*new Set\(\[(.*?)\]\)", _js(), re.S)
    assert match, "prompt.js no longer declares _CAPABILITY_TAGS"
    assert leaf in match.group(1), f"prompt.js does not render {leaf} as capability cards"


def test_the_splitter_treats_the_container_as_stable():
    """It kept a hand-written list of the four leaf names.

    That list and the templates were two records of one fact. When the blocks were merged
    the list was the copy nobody updated, and the split found nothing stable — sending
    the whole catalog past the point a cache can reach.
    """
    match = re.search(r"for block in \((.*?)\):", _splitter(), re.S)
    assert match, "the stable-block list moved; this check needs updating with it"
    assert f'"{CONTAINER}"' in match.group(1), (
        f"_split_rendered_turn does not treat <{CONTAINER}> as stable content"
    )


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_a_template_puts_its_capabilities_before_its_state(path):
    """The ordering the whole cache gain rests on.

    A cache keeps a prefix. The catalogs are identical every step and the agent state is
    not, so state placed first invalidates everything behind it — which is what the
    templates did, and why 63% of the prompt was re-read at full price every step.
    """
    text = _inline(path.read_text(encoding="utf-8"), path.parent)
    if f"<{CONTAINER}>" not in text or "<agent-context>" not in text:
        pytest.skip("template carries no capability catalog")
    assert text.index(f"<{CONTAINER}>") < text.index("<agent-context>"), (
        f"{path.name} renders its state before its capabilities; nothing after the "
        f"state can be cached"
    )


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_no_template_keeps_a_leaf_outside_the_container(path):
    """Half-migrated is the state a hand edit across 27 files actually produces."""
    text = path.read_text(encoding="utf-8")
    if f"<{CONTAINER}>" not in text:
        return
    outside = re.sub(rf"<{CONTAINER}>.*?</{CONTAINER}>", "", text, flags=re.S)
    stray = {t for t in re.findall(r"<([a-z]+-context)>", outside) if t in LEAVES}
    assert not stray, f"{path.name} leaves {sorted(stray)} outside <{CONTAINER}>"


def test_the_agent_authoring_guide_describes_the_shape_it_will_be_read_against():
    """A generated agent inherits whatever this document says.

    It said "SIBLINGS (not nested)" for as long as that was true, which means a doc left
    stale does not merely mislead a reader — it reproduces the old layout in every agent
    generated afterwards.
    """
    guide = (
        ROOT / "agentevolver" / "skill" / "evolving" / "generate_skill" / "references" / "agent.md"
    ).read_text(encoding="utf-8")
    assert CONTAINER in guide, "the agent-authoring guide never mentions the container"
    assert "as **siblings** of `<agent-context>`" not in guide, (
        "the guide still describes the pre-merge sibling layout"
    )


# ---------------------------------------------------------------------------
# from test_prompt_documents_capabilities.py
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
#: `module/` is where the layout actually lives — `<capability-context>` and its leaves
#: are written once there and pulled into each agent by `<module src>`. It was missing
#: from this list, so `_leaves_used_in_templates` (which reads raw text, not inlined)
#: found its leaves only in `extension/prompt/gaia_answer_agent.html`: a generated,
#: fully-expanded prompt that happened to be committed. Clearing the extension tree
#: emptied the guard, and it said so — which is the one thing that made this visible.
TEMPLATES = (
    sorted((ROOT / "agentevolver" / "prompt" / "default").glob("*.html"))
    + sorted((ROOT / "agentevolver" / "prompt" / "module").glob("*.html"))
    + sorted((ROOT / "extension" / "prompt").glob("*.html"))
)


def _inline(text: str, base: Path) -> str:
    """Resolve `<module src>` includes.

    Shared rules live in modules, so a template can document something without the word
    appearing in its own file. Checking the raw text alone reports four false failures.
    """

    def sub(match):
        path = (base / match.group(1)).resolve()
        return path.read_text(encoding="utf-8") if path.exists() else ""

    return re.sub(r'<module src="([^"]+)"></module>', sub, text)


def _with_capabilities():
    for path in TEMPLATES:
        config = parse_prompt_file(str(path))
        if "capability-context" in (config.user_template or ""):
            yield path, config


def test_there_is_something_to_check():
    """A scan that matches nothing passes vacuously."""
    assert list(_with_capabilities()), "no template carries a capability catalog"


@pytest.mark.parametrize("path", [p for p, _ in _with_capabilities()], ids=lambda p: p.name)
def test_every_catalog_template_explains_the_changes_block(path):
    config = parse_prompt_file(str(path))
    system = _inline(config.system_template or "", path.parent)
    assert "capability-context-changes" in system or "capability_context_changes" in system, (
        f"{path.name} can receive a <capability-context-changes> block but never says so"
    )


@pytest.mark.parametrize("path", [p for p, _ in _with_capabilities()], ids=lambda p: p.name)
def test_the_changes_block_is_described_as_an_amendment(path):
    """ "Supersedes what it names" and "replaces the catalog" are opposite instructions.

    The block lists only what moved, so read as a replacement it would strip the model of
    every capability the delta happens not to mention.
    """
    config = parse_prompt_file(str(path))
    system = _inline(config.system_template or "", path.parent).lower()
    assert any(word in system for word in ("amendment", "supersedes", "does not mention")), (
        f"{path.name} names the block but does not say it amends rather than replaces"
    )


@pytest.mark.parametrize("path", [p for p, _ in _with_capabilities()], ids=lambda p: p.name)
def test_the_explanation_sits_before_the_cache_breakpoint(path):
    """In the system message, not beside the catalog.

    The model-facing explanation of the changes-block must not sit in the user turn's live
    zone, where it is re-read at full price every step. HTML comments are developer notes,
    not model-facing prose, and the layout comment in agent_context.html names the block on
    purpose — strip comments before checking, so the guard is about the rendered prose only.
    """
    config = parse_prompt_file(str(path))
    user = re.sub(r"<!--.*?-->", "", config.user_template or "", flags=re.S)
    tail = user[user.index("</capability-context>") :]
    assert "capability-context-changes" not in tail, (
        f"{path.name} explains the block after the breakpoint, where it is not cacheable"
    )
