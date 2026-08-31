"""Command dispatch: the control plane's parse → look up → run path.

Dispatch is the module's whole job, and its defining promise is that it never raises: a
typo, an empty line, or a command that blows up mid-run all have to come back as a
``Response`` the session can render, because this layer sits in front of the agent loop
and taking it down takes the session with it.

The other half is what a command is by default. A command declares its type and its
permission mode, and both defaults are chosen to be the safe reading — deterministic, and
scoped to the workspace — so that a new command file that declares neither cannot
accidentally reach the model or the wider filesystem.
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
    """A SKILL command: present in the registry so help() has two groups to render."""

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


# --------------------------------------------------------------------------- #
# Reading a raw line
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, name, args",
    [
        ("/rollback tool foo 1.2", "rollback", ["tool", "foo", "1.2"]),
        ("rollback tool foo", "rollback", ["tool", "foo"]),  # the slash is optional
        ("  /echo   a   b  ", "echo", ["a", "b"]),  # runs of spaces collapse
        ("/echo", "echo", []),
    ],
)
@pytest.mark.asyncio
async def test_a_raw_line_splits_into_a_name_and_its_tail(commands, raw, name, args):
    """What a human types is not what a parser wants, and the gap is where typos live.

    Each row is a way the same command is really entered: with the slash and without,
    padded, double-spaced, or with nothing after the name. A parser that kept the slash on
    the name would fail every lookup; one that split on a single space would hand a
    command empty-string arguments it then has to defend against.
    """
    assert commands._parse(raw) == (name, args)


@pytest.mark.parametrize("raw", ["", "   ", "/", "  /  "])
@pytest.mark.asyncio
async def test_a_line_with_no_command_parses_to_nothing(commands, raw):
    """A bare slash is a name of zero characters, which must not become a lookup."""
    assert commands._parse(raw) == (None, [])


@pytest.mark.asyncio
async def test_an_empty_line_is_refused_not_dispatched(commands):
    """Pressing enter on an empty prompt is the most common input there is.

    It comes back as an ordinary COMMAND response rather than an exception, so the session
    renders a line and carries on — this is the cheapest possible test of the promise the
    module makes everywhere else.
    """
    result = await commands.dispatch("")
    assert result.success is False
    assert result.message == "Empty command."
    assert result.type is ResponseType.COMMAND


# --------------------------------------------------------------------------- #
# Running one
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_known_command_runs_and_receives_its_arguments(commands):
    result = await commands.dispatch("/echo hello world")
    assert result.success is True
    assert result.message == "hello world"


@pytest.mark.asyncio
async def test_dispatch_synthesizes_a_context_when_none_is_given(commands):
    """Commands may read ``ctx``; dispatch must never hand them ``None``.

    Every command would otherwise need its own None check before touching the context, and
    the one that forgets crashes the control plane on the path where no caller supplied
    one — which is the path a plain typed command takes.
    """
    result = await commands.dispatch("/echo x")
    assert result.data["ctx_name"] == "echo"


@pytest.mark.asyncio
async def test_a_caller_supplied_context_is_passed_through(commands):
    """The synthesized context is a fallback, not a replacement.

    A gateway session builds a context carrying its own workspace root; overwriting it
    would run the command against a different filesystem boundary than the session it was
    typed into.
    """
    ctx = CommandContext(name="preset", raw="/echo x", workspace_root="/tmp/ws")
    result = await commands.dispatch("/echo x", ctx)
    assert result.data["ctx_name"] == "preset"


@pytest.mark.asyncio
async def test_an_unknown_command_lists_what_is_available(commands):
    """A misspelled command is a person who does not know the exact name.

    "Unknown command" alone sends them to the docs; the listing answers the question they
    actually had, in the place they asked it.
    """
    result = await commands.dispatch("/nope")
    assert result.success is False
    assert "Unknown command /nope" in result.message
    # The listing is the recovery path — it must name the real commands.
    assert "/echo" in result.message and "/evolve" in result.message


@pytest.mark.asyncio
async def test_a_command_that_raises_comes_back_as_a_failed_response(commands):
    """The control plane sits in front of the agent loop; it must not crash.

    Commands are ordinary Python written per file, so one of them raising is a matter of
    time. The exception text has to reach the message, or the session shows a failure with
    no way to tell which command failed or why.
    """
    result = await commands.dispatch("/boom")
    assert result.success is False
    assert "detonated" in result.message
    assert result.type is ResponseType.COMMAND


# --------------------------------------------------------------------------- #
# The help listing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", ["/help", "/?", "help", "?"])
@pytest.mark.asyncio
async def test_help_is_answered_without_a_registered_command(commands, raw):
    """There is no `/help` command; dispatch answers it before it reaches the lookup.

    That is what makes all four spellings work, and what keeps help available when the
    registry is short of whatever a real help command would have been. The spellings
    matter on their own: `?` and `/?` are what someone types when they do not yet know
    that the slash is optional.
    """
    result = await commands.dispatch(raw)
    assert result.success is True
    assert "/echo" in result.message


@pytest.mark.asyncio
async def test_help_groups_by_type_and_sorts_within_a_group(commands):
    """Grouping tells a reader which commands cost a model call before they run one.

    Control commands come first because they are the ones that are always safe to try, and
    sorting inside a group is what makes the list scannable as it grows past a screenful.
    """
    lines = (await commands.help()).splitlines()
    assert lines[0] == "[control]"
    assert "[skill]" in lines
    control = [line for line in lines[1 : lines.index("[skill]")]]
    names = [line.strip().split()[0] for line in control]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_help_shows_the_usage_hint_when_one_is_declared(commands):
    """A command taking arguments is unusable from a name and a description alone."""
    assert "/echo <words...>  —  Echo the arguments back" in await commands.help()


@pytest.mark.asyncio
async def test_a_command_without_a_usage_hint_falls_back_to_its_name(commands):
    """Omitting `usage` is normal for a command that takes nothing; the line still renders."""
    assert "/boom  —  Always raises" in await commands.help()


@pytest.mark.asyncio
async def test_an_empty_group_is_omitted_rather_than_left_as_a_bare_header():
    """A `[skill]` heading with nothing under it reads as commands that failed to load."""
    manager = CommandContextManager()
    manager._commands["echo"] = Echo()
    assert "[skill]" not in await manager.help()


# --------------------------------------------------------------------------- #
# Registry lifecycle
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_listing_reports_the_registered_names(commands):
    assert sorted(await commands.list()) == ["boom", "echo", "evolve"]


@pytest.mark.asyncio
async def test_get_returns_none_for_an_unknown_name(commands):
    """Lookup answers with None rather than raising: dispatch turns that into a listing."""
    assert await commands.get("echo") is not None
    assert await commands.get("ghost") is None


@pytest.mark.asyncio
async def test_cleanup_empties_the_registry(commands):
    """After teardown a dispatch must fail as unknown, not reach a half-released command.

    Cleanup runs while a session is shutting down, and anything still dispatchable
    afterwards runs against state that is on its way out.
    """
    await commands.cleanup()
    assert await commands.list() == []
    assert (await commands.dispatch("/echo x")).success is False


# --------------------------------------------------------------------------- #
# What a command is until it says otherwise
# --------------------------------------------------------------------------- #
def test_a_command_defaults_to_deterministic_control():
    """SKILL commands cost a model call; that must be opted into, not inherited.

    Defaulting the other way would mean a new command file that forgot to declare its type
    quietly routes to an agent — same result most of the time, paid for every time, and
    non-deterministic in a layer whose value is that it is not.
    """
    assert Echo().type is CommandType.CONTROL


def test_a_command_defaults_to_the_least_privileged_useful_mode():
    """Workspace-write, not full access: an undeclared command stays inside the boundary."""
    assert Echo().permission_mode == "workspace_write"


def test_the_convenience_builders_carry_the_command_response_type():
    """`ok`/`fail` exist so command authors cannot forget to stamp the response type.

    A command returning a TOOL-typed response is rendered by the wrong path in the session,
    which is the sort of mistake that looks like a display bug rather than a typing one.
    """
    assert Echo().ok("done").type is ResponseType.COMMAND
    assert Echo().fail("nope").success is False


@pytest.mark.asyncio
async def test_the_base_command_refuses_to_run_unimplemented():
    """A base `__call__` that returned would make a half-written command report success."""

    class Bare(Command):
        name: str = "bare"
        description: str = "no body"

    with pytest.raises(NotImplementedError):
        await Bare()([], None)
