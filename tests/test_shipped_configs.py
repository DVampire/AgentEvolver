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
