"""What a self-evolution run copies has to actually work.

`generate_skill` hands these files to the model as the shape a new component takes, and
whatever comes back is written into `{extension_root}` and registered. A template that
does not run is therefore not a documentation bug: every component generated from it is
born broken, and the failure surfaces as the *generated* agent failing to construct,
which reads like the model wrote something wrong.

Both agent templates were in exactly that state. Each hand-wrote an `__init__` whose
parameters all defaulted to `None` and forwarded them to the base, and pydantic rejects
`None` for a `str` field — so anything generated from either failed with five validation
errors before it ever ran a step. Nothing caught it because no test had ever imported a
template; they were treated as prose.
"""

import importlib.util
from pathlib import Path

import pytest

TEMPLATE_ROOT = Path(__file__).parents[1] / "agentevolver" / "skill" / "evolving"
TEMPLATES = sorted(TEMPLATE_ROOT.rglob("*_template.py"))


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"template_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _declared(module):
    """Classes the template itself defines, as opposed to what it imported."""
    return [
        value for name, value in vars(module).items()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and not name.startswith("_")
    ]


def test_there_are_templates_to_check():
    """Guards the guard: an empty sweep would pass every parametrised case below."""
    assert len(TEMPLATES) >= 4, f"found only {[p.name for p in TEMPLATES]}"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_a_template_imports(path):
    _load(path)


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_a_template_constructs_with_no_arguments(path):
    """The generated file is instantiated by a manager that passes nothing of its own.

    So default construction is the contract, not a convenience. A template that needs an
    argument to exist cannot be copied, which is the one thing a template is for.
    """
    declared = _declared(_load(path))
    assert declared, f"{path.name} declares no class to copy"
    for cls in declared:
        try:
            cls()
        except Exception as error:  # noqa: BLE001 - any raise is a broken template
            pytest.fail(f"{path.name}: {cls.__name__}() raised "
                        f"{type(error).__name__}: {error}")


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_a_template_names_only_seams_that_still_exist(path):
    """A template pointing at a method the base no longer has teaches the model to
    override nothing.

    The tool-calling template told the model to inherit `_get_agent_context`,
    `_get_messages` and `_think_and_act`. All three were gone; the loop is
    `__call__ → think → act` now.
    """
    from agentevolver.agent.types import Agent

    text = path.read_text(encoding="utf-8")
    gone = [
        name for name in ("_get_agent_context", "_get_messages", "_think_and_act",
                          "_prepare_round", "_advance_once", "_dispatch_round",
                          "_run_one_bg", "_on_round_complete")
        if name in text and not hasattr(Agent, name)
    ]
    assert not gone, f"{path.name} names methods the base class no longer has: {gone}"
