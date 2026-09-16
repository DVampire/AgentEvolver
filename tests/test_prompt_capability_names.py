"""A capability named in a prompt has to be one that exists.

Prose is the whole interface between a prompt and the model, so a stale name is not a
documentation defect — it is an instruction to call something that is not there, and the
model spends turns discovering that. It has happened twice.

`inspect_agent_tool`, `inspect_connector_tool`, `inspect_environment_tool`,
`inspect_memory_tool` and `inspect_skill_tool` opened eight procedures across the
evolution skills after all five had been consolidated into one `inspect_tool` taking a
`capability_type`. And `evolution_tool` was named in six prompts and eight configs when
it was renamed to `adoption_tool` — this test is what makes that rename checkable
instead of hopeful.

The evolving skills have their own version of this in
`tests/test_evolving_skill_references.py`; this one covers every shipped prompt, which is
where a rename is most likely to be missed.

Task documents are deliberately out of scope. A task names what the run is being asked to
*create* — `examples/tasks/agent_generate.html` asks for a `summary_agent` and
`gaia_sales_evolution.html` for an `xlsx_reader_tool` — so an unregistered name there is
the point of the task rather than a defect in it.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PROMPTS = sorted((ROOT / "agentevolver" / "prompt" / "default").glob("*.html"))

#: A capability named the way an instruction names one to call. Only the two suffixes
#: that are invoked: a skill is read rather than called, and `_skill` also matches JSON
#: field values like `with_skill` in report schemas, which are data.
CAPABILITY = re.compile(r"`([a-z][a-z0-9_]*_(?:tool|agent))`")
#: Fenced and preformatted blocks are examples and payloads, not instructions.
LITERAL = re.compile(r"```.*?```|<pre.*?</pre>", re.DOTALL)

#: Names a prompt may use for something that does not exist yet: a template's
#: placeholder, or a capability the run is being told to create.
PLACEHOLDERS = frozenset({
    "my_agent", "my_tool", "my_procedural_agent", "example_tool", "new_tool",
})


def _registered() -> set:
    import agentevolver.agent  # noqa: F401 - registers the built-in agents
    import agentevolver.tool  # noqa: F401 - registers the built-in tools
    from agentevolver.registry import AGENT, TOOL

    names = set()
    for registry in (TOOL, AGENT):
        for key, cls in registry.module_dict.items():
            field = getattr(cls, "model_fields", {}).get("name")
            names.add(getattr(field, "default", None) or key)
    return names


def _named(path: Path) -> set:
    text = LITERAL.sub("", path.read_text(encoding="utf-8", errors="replace"))
    return set(CAPABILITY.findall(text)) - PLACEHOLDERS


def test_there_are_prompts_to_check():
    """Guards the guard: an empty sweep would pass every case below."""
    assert len(PROMPTS) >= 10, [p.name for p in PROMPTS]


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.stem)
def test_every_capability_a_prompt_names_is_registered(prompt):
    unknown = sorted(_named(prompt) - _registered())
    assert not unknown, (
        f"{prompt.name} tells the model to call capabilities that are not registered: "
        f"{unknown}"
    )
