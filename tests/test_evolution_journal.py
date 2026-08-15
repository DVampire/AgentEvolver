"""The evolution journal survives its own file format, and remembers what was reverted.

The journal is what stops the optimizer re-proposing a hypothesis that was already tried
and rolled back. That makes it a memory, and a memory is only as good as its round trip:
everything it knows is written as YAML inside an HTML comment, recovered by one regular
expression, and any drift between the writer and that expression loses history silently —
the optimizer simply starts repeating itself, and nothing reports an error.

So most of this file is round trips through real files. The rest covers the two readings
that are easy to get backwards. A malformed section must cost only itself, because a
half-written round is the normal shape of a journal from an interrupted evolution run and
losing the whole file over it would discard every round before it. And `fill_gating`
targets the latest *pending* round rather than the latest round, or a second evaluation
would overwrite the verdict of an earlier one that had already been decided.

Until the coverage lane was introduced, no test executed a line of this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentevolver.extension.journal import Journal, JournalRound


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    """A journal rooted in a throwaway tree rather than the shared extension root."""
    return Journal(base_dir=str(tmp_path))


# --------------------------------------------------------------------------- #
# The round trip
# --------------------------------------------------------------------------- #
def test_a_recorded_round_reads_back_with_every_field_intact(journal: Journal):
    """The whole memory depends on this and nothing else checks it.

    The machine-readable fields live in an HTML comment so the file stays readable to a
    human and writable by an agent. That is a good trade for everything except
    verification: a comment that no longer parses looks exactly like a file with no
    rounds in it.
    """
    journal.append_round(
        "tool", "search", hypothesis_id="h-1", lever="instruction",
        predicted_flip=["task-a", "task-b"], note="Widen the query before falling back.",
    )

    (recovered,) = journal.read("tool", "search")

    assert recovered.round == 1
    assert recovered.hypothesis_id == "h-1"
    assert recovered.lever == "instruction"
    assert recovered.predicted_flip == ["task-a", "task-b"]
    assert recovered.gating_outcome == "pending"
    # The prose is outside the comment block; the regex has to stop at the next round
    # header rather than swallowing it.
    assert recovered.note == "Widen the query before falling back."


def test_several_rounds_in_one_file_stay_separate_and_ordered(journal: Journal):
    """One file per component, one section per round — so the parser must split them.

    A greedy match here would fold every later round into the first one's prose, and the
    symptom would be a journal that always reports exactly one round no matter how many
    were recorded.
    """
    for index in range(1, 4):
        journal.append_round("agent", "planner", hypothesis_id=f"h-{index}", note=f"note {index}")

    rounds = journal.read("agent", "planner")

    assert [r.round for r in rounds] == [1, 2, 3]
    assert [r.hypothesis_id for r in rounds] == ["h-1", "h-2", "h-3"]
    assert [r.note for r in rounds] == ["note 1", "note 2", "note 3"]


def test_a_component_with_no_journal_yet_reads_as_empty_not_as_an_error(journal: Journal):
    """The first round of every component starts here, so this is the common case."""
    assert journal.read("tool", "never_evolved") == []
    assert journal.next_round_number("tool", "never_evolved") == 1


def test_round_numbers_continue_from_the_highest_already_recorded(journal: Journal):
    """Counting rounds from the file rather than from a caller-held counter is what
    keeps numbering correct across separate processes and interrupted runs."""
    journal.append_round("tool", "search", hypothesis_id="h-1")
    journal.append_round("tool", "search", hypothesis_id="h-2")

    assert journal.next_round_number("tool", "search") == 3


# --------------------------------------------------------------------------- #
# Damage
# --------------------------------------------------------------------------- #
def test_a_malformed_round_costs_only_itself(journal: Journal):
    """An interrupted evolution run leaves exactly this: a section that will not parse.

    Failing the whole read would discard every round before the damage — the history the
    optimizer needs most, since it is the part that has already been evaluated.
    """
    journal.append_round("tool", "search", hypothesis_id="h-1", note="first")
    journal.append_round("tool", "search", hypothesis_id="h-2", note="second")

    path = Path(journal._path("tool", "search"))
    # Break round 1's YAML block only, leaving its header and round 2 untouched.
    path.write_text(
        path.read_text(encoding="utf-8").replace("hypothesis_id: h-1", "hypothesis_id: [unclosed"),
        encoding="utf-8",
    )

    surviving = journal.read("tool", "search")

    assert [r.hypothesis_id for r in surviving] == ["h-2"]


def test_an_unrecognised_lever_is_recorded_rather_than_refused(journal: Journal):
    """The lever vocabulary is advice to the generator, not a schema to enforce.

    Rejecting an unknown value would drop the round entirely, losing the hypothesis; the
    warning is there so a typo is visible without the history paying for it.
    """
    recorded = journal.append_round("tool", "search", hypothesis_id="h-1", lever="not-a-lever")

    assert recorded.lever == "not-a-lever"
    assert journal.read("tool", "search")[0].lever == "not-a-lever"


# --------------------------------------------------------------------------- #
# Backfilling the verdict
# --------------------------------------------------------------------------- #
def test_gating_fills_the_latest_pending_round_and_leaves_decided_ones_alone(journal: Journal):
    """"Latest pending", not "latest" — the distinction the whole backfill turns on.

    Rounds are gated after the *next* evaluation, so at any moment the file can hold
    already-decided rounds followed by a pending one. Targeting the latest round instead
    would overwrite a verdict that had already been measured.
    """
    journal.append_round("tool", "search", hypothesis_id="h-1")
    journal.fill_gating("tool", "search", "accepted", attribution={"task-a": True})
    journal.append_round("tool", "search", hypothesis_id="h-2")

    filled = journal.fill_gating("tool", "search", "reverted", attribution={"task-a": False})

    assert filled is not None and filled.round == 2
    first, second = journal.read("tool", "search")
    assert (first.gating_outcome, first.gating_attribution) == ("accepted", {"task-a": True})
    assert (second.gating_outcome, second.gating_attribution) == ("reverted", {"task-a": False})


def test_a_named_round_can_be_gated_out_of_order(journal: Journal):
    """Evaluations can land late; the explicit round number is how a caller says which
    one it measured rather than trusting the file's current tail."""
    journal.append_round("tool", "search", hypothesis_id="h-1")
    journal.append_round("tool", "search", hypothesis_id="h-2")

    filled = journal.fill_gating("tool", "search", "noop", round_no=1)

    assert filled is not None and filled.round == 1
    assert journal.read("tool", "search")[1].gating_outcome == "pending"


@pytest.mark.parametrize("kwargs,why", [
    ({}, "nothing recorded yet"),
    ({"round_no": 99}, "a round number that does not exist"),
])
def test_gating_something_that_is_not_there_returns_nothing(journal: Journal, kwargs, why):
    """`None` rather than an exception: a late or duplicated evaluation is ordinary, and
    the caller's only reasonable response is to move on."""
    assert journal.fill_gating("tool", "unknown", "accepted", **kwargs) is None


def test_gating_a_file_whose_rounds_are_all_decided_returns_nothing(journal: Journal):
    """No pending round left is a real state, reached whenever an evaluation runs twice."""
    journal.append_round("tool", "search", hypothesis_id="h-1")
    journal.fill_gating("tool", "search", "accepted")

    assert journal.fill_gating("tool", "search", "reverted") is None


# --------------------------------------------------------------------------- #
# What the optimizer is told
# --------------------------------------------------------------------------- #
def test_only_reverted_hypotheses_are_named_as_forbidden(journal: Journal):
    """The journal's reason for existing, stated as narrowly as possible.

    An accepted hypothesis is not forbidden — it is the current state, and a later round
    may well refine it. Listing anything other than the reverted ones would stop the
    optimizer from building on its own successes.
    """
    for hypothesis, outcome in [("h-1", "accepted"), ("h-2", "reverted"), ("h-3", "noop")]:
        journal.append_round("tool", "search", hypothesis_id=hypothesis)
        journal.fill_gating("tool", "search", outcome)

    assert journal.reverted_hypotheses("tool", "search") == ["h-2"]


def test_the_optimizer_ribbon_contrasts_what_was_predicted_with_what_flipped(journal: Journal):
    """Prediction and outcome side by side is the whole signal.

    Either half alone is uninformative: a prediction with no result says nothing about
    whether the lever works, and a result with no prediction says nothing about whether
    the hypothesis was the reason.
    """
    journal.append_round("tool", "search", hypothesis_id="h-1", lever="action",
                         predicted_flip=["task-a", "task-b"])
    journal.fill_gating("tool", "search", "reverted",
                        attribution={"task-a": True, "task-b": False})

    ribbon = journal.render_context("tool", "search")

    assert "[action]" in ribbon
    assert "predicted=['task-a', 'task-b']" in ribbon
    # Only the ones that actually flipped, not the whole attribution map.
    assert "actually_flipped=['task-a']" in ribbon
    assert "DO NOT re-propose these reverted hypotheses: ['h-1']" in ribbon


def test_the_ribbon_for_an_untouched_component_says_so_plainly(journal: Journal):
    """This string goes into a prompt, so an empty one would read as a truncated section
    and invite the model to fill the gap."""
    assert journal.render_context("tool", "fresh") == "(no prior evolution rounds for tool:fresh)"


def test_the_ribbon_stays_silent_about_forbidden_hypotheses_when_there_are_none(journal: Journal):
    """A standing DO-NOT line with an empty list is noise in every prompt that carries
    it, and trains the reader to skip the line that matters when it is not empty."""
    journal.append_round("tool", "search", hypothesis_id="h-1")
    journal.fill_gating("tool", "search", "accepted")

    assert "DO NOT" not in journal.render_context("tool", "search")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_a_round_renders_as_a_header_a_comment_block_and_prose(journal: Journal):
    """The file is meant to be read and edited by hand, so its shape is part of the
    contract — an agent asked to amend a journal has to be able to find the fields."""
    rendered = JournalRound(round=7, hypothesis_id="h-7", lever="control", note="why").render()

    assert rendered.startswith("## Round 7\n<!--\n")
    assert "hypothesis_id: h-7" in rendered
    assert rendered.rstrip().endswith("why")
