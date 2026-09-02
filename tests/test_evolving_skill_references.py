"""What the evolution skills tell an agent to do has to be doable.

These four skills are the framework's instructions for changing itself: `generate_skill`,
`optimize_skill` and `evaluate_skill` are read by the three evolution agents, and
`self_evolving_skill` decides which of them runs. Prose is their entire interface, so a
name that has gone stale is not a documentation defect — it is an instruction that costs
a run several turns and then fails.

Two kinds had gone stale and neither could fail loudly:

`inspect_agent_tool`, `inspect_connector_tool`, `inspect_environment_tool`,
`inspect_memory_tool` and `inspect_skill_tool` were named as the first step of eight
different procedures. All five were consolidated into one `inspect_tool` that takes a
`capability_type`, so every one of those procedures opened by calling something that does
not exist.

`references/connector/evaluation.md` closed with two hundred lines describing an
evaluation harness — CLI flags, XML input, output format, troubleshooting — for a
`scripts/evaluation.py` that was never shipped. It contradicted `references/connector.md`
in the same skill, which says the comparison is run by dispatching agents.
"""

import re
from pathlib import Path

import pytest

SKILLS_ROOT = Path(__file__).parents[1] / "agentevolver" / "skill" / "evolving"
SKILLS = sorted(p for p in SKILLS_ROOT.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())
DOCS = sorted(SKILLS_ROOT.rglob("*.md"))

#: A bundled resource as these documents cite one.
RESOURCE = re.compile(r"`((?:scripts|references)/[A-Za-z0-9_./-]+)`")
#: A capability named the way an instruction names one to *call*. Deliberately only the
#: two suffixes that are invoked: a skill is read, not called, and `_skill` also matches
#: JSON field values like `with_skill` in the report schemas, which are data.
CAPABILITY = re.compile(r"`([a-z][a-z0-9_]*_(?:tool|agent))`")
#: Fenced blocks are examples and payloads, not instructions.
FENCE = re.compile(r"```.*?```", re.DOTALL)


def _skill_of(doc: Path) -> Path:
    """The skill directory a document belongs to; its resource paths are relative to it."""
    return next(s for s in SKILLS if s in doc.parents or s == doc.parent)


def test_there_are_skills_and_documents_to_check():
    """Guards the guard: an empty sweep would pass everything below."""
    assert len(SKILLS) == 4, [s.name for s in SKILLS]
    assert len(DOCS) >= 25


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(SKILLS_ROOT)))
def test_every_bundled_resource_a_document_cites_exists(doc):
    """`scripts/…` and `references/…` are paths the agent is told to run or read."""
    skill = _skill_of(doc)
    prose = FENCE.sub("", doc.read_text(encoding="utf-8"))
    missing = sorted(rel for rel in set(RESOURCE.findall(prose))
                     if not (skill / rel).exists())
    assert not missing, f"cites resources that do not exist: {missing}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(SKILLS_ROOT)))
def test_every_capability_a_document_names_is_registered(doc):
    """A tool, agent or skill named in an instruction has to be one that exists.

    Only names shaped like a registered capability are checked, so ordinary prose is
    untouched. The registry is the authority rather than a hand-kept list, so a renamed
    capability fails here instead of in a run.
    """
    import agentevolver.agent  # noqa: F401 - registers the built-in agents
    import agentevolver.tool  # noqa: F401 - registers the built-in tools
    from agentevolver.registry import AGENT, TOOL

    known = set()
    for registry in (TOOL, AGENT):
        for key, cls in registry.module_dict.items():
            field = getattr(cls, "model_fields", {}).get("name")
            known.add(getattr(field, "default", None) or key)

    named = set(CAPABILITY.findall(FENCE.sub("", doc.read_text(encoding="utf-8"))))
    # Templates name the capability the agent is about to write, which cannot exist yet.
    named -= {"my_agent", "my_tool", "my_procedural_agent", "example_tool"}
    unknown = sorted(named - known)
    assert not unknown, f"names capabilities that are not registered: {unknown}"
