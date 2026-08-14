"""Version manager: how a component's version line is advanced and compared.

Every evolving component (tool, agent, skill, prompt…) gets its next version from here, so
the arithmetic has to hold for the malformed inputs a generated component can carry — a
two-part version, a non-numeric one, an unknown name — without throwing away the caller's
history. A component whose version stops advancing overwrites its own previous record on
every evolution, and the history is what a rollback reads.

The comparison half is used to decide which of two versions is newer, and the trap there
is that the obvious implementation is string comparison, under which "1.10.0" precedes
"1.9.0". The status half exists so a version can be retired without stranding the callers
resolving to it.
"""

import pytest

from agentevolver.version.server import VersionManagerServer
from agentevolver.version.types import ComponentVersionHistory, VersionStatus


@pytest.fixture
def versions():
    return VersionManagerServer()


# --------------------------------------------------------------------------- #
# Recording that a version exists
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_registered_component_reports_its_version(versions):
    await versions.register_version("tool", "bash", "1.0.0")
    assert await versions.get_current_version("tool", "bash") == "1.0.0"


@pytest.mark.asyncio
async def test_registering_again_moves_the_current_version_forward(versions):
    """"Current" is the version most recently registered, and the older one is kept.

    Both halves are load-bearing: resolution has to follow the new version, and the
    previous one has to remain in the history, because a rollback can only return to a
    version the manager still knows about.
    """
    await versions.register_version("tool", "bash", "1.0.0")
    await versions.register_version("tool", "bash", "1.1.0")
    history = await versions.get_version_history("tool", "bash")
    assert history.current_version == "1.1.0"
    assert sorted(history.list_versions()) == ["1.0.0", "1.1.0"]


@pytest.mark.asyncio
async def test_re_registering_the_same_version_updates_rather_than_duplicates(versions):
    """The same version gets registered twice whenever a component is reloaded.

    Appending a second record each time would make the history grow without any new
    versions existing, and would leave two records disagreeing about the description. The
    later description wins because it comes from the more recent load.
    """
    await versions.register_version("tool", "bash", "1.0.0", description="first")
    await versions.register_version("tool", "bash", "1.0.0", description="revised")
    history = await versions.get_version_history("tool", "bash")
    assert history.list_versions() == ["1.0.0"]
    assert history.versions["1.0.0"].description == "revised"


@pytest.mark.asyncio
async def test_metadata_accumulates_across_re_registration(versions):
    """Different callers contribute different keys about the same version.

    Registration happens from more than one place — discovery, evolution, extension
    install — and each knows something the others do not. Replacing the dict instead of
    merging it means whichever caller runs last silently erases the rest.
    """
    await versions.register_version("tool", "bash", "1.0.0", metadata={"a": 1})
    await versions.register_version("tool", "bash", "1.0.0", metadata={"b": 2})
    info = (await versions.get_version_history("tool", "bash")).versions["1.0.0"]
    assert info.metadata == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_an_unknown_component_type_is_rejected_loudly(versions):
    """A typo'd type must not silently create a category nothing reads.

    Accepting it would store the history under a key no lookup ever uses: registration
    keeps succeeding, and the component reads as having no version at all.
    """
    with pytest.raises(ValueError, match="Unknown component type"):
        await versions.register_version("nonsense", "x", "1.0.0")


@pytest.mark.asyncio
async def test_components_of_different_types_do_not_collide(versions):
    """A tool and an agent may share a name; they do not share a version line.

    Names are only unique within a type, so a flat name-keyed store would have the agent's
    registration overwrite the tool's and hand one of them the other's rollback target.
    """
    await versions.register_version("tool", "shared", "1.0.0")
    await versions.register_version("agent", "shared", "2.0.0")
    assert await versions.get_current_version("tool", "shared") == "1.0.0"
    assert await versions.get_current_version("agent", "shared") == "2.0.0"


@pytest.mark.asyncio
async def test_an_unregistered_component_reads_as_none(versions):
    """Asking about something unknown is routine, so it answers rather than raises.

    The third call uses an unknown *type* as well: that path returns None too, so callers
    need one check rather than a check and an exception handler.
    """
    assert await versions.get_current_version("tool", "never") is None
    assert await versions.get_version_history("tool", "never") is None
    assert await versions.get_version_history("nonsense", "never") is None


# --------------------------------------------------------------------------- #
# Choosing the next number
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_new_component_starts_at_one_zero_zero(versions):
    assert await versions.generate_next_version("tool", "brand-new") == "1.0.0"


@pytest.mark.parametrize(
    "bump, expected",
    [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")],
)
@pytest.mark.asyncio
async def test_each_bump_resets_the_levels_below_it(versions, bump, expected):
    """A minor bump from 1.2.3 is 1.3.0, not 1.3.3.

    Carrying the lower levels through is the natural result of incrementing one field and
    leaving the others alone, and it produces version lines that go backwards: 1.3.3
    followed later by a patch of 1.3.4 loses the fact that the minor bump happened.
    """
    await versions.register_version("tool", "bash", "1.2.3")
    assert await versions.generate_next_version("tool", "bash", bump) == expected


@pytest.mark.asyncio
async def test_the_default_bump_is_a_patch(versions):
    """Most evolutions are small, and the smallest bump is the one that claims least."""
    await versions.register_version("tool", "bash", "1.2.3")
    assert await versions.generate_next_version("tool", "bash") == "1.2.4"


@pytest.mark.parametrize(
    "current, expected",
    [("1.2", "1.2.1"), ("3", "3.0.1")],
)
@pytest.mark.asyncio
async def test_a_short_version_is_padded_rather_than_rejected(versions, current, expected):
    """Hand-written components declare "1.2" or "3" and mean the same thing as "1.2.0".

    Refusing them would block the component from evolving at all; the missing fields are
    read as zero and the line continues from there.
    """
    await versions.register_version("tool", "bash", current)
    assert await versions.generate_next_version("tool", "bash") == expected


@pytest.mark.asyncio
async def test_an_unparseable_version_restarts_the_line(versions):
    """A generated component can carry anything; the line must still advance.

    Raising here would stop the evolution loop over a string a model invented. Restarting
    at 1.0.0 loses the ordering against the old value, which is the lesser cost: nothing
    could be ordered against "not-a-version" anyway.
    """
    await versions.register_version("tool", "bash", "not-a-version")
    assert await versions.generate_next_version("tool", "bash") == "1.0.0"


# --------------------------------------------------------------------------- #
# The version a caller is handed
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_explicit_version_wins_over_generation(versions):
    """Someone who names a version has a reason; generation is only the fallback."""
    await versions.register_version("tool", "bash", "1.0.0")
    assert await versions.get_version("tool", "bash", "9.9.9") == "9.9.9"


@pytest.mark.asyncio
async def test_a_first_time_component_is_handed_one_zero_zero(versions):
    assert await versions.get_version("tool", "fresh") == "1.0.0"


@pytest.mark.asyncio
async def test_an_existing_component_is_handed_the_next_patch(versions):
    """Never the version already in use: reusing it would overwrite the record of it.

    That is the difference between a history that can be rolled back through and one where
    two different implementations share a number.
    """
    await versions.register_version("tool", "bash", "1.0.0")
    assert await versions.get_version("tool", "bash") == "1.0.1"


# --------------------------------------------------------------------------- #
# Retiring a version
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_current_version_cannot_be_deprecated(versions):
    """Deprecating what everything resolves to would strand every caller.

    Deprecation says "use something newer", and there is nothing newer when the version is
    the current one — the refusal is what forces the newer version to be registered first.
    """
    await versions.register_version("tool", "bash", "1.0.0")
    with pytest.raises(ValueError, match="Cannot deprecate current version"):
        await versions.deprecate_version("tool", "bash", "1.0.0")


@pytest.mark.asyncio
async def test_an_older_version_can_be_deprecated(versions):
    """Deprecating one version must not touch its neighbours.

    The assertion on 1.1.0 is the half that would go unnoticed: a status change applied to
    the whole history retires the current version too, and the component stops resolving.
    """
    await versions.register_version("tool", "bash", "1.0.0")
    await versions.register_version("tool", "bash", "1.1.0")
    await versions.deprecate_version("tool", "bash", "1.0.0")
    history = await versions.get_version_history("tool", "bash")
    assert history.versions["1.0.0"].status is VersionStatus.DEPRECATED
    assert history.versions["1.1.0"].status is VersionStatus.ACTIVE


@pytest.mark.asyncio
async def test_the_current_version_may_be_archived(versions):
    """Archiving is a record-keeping act, unlike deprecation.

    It marks where a version was stored, not that it should stop being used, so the guard
    that protects the current version from deprecation must not also apply here — a
    component would otherwise be unable to archive the version it is running.
    """
    await versions.register_version("tool", "bash", "1.0.0")
    await versions.archive_version("tool", "bash", "1.0.0")
    history = await versions.get_version_history("tool", "bash")
    assert history.versions["1.0.0"].status is VersionStatus.ARCHIVED


@pytest.mark.asyncio
async def test_status_changes_on_an_unknown_component_are_rejected(versions):
    """Both status changes raise, rather than one of them quietly doing nothing.

    A no-op here reports that a version was retired when no such component exists — the
    caller's mistake (wrong type, wrong name) is confirmed as success.
    """
    for change in (versions.deprecate_version, versions.archive_version):
        with pytest.raises(ValueError, match="not found"):
            await change("tool", "ghost", "1.0.0")


@pytest.mark.asyncio
async def test_status_changes_on_an_unknown_version_are_rejected(versions):
    """The component exists and the version does not — the easier case to miss."""
    await versions.register_version("tool", "bash", "1.0.0")
    with pytest.raises(ValueError, match="Version 2.0.0 not found"):
        await versions.archive_version("tool", "bash", "2.0.0")


# --------------------------------------------------------------------------- #
# Reporting the whole registry
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_listing_covers_every_known_type_even_when_empty(versions):
    """An absent category and an empty one mean different things to whoever renders this.

    Omitting types with no components makes the listing's shape depend on what happens to
    be registered, so a consumer iterating the result sees categories appear and disappear
    between calls instead of showing an empty section.
    """
    await versions.register_version("tool", "bash", "1.0.0")
    listed = await versions.list()
    assert listed["tool"] == {"bash": ["1.0.0"]}
    assert listed["agent"] == {}
    assert "workflow" in listed  # every declared category is reported


# --------------------------------------------------------------------------- #
# Which of two versions is newer
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "left, right, expected",
    [
        ("1.0.1", "1.0.0", 1),
        ("1.0.0", "1.0.1", -1),
        ("1.0.0", "1.0.0", 0),
        ("2.0.0", "1.9.9", 1),
        ("1.10.0", "1.9.0", 1),   # numeric, not lexicographic
        ("1.0", "1.0.0", 0),      # padded to equal length
        ("1.0.0.1", "1.0.0", 1),
    ],
)
def test_versions_compare_numerically(left, right, expected):
    """The 1.10.0 row is the one that fails under the obvious implementation.

    Compared as strings, "1.10.0" sorts before "1.9.0" because "1" precedes "9", so a
    tenth minor release reads as older than the ninth — and a rollback picks the wrong
    target at exactly the point a component has been evolved enough times to need one.
    The padding rows cover the other shape: "1.0" and "1.0.0" are the same version written
    two ways.
    """
    assert VersionManagerServer.compare_versions(left, right) == expected


def test_a_non_numeric_version_falls_back_to_string_order():
    """Nothing sensible can be computed from "beta", so it degrades instead of raising.

    The ordering is arbitrary but total and stable, which is all a caller sorting a list
    needs; raising would propagate a bad version string into an unrelated code path.
    """
    assert VersionManagerServer.compare_versions("beta", "alpha") == 1
    assert VersionManagerServer.compare_versions("beta", "beta") == 0


# --------------------------------------------------------------------------- #
# The history object on its own
# --------------------------------------------------------------------------- #
def test_adding_a_version_makes_it_current():
    """Current follows the most recent registration, not the highest number.

    That is deliberate — restoring an older version has to be able to make it current
    again — but it means the history cannot be read as a sorted line, and a caller wanting
    "the newest version" has to compare rather than take the last one added.
    """
    history = ComponentVersionHistory(name="x", component_type="tool", current_version="1.0.0")
    history.add_version("1.0.0")
    history.add_version("2.0.0")
    assert history.current_version == "2.0.0"


def test_an_absent_version_cannot_be_deprecated():
    """The check lives on the history object too, not only on the manager wrapping it."""
    history = ComponentVersionHistory(name="x", component_type="tool", current_version="1.0.0")
    with pytest.raises(ValueError, match="not found"):
        history.deprecate_version("9.9.9")
