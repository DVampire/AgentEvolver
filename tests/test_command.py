"""Command dispatch: the control plane's parse → look up → run path.

Dispatch is the module's whole job, and its defining promise is that it never
raises: a typo, an empty line, or a command that blows up mid-run all have to
come back as a ``Response`` the session can render, because this layer sits in
front of the agent loop and taking it down takes the session with it.
"""

import pytest

from agentevolver.command.context import CommandContextManager
from agentevolver.command.types import Command, CommandContext, CommandType
from agentevolver.response.types import ResponseType

class Echo(Command):
    name: str = "echo"
    description: str = "Echo the arguments back"
    usage: str = "/echo <words...>"

    async def __call__(self, args, ctx=None):
        return self.ok(" ".join(args), data={"ctx_name": ctx.name if ctx else None})


class Exploding(Command):
    name: str = "boom"
    description: str = "Always raises"

    async def __call__(self, args, ctx=None):
        raise RuntimeError("detonated")


class Packaged(Command):
    name: str = "evolve"
    description: str = "Routes to an agent"
    type: CommandType = CommandType.SKILL


@pytest.fixture
def commands():
    """A manager loaded by hand — ``initialize`` would pull in the real registry."""
    manager = CommandContextManager()
    for cmd in (Echo(), Exploding(), Packaged()):
        manager._commands[cmd.name] = cmd
    return manager


# --------------------------------------------------------------------- parsing
@pytest.mark.parametrize(
    "raw, name, args",
    [
        ("/rollback tool foo 1.2", "rollback", ["tool", "foo", "1.2"]),
        ("rollback tool foo", "rollback", ["tool", "foo"]),   # the slash is optional
        ("  /echo   a   b  ", "echo", ["a", "b"]),            # runs of spaces collapse
        ("/echo", "echo", []),
    ],
)
@pytest.mark.asyncio
async def test_a_raw_line_splits_into_a_name_and_its_tail(commands, raw, name, args):
    assert commands._parse(raw) == (name, args)


@pytest.mark.parametrize("raw", ["", "   ", "/", "  /  "])
@pytest.mark.asyncio
async def test_a_line_with_no_command_parses_to_nothing(commands, raw):
    assert commands._parse(raw) == (None, [])


@pytest.mark.asyncio
async def test_an_empty_line_is_refused_not_dispatched(commands):
    result = await commands.dispatch("")
    assert result.success is False
    assert result.message == "Empty command."
    assert result.type is ResponseType.COMMAND


# ------------------------------------------------------------------- dispatch
@pytest.mark.asyncio
async def test_a_known_command_runs_and_receives_its_arguments(commands):
    result = await commands.dispatch("/echo hello world")
    assert result.success is True
    assert result.message == "hello world"


@pytest.mark.asyncio
async def test_dispatch_synthesizes_a_context_when_none_is_given(commands):
    """Commands may read ``ctx``; dispatch must never hand them ``None``."""
    result = await commands.dispatch("/echo x")
    assert result.data["ctx_name"] == "echo"


@pytest.mark.asyncio
async def test_a_caller_supplied_context_is_passed_through(commands):
    ctx = CommandContext(name="preset", raw="/echo x", workspace_root="/tmp/ws")
    result = await commands.dispatch("/echo x", ctx)
    assert result.data["ctx_name"] == "preset"


@pytest.mark.asyncio
async def test_an_unknown_command_lists_what_is_available(commands):
    result = await commands.dispatch("/nope")
    assert result.success is False
    assert "Unknown command /nope" in result.message
    # The listing is the recovery path — it must name the real commands.
    assert "/echo" in result.message and "/evolve" in result.message


@pytest.mark.asyncio
async def test_a_command_that_raises_comes_back_as_a_failed_response(commands):
    """The control plane sits in front of the agent loop; it must not crash."""
    result = await commands.dispatch("/boom")
    assert result.success is False
    assert "detonated" in result.message
    assert result.type is ResponseType.COMMAND


# ----------------------------------------------------------------------- help
@pytest.mark.parametrize("raw", ["/help", "/?", "help", "?"])
@pytest.mark.asyncio
async def test_help_is_answered_without_a_registered_command(commands, raw):
    result = await commands.dispatch(raw)
    assert result.success is True
    assert "/echo" in result.message


@pytest.mark.asyncio
async def test_help_groups_by_type_and_sorts_within_a_group(commands):
    lines = (await commands.help()).splitlines()
    assert lines[0] == "[control]"
    assert "[skill]" in lines
    control = [line for line in lines[1:lines.index("[skill]")]]
    names = [line.strip().split()[0] for line in control]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_help_shows_the_usage_hint_when_one_is_declared(commands):
    assert "/echo <words...>  —  Echo the arguments back" in await commands.help()


@pytest.mark.asyncio
async def test_a_command_without_a_usage_hint_falls_back_to_its_name(commands):
    assert "/boom  —  Always raises" in await commands.help()


@pytest.mark.asyncio
async def test_an_empty_group_is_omitted_rather_than_left_as_a_bare_header():
    manager = CommandContextManager()
    manager._commands["echo"] = Echo()
    assert "[skill]" not in await manager.help()


# ------------------------------------------------------------------ lifecycle
@pytest.mark.asyncio
async def test_listing_reports_the_registered_names(commands):
    assert sorted(await commands.list()) == ["boom", "echo", "evolve"]


@pytest.mark.asyncio
async def test_get_returns_none_for_an_unknown_name(commands):
    assert await commands.get("echo") is not None
    assert await commands.get("ghost") is None


@pytest.mark.asyncio
async def test_cleanup_empties_the_registry(commands):
    await commands.cleanup()
    assert await commands.list() == []
    assert (await commands.dispatch("/echo x")).success is False


# ---------------------------------------------------------------- declarations
def test_a_command_defaults_to_deterministic_control():
    """SKILL commands cost a model call; that must be opted into, not inherited."""
    assert Echo().type is CommandType.CONTROL


def test_a_command_defaults_to_the_least_privileged_useful_mode():
    assert Echo().permission_mode == "workspace_write"


def test_the_convenience_builders_carry_the_command_response_type():
    assert Echo().ok("done").type is ResponseType.COMMAND
    assert Echo().fail("nope").success is False


@pytest.mark.asyncio
async def test_the_base_command_refuses_to_run_unimplemented():
    class Bare(Command):
        name: str = "bare"
        description: str = "no body"

    with pytest.raises(NotImplementedError):
        await Bare()([], None)
