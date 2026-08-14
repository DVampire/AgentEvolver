"""The capability layout is stated in five places; they must not drift apart.

Merging four blocks into `<capability-context>` meant editing 27 templates, a stylesheet
whose selectors were direct-child, a renderer with a hardcoded container list, a splitter
with a hardcoded tag list, and two documents that told future agents the old shape. Every
one was edited by hand, and the renderer was missed — the container was flattened into a
wall of text, which is a functional break, not a cosmetic one, and no test failed.

Each check below derives its expectation from the templates rather than restating it, so
a template that adopts a new leaf drags the stylesheet, the renderer and the splitter
into the failure with it.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = sorted((ROOT / "agentevolver" / "prompt" / "default").glob("*.html")) + \
            sorted((ROOT / "extension" / "prompt").glob("*.html"))
CSS = (ROOT / "agentevolver" / "visual" / "css" / "prompt.css").read_text(encoding="utf-8")
JS = (ROOT / "agentevolver" / "visual" / "js" / "prompt.js").read_text(encoding="utf-8")
SPLITTER = (ROOT / "agentevolver" / "agent" / "types.py").read_text(encoding="utf-8")

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


def test_the_layout_was_actually_found():
    assert LEAVES, "no template nests anything in <capability-context>"
    assert len(TEMPLATES) > 20, f"only {len(TEMPLATES)} templates found"


@pytest.mark.parametrize("leaf", sorted(LEAVES))
def test_the_stylesheet_reaches_the_leaf_at_its_real_depth(leaf):
    """Nesting broke every `div.user > leaf` selector at once.

    A direct-child selector for a leaf that is now a grandchild matches nothing, and a
    stylesheet that matches nothing fails silently by definition.
    """
    assert f"div.user > {leaf}" not in CSS, (
        f"{leaf} is nested in <{CONTAINER}> but the stylesheet still selects it as a "
        f"direct child of div.user; that rule matches nothing")
    assert re.search(rf"{CONTAINER} > {leaf}\b", CSS), (
        f"the stylesheet never reaches {leaf} at its nested depth")


def test_the_renderer_knows_the_container_holds_children():
    """The break that shipped: `_CONTAINER_TAGS` did not list it.

    A container it does not know is rendered as one leaf — the whole catalog flattened
    into a single blob of text, structure gone.
    """
    match = re.search(r"_CONTAINER_TAGS\s*=\s*new Set\(\[(.*?)\]\)", JS, re.S)
    assert match, "prompt.js no longer declares _CONTAINER_TAGS"
    assert CONTAINER in match.group(1), (
        f"prompt.js does not treat <{CONTAINER}> as a container; its children would be "
        f"flattened into one block of text")


@pytest.mark.parametrize("leaf", sorted(LEAVES))
def test_the_renderer_cards_every_leaf_the_templates_use(leaf):
    match = re.search(r"_CAPABILITY_TAGS\s*=\s*new Set\(\[(.*?)\]\)", JS, re.S)
    assert match, "prompt.js no longer declares _CAPABILITY_TAGS"
    assert leaf in match.group(1), f"prompt.js does not render {leaf} as capability cards"


def test_the_splitter_treats_the_container_as_stable():
    """It kept a hand-written list of the four leaf names.

    That list and the templates were two records of one fact. When the blocks were merged
    the list was the copy nobody updated, and the split found nothing stable — sending
    the whole catalog past the point a cache can reach.
    """
    match = re.search(r"for block in \((.*?)\):", SPLITTER, re.S)
    assert match, "the stable-block list moved; this check needs updating with it"
    assert f'"{CONTAINER}"' in match.group(1), (
        f"_split_rendered_turn does not treat <{CONTAINER}> as stable content")


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
        f"state can be cached")


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
    guide = (ROOT / "agentevolver" / "skill" / "creator" / "agent_creator_skill"
             / "SKILL.md").read_text(encoding="utf-8")
    assert CONTAINER in guide, "the agent-authoring guide never mentions the container"
    assert "as **siblings** of `<agent-context>`" not in guide, \
        "the guide still describes the pre-merge sibling layout"
