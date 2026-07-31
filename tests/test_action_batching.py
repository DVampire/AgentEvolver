"""Actor prompts must tell the model it can batch a turn's actions.

A measured ProgramBench run spent 34 of its 55 minutes waiting on the model, having
asked for exactly one action in 84% of its turns while ``max_actions`` was 10. The
framework already dispatches a turn's batch concurrently
(``Agent._dispatch_round``) and ``browser_agent`` already carried this guidance —
nothing told the other agents.

Kept out of test_prompt_manager.py deliberately: that module is marked
``integration`` and is deselected by the default ``-m 'not integration'``.
"""
import os

from types import SimpleNamespace

import pytest

from agentevolver.agent.types import Agent
from agentevolver.prompt.types import _render_template, parse_prompt_file

# --- action batching --------------------------------------------------------
_BATCHING_PROMPTS = ("code_agent", "meta_agent", "general_agent", "reviewer_agent")

_RENDER_MODULES = dict(
    max_actions=10, extension_root="/e", package_root="/p", project_root="/pr",
    workspace_root="/w", log_root="/l", python_executable="/py", python_version="3.12",
    platform="linux", shell="bash", cwd="/w", evolution_enabled=True,
)


def _render(name):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = parse_prompt_file(os.path.join(
        root, "agentevolver", "prompt", "default", f"{name}.html"))
    return _render_template(cfg.system_template, _RENDER_MODULES)


@pytest.mark.parametrize("name", _BATCHING_PROMPTS)
def test_actor_prompts_carry_the_batching_rules(name):
    out = _render(name)
    assert "action-batching-rules" in out
    # The action budget must be substituted, not left as a literal or a None.
    assert "up to 10 actions" in out
    assert "None actions" not in out
    # Both halves of the rule: batch independents, chain dependents in one command.
    assert "same turn" in out
    assert "&&" in out


@pytest.mark.parametrize("name", _BATCHING_PROMPTS)
def test_batching_rules_warn_against_the_two_ways_to_get_it_wrong(name):
    out = _render(name)
    # Writing a script purely to batch is two round-trips, not one.
    assert "script file merely to batch" in out
    # Batching across an unresolved dependency is worse than not batching.
    assert "dependency you have not resolved" in out


def test_browser_agent_keeps_its_own_batching_guidance():
    """It had this first, tuned to page state; do not overwrite it."""
    out = _render("browser_agent")
    assert "action-batching-rules" not in out
    assert "Never plan past a page change" in out


# --- max_actions is a cap, not a suggestion ----------------------------------
# It reached the prompt and nothing enforced it. After the batching guidance was
# added, a ProgramBench run produced 943 tool calls across 71 turns — one turn asked
# for hundreds of `readelf | grep <symbol>` probes, and 25% of the run's budget went
# into static analysis the task forbids.

class _CapAgent(Agent):
    name: str = "cap_agent"
    description: str = "test"
    metadata: dict = {}


def _calls(n):
    return [SimpleNamespace(name="bash_tool", id=str(i), input={}) for i in range(n)]


def test_an_oversized_batch_is_capped_at_max_actions():
    agent = _CapAgent(base_dir="/tmp", use_memory=False, max_actions=10)
    kept = agent._cap_actions(_calls(50))
    assert len(kept) == 10
    # The earliest calls survive: the tail was planned without seeing any result.
    assert [c.id for c in kept] == [str(i) for i in range(10)]


def test_a_batch_within_the_cap_is_untouched():
    agent = _CapAgent(base_dir="/tmp", use_memory=False, max_actions=10)
    calls = _calls(3)
    assert agent._cap_actions(calls) is calls


def test_the_cap_follows_the_configured_value():
    agent = _CapAgent(base_dir="/tmp", use_memory=False, max_actions=2)
    assert len(agent._cap_actions(_calls(9))) == 2


def test_think_applies_the_cap():
    """Pin the wiring, not just the helper — an unused cap fixes nothing."""
    import inspect

    from agentevolver.agent.types import Agent as BaseAgent

    assert "_cap_actions" in inspect.getsource(BaseAgent._think)
