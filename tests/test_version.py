"""Version manager: how a component's version line is advanced and compared.

Every evolving component (tool, agent, skill, prompt…) gets its next version
from here, so the arithmetic has to hold for the malformed inputs a generated
component can carry — a two-part version, a non-numeric one, an unknown name —
without throwing away the caller's history.
"""

import pytest

from agentevolver.version.server import VersionManagerServer
from agentevolver.version.types import ComponentVersionHistory, VersionStatus

@pytest.fixture
def versions():
    return VersionManagerServer()


# ----------------------------------------------------------------- registering
@pytest.mark.asyncio
async def test_a_registered_component_reports_its_version(versions):
    await versions.register_version("tool", "bash", "1.0.0")
    assert await versions.get_current_version("tool", "bash") == "1.0.0"


@pytest.mark.asyncio
async def test_registering_again_moves_the_current_version_forward(versions):
    await versions.register_version("tool", "bash", "1.0.0")
    await versions.register_version("tool", "bash", "1.1.0")
    history = await versions.get_version_history("tool", "bash")
    assert history.current_version == "1.1.0"
    assert sorted(history.list_versions()) == ["1.0.0", "1.1.0"]


@pytest.mark.asyncio
async def test_re_registering_the_same_version_updates_rather_than_duplicates(versions):
    await versions.register_version("tool", "bash", "1.0.0", description="first")
    await versions.register_version("tool", "bash", "1.0.0", description="revised")
    history = await versions.get_version_history("tool", "bash")
    assert history.list_versions() == ["1.0.0"]
    assert history.versions["1.0.0"].description == "revised"


@pytest.mark.asyncio
async def test_metadata_accumulates_across_re_registration(versions):
    await versions.register_version("tool", "bash", "1.0.0", metadata={"a": 1})
    await versions.register_version("tool", "bash", "1.0.0", metadata={"b": 2})
    info = (await versions.get_version_history("tool", "bash")).versions["1.0.0"]
    assert info.metadata == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_an_unknown_component_type_is_rejected_loudly(versions):
    """A typo'd type must not silently create a category nothing reads."""
    with pytest.raises(ValueError, match="Unknown component type"):
        await versions.register_version("nonsense", "x", "1.0.0")


@pytest.mark.asyncio
async def test_components_of_different_types_do_not_collide(versions):
    await versions.register_version("tool", "shared", "1.0.0")
    await versions.register_version("agent", "shared", "2.0.0")
    assert await versions.get_current_version("tool", "shared") == "1.0.0"
    assert await versions.get_current_version("agent", "shared") == "2.0.0"


@pytest.mark.asyncio
async def test_an_unregistered_component_reads_as_none(versions):
    assert await versions.get_current_version("tool", "never") is None
    assert await versions.get_version_history("tool", "never") is None
    assert await versions.get_version_history("nonsense", "never") is None


# -------------------------------------------------------------- next version
@pytest.mark.asyncio
async def test_a_new_component_starts_at_one_zero_zero(versions):
    assert await versions.generate_next_version("tool", "brand-new") == "1.0.0"


@pytest.mark.parametrize(
    "bump, expected",
    [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")],
)
@pytest.mark.asyncio
async def test_each_bump_resets_the_levels_below_it(versions, bump, expected):
    await versions.register_version("tool", "bash", "1.2.3")
    assert await versions.generate_next_version("tool", "bash", bump) == expected


@pytest.mark.asyncio
async def test_the_default_bump_is_a_patch(versions):
    await versions.register_version("tool", "bash", "1.2.3")
    assert await versions.generate_next_version("tool", "bash") == "1.2.4"


@pytest.mark.parametrize(
    "current, expected",
    [("1.2", "1.2.1"), ("3", "3.0.1")],
)
@pytest.mark.asyncio
async def test_a_short_version_is_padded_rather_than_rejected(versions, current, expected):
    await versions.register_version("tool", "bash", current)
    assert await versions.generate_next_version("tool", "bash") == expected


@pytest.mark.asyncio
async def test_an_unparseable_version_restarts_the_line(versions):
    """A generated component can carry anything; the line must still advance."""
    await versions.register_version("tool", "bash", "not-a-version")
    assert await versions.generate_next_version("tool", "bash") == "1.0.0"


# ------------------------------------------------------------- get_version
@pytest.mark.asyncio
async def test_an_explicit_version_wins_over_generation(versions):
    await versions.register_version("tool", "bash", "1.0.0")
    assert await versions.get_version("tool", "bash", "9.9.9") == "9.9.9"


@pytest.mark.asyncio
async def test_a_first_time_component_is_handed_one_zero_zero(versions):
    assert await versions.get_version("tool", "fresh") == "1.0.0"


@pytest.mark.asyncio
async def test_an_existing_component_is_handed_the_next_patch(versions):
    await versions.register_version("tool", "bash", "1.0.0")
    assert await versions.get_version("tool", "bash") == "1.0.1"


# ---------------------------------------------------------------- status
@pytest.mark.asyncio
async def test_the_current_version_cannot_be_deprecated(versions):
    """Deprecating what everything resolves to would strand every caller."""
    await versions.register_version("tool", "bash", "1.0.0")
    with pytest.raises(ValueError, match="Cannot deprecate current version"):
        await versions.deprecate_version("tool", "bash", "1.0.0")


@pytest.mark.asyncio
async def test_an_older_version_can_be_deprecated(versions):
    await versions.register_version("tool", "bash", "1.0.0")
    await versions.register_version("tool", "bash", "1.1.0")
    await versions.deprecate_version("tool", "bash", "1.0.0")
    history = await versions.get_version_history("tool", "bash")
    assert history.versions["1.0.0"].status is VersionStatus.DEPRECATED
    assert history.versions["1.1.0"].status is VersionStatus.ACTIVE


@pytest.mark.asyncio
async def test_the_current_version_may_be_archived(versions):
    """Archiving is a record-keeping act, unlike deprecation."""
    await versions.register_version("tool", "bash", "1.0.0")
    await versions.archive_version("tool", "bash", "1.0.0")
    history = await versions.get_version_history("tool", "bash")
    assert history.versions["1.0.0"].status is VersionStatus.ARCHIVED


@pytest.mark.asyncio
async def test_status_changes_on_an_unknown_component_are_rejected(versions):
    for change in (versions.deprecate_version, versions.archive_version):
        with pytest.raises(ValueError, match="not found"):
            await change("tool", "ghost", "1.0.0")


@pytest.mark.asyncio
async def test_status_changes_on_an_unknown_version_are_rejected(versions):
    await versions.register_version("tool", "bash", "1.0.0")
    with pytest.raises(ValueError, match="Version 2.0.0 not found"):
        await versions.archive_version("tool", "bash", "2.0.0")


# ------------------------------------------------------------------ listing
@pytest.mark.asyncio
async def test_listing_covers_every_known_type_even_when_empty(versions):
    await versions.register_version("tool", "bash", "1.0.0")
    listed = await versions.list()
    assert listed["tool"] == {"bash": ["1.0.0"]}
    assert listed["agent"] == {}
    assert "workflow" in listed  # every declared category is reported


# ------------------------------------------------------------- comparison
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
    assert VersionManagerServer.compare_versions(left, right) == expected


def test_a_non_numeric_version_falls_back_to_string_order():
    assert VersionManagerServer.compare_versions("beta", "alpha") == 1
    assert VersionManagerServer.compare_versions("beta", "beta") == 0


# ------------------------------------------------------- history in isolation
def test_adding_a_version_makes_it_current():
    history = ComponentVersionHistory(name="x", component_type="tool", current_version="1.0.0")
    history.add_version("1.0.0")
    history.add_version("2.0.0")
    assert history.current_version == "2.0.0"


def test_an_absent_version_cannot_be_deprecated():
    history = ComponentVersionHistory(name="x", component_type="tool", current_version="1.0.0")
    with pytest.raises(ValueError, match="not found"):
        history.deprecate_version("9.9.9")
