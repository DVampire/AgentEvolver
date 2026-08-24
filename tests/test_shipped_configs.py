"""Every shipped config still loads.

Nothing checked this, and the gap is not theoretical: merging the twenty-one evolution
agents into three deleted `configs/agents/tool_generate_agent.py` and eight siblings, and
`configs/programbench_agent.py` went on importing all nine. That config could not be loaded
at all — `ConfigParsingError: configs/agents/tool_generate_agent.py not found!` — while the
whole suite stayed green, because a config is plain Python that nothing imports until
someone runs it.

Which makes it the one kind of breakage the test suite is structurally blind to. A config
names agents, tools, skills and environments as strings and by import, so it goes stale
whenever any of those is renamed or removed — exactly the changes a self-evolving framework
makes to itself.

Loading is all this asserts. It is enough: an import of something deleted, a name used but
no longer defined, a `.update()` on a dict that no longer exists — all of them raise here,
and all of them are what actually goes wrong.
"""

from __future__ import annotations

import argparse
import contextlib
import io
from pathlib import Path

import pytest

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
#: `base.py` and `__init__.py` are fragments other configs read through `read_base()`;
#: they are not entry points and do not stand alone.
NOT_ENTRY_POINTS = {"__init__.py", "base.py"}


def _entry_points():
    return sorted(p for p in CONFIGS.glob("*.py") if p.name not in NOT_ENTRY_POINTS)


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.stem)
def test_a_shipped_config_loads(path):
    """Parametrised, so a failure names the config rather than "one of twelve"."""
    from agentevolver.config import config

    # `mmengine` prints the merged config; the assertion is that this does not raise.
    with contextlib.redirect_stdout(io.StringIO()):
        config.initialize(config_path=str(path), args=argparse.Namespace())


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.stem)
def test_every_agent_a_config_names_is_configured(path):
    """A name in `agent_names` with no config dict beside it is a registration that fails.

    The roster and the per-agent config blocks are two lists that have to agree, and they
    are pages apart in the file. Dropping an agent from one and not the other is how nine
    stale `.update()` calls survived their imports being deleted.
    """
    from agentevolver.config import config

    with contextlib.redirect_stdout(io.StringIO()):
        config.initialize(config_path=str(path), args=argparse.Namespace())

    missing = [name for name in (getattr(config, "agent_names", None) or [])
               if not isinstance(getattr(config, name, None), dict)]
    assert not missing, f"named in agent_names with no config block: {missing}"


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.stem)
def test_every_tool_a_config_names_is_registered(path):
    """A config may only name tools that exist.

    Same failure shape as the agents, one layer out: `tool_names` is strings, so a renamed
    or deleted tool leaves a config that loads fine and then fails when the manager is
    asked for something that is not there.
    """
    import agentevolver.tool.default  # noqa: F401 — importing is what registers them
    from agentevolver.config import config
    from agentevolver.registry import TOOL

    with contextlib.redirect_stdout(io.StringIO()):
        config.initialize(config_path=str(path), args=argparse.Namespace())

    known = {
        field.default
        for cls in TOOL.module_dict.values()
        for field in [getattr(cls, "model_fields", {}).get("name")]
        if field is not None and isinstance(field.default, str)
    }
    unknown = [name for name in (getattr(config, "tool_names", None) or [])
               if name not in known]
    assert not unknown, f"named in tool_names but not registered: {unknown}"


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.stem)
def test_every_environment_a_config_names_is_registered(path):
    """A config may only mount environments that exist.

    The third of the same failure: `env_names` is strings, so a renamed or missing
    environment leaves a config that loads fine and fails when the manager is asked for it.

    This one also guards a migration shape. Turning tools into an environment means
    deleting names from `tool_names` and adding one to `env_names`, and the two halves are
    edited separately — the terminal migration did the first for every config and the
    second for only some, so two agents silently lost the capability entirely. A name that
    does not resolve fails here; the *absence* of a name cannot be caught generically,
    which is why `test_every_agent_with_bash_can_collect_a_background_job` states that one
    dependency by hand.
    """
    import agentevolver.environment.default  # noqa: F401 — importing is what registers them
    from agentevolver.config import config
    from agentevolver.registry import ENVIRONMENT

    with contextlib.redirect_stdout(io.StringIO()):
        config.initialize(config_path=str(path), args=argparse.Namespace())

    known = {
        field.default
        for cls in ENVIRONMENT.module_dict.values()
        for field in [getattr(cls, "model_fields", {}).get("name")]
        if field is not None and isinstance(field.default, str)
    }
    unknown = [name for name in (getattr(config, "env_names", None) or [])
               if name not in known]
    assert not unknown, f"named in env_names but not registered: {unknown}"


def test_a_commented_out_capability_still_names_one_that_exists():
    """A config's commented-out lines are a menu, and a menu goes stale silently.

    `tool_names` and `env_names` carry as many commented entries as live ones — that is
    how a shipped config offers a capability without turning it on. Every check above
    reads only the live entries, so a renamed or deleted capability rots in the comments
    with the suite green, and the reader who uncomments it gets a registry miss.

    It had already happened: `# "job_list_tool"`, `# "job_output_tool"` and
    `# "job_kill_tool"` outlived the tools by a migration, above an `env_names` block
    that had `"job"` switched on the whole time. The comment did not merely fail to
    work — it pointed at three dead names for a capability that was already live.

    Only quoted names on comment lines count, so prose that mentions a tool is not a
    false positive; a commented-out list entry always carries its quotes.
    """
    import re

    import agentevolver.environment.default  # noqa: F401 — importing is what registers them
    import agentevolver.tool.default  # noqa: F401
    from agentevolver.registry import ENVIRONMENT, TOOL

    live = {
        field.default
        for registry in (TOOL, ENVIRONMENT)
        for cls in registry.module_dict.values()
        for field in [getattr(cls, "model_fields", {}).get("name")]
        if field is not None and isinstance(field.default, str)
    }

    # An agent is not in a registry — it is a config fragment beside this one, and the
    # fragment file is the thing a commented-out name would be missing.
    agents = {path.stem for path in (CONFIGS / "agents").glob("*.py")}

    stale = []
    for path in sorted(CONFIGS.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip().startswith("#"):
                continue
            for name in re.findall(r'"([a-z][a-z0-9_]*)"\s*,', line):
                if name.endswith("_agent"):
                    known = name in agents
                elif name.endswith(("_tool", "_environment")):
                    known = name in live
                else:
                    continue
                if not known:
                    stale.append(f"{path.relative_to(CONFIGS.parent)}:{number} {name}")

    assert not stale, (
        "commented-out entries naming capabilities that no longer exist:\n  "
        + "\n  ".join(stale)
        + "\nDelete the line, or update it to what replaced the capability."
    )
