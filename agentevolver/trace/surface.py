"""The surface: which of a session's events still stand for its history.

The trace log is append-only, so it grows and never shrinks. What the agent's history
*says*, though, does shrink — compaction folds a run of records into one summary. Those
two facts used to be irreconcilable: memory dropped the folded records from its own
window and the log kept them, with nothing anywhere recording that one now stood for the
others.

A surface reconciles them without deleting anything. Each event declares how it joins:
``append`` puts it at the tail, ``replace`` puts it in place of a range. Folding the
declarations gives the current ordered history; reading the log in write order still
gives everything that actually happened. One log, two readings.

This module is the fold and its rules. It does not decide *when* to replace anything —
that belongs to whoever is compacting.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

APPEND = "append"


class SurfaceError(ValueError):
    """A replacement that cannot be applied to the surface it claims to replace."""


def _replacement(op: Any) -> Optional[Tuple[int, int]]:
    """The inclusive range an op replaces, or ``None`` if it is a plain append."""
    if not isinstance(op, dict) or op.get("op") != "replace":
        return None
    try:
        return int(op["start"]), int(op["end"])
    except (KeyError, TypeError, ValueError) as error:
        raise SurfaceError(f"malformed replace op {op!r}: {error}") from None


def fold_surface(events: Sequence[Any]) -> Dict[str, Any]:
    """Replay the surface declarations in ``events``.

    Args:
        events: The session's events in write order. Anything without a ``surface_op``
            is log-only — a bookkeeping record that never stood for history — and is
            skipped rather than treated as an implicit append. Being explicit here is
            what keeps "this event is part of the conversation" from being a guess.

    Returns:
        ``{"nodes": [...], "replacements": [...]}`` — the surface seqs in history order,
        and one record per replacement with the seqs it actually shadowed.

    Raises:
        SurfaceError: A replacement named a range that is not on the surface, ran
            backwards, or failed to cite what it shadowed. Each of these means the log
            can no longer be read the way its writer intended, and guessing at the
            intent would silently produce a history nobody wrote.
    """
    nodes: List[int] = []
    replacements: List[Dict[str, Any]] = []

    for event in events:
        op = getattr(event, "surface_op", None)
        if op is None:
            continue
        seq = getattr(event, "seq_no", None)
        if seq is None:
            raise SurfaceError("a surface event has no seq_no; it cannot be placed")

        span = _replacement(op)
        if span is None:
            if op != APPEND:
                raise SurfaceError(f"unknown surface op {op!r} on event {seq}")
            nodes.append(seq)
            continue

        start, end = span
        try:
            i, j = nodes.index(start), nodes.index(end)
        except ValueError:
            raise SurfaceError(
                f"event {seq} replaces [{start}, {end}], but that range is not on the "
                f"current surface — it was already replaced, or never joined it"
            ) from None
        if i > j:
            raise SurfaceError(f"event {seq} replaces [{start}, {end}], which runs backwards")

        shadowed = nodes[i : j + 1]
        cited = getattr(event, "source_event_seqs", None) or []
        missing = [s for s in shadowed if s not in cited]
        if missing:
            # Without the citation the originals behind a summary are unreachable: the
            # log still holds them, but nothing says which ones this summary stands for.
            raise SurfaceError(
                f"event {seq} replaces {shadowed} but does not cite {missing}"
            )

        nodes[i : j + 1] = [seq]
        replacements.append({"seq": seq, "start": start, "end": end, "shadowed": shadowed})

    return {"nodes": nodes, "replacements": replacements}


def surface_events(events: Sequence[Any]) -> List[Any]:
    """The events on the current surface, in history order."""
    by_seq = {getattr(e, "seq_no", None): e for e in events}
    return [by_seq[seq] for seq in fold_surface(events)["nodes"] if seq in by_seq]


def transcript_events(events: Sequence[Any]) -> List[Any]:
    """Every event that joined the surface by *appending*, in history order.

    This is what a human transcript is built from, and it is **not** what
    :func:`surface_events` returns.

    The surface is the model's history, and a replacement deliberately shadows the range it
    replaced — that is the whole mechanism behind compaction. Rendering a conversation from
    it therefore deletes messages the user has already read: the summary arrives and the ten
    turns it stands for disappear from the screen. The events that appended never get
    shadowed, so they remain the durable record of what was actually said; replacement
    copies stay model-only.

    The distinction is easy to miss because both readings are correct for their own
    consumer, and the wrong one fails silently — a transcript that renders perfectly right
    up until the first compaction.
    """
    # No fold is needed: an append is a transcript event whether or not a later
    # replacement shadowed it. Filtering by the folded surface is the tempting mistake —
    # it would reintroduce exactly the deletion this function exists to avoid.
    return [event for event in events if getattr(event, "surface_op", None) == APPEND]


def shadowed_by(events: Sequence[Any], seq: int) -> List[int]:
    """The seqs one replacement shadowed — the way back from a summary to its originals."""
    for replacement in fold_surface(events)["replacements"]:
        if replacement["seq"] == seq:
            return list(replacement["shadowed"])
    return []


def replace_op(start: int, end: int) -> Dict[str, Any]:
    """Build a replace op for the inclusive surface range ``start``..``end``."""
    return {"op": "replace", "start": int(start), "end": int(end)}


def check_citations(events: Iterable[Any]) -> List[str]:
    """Report surface events whose declarations do not hold up, without raising.

    The fold refuses a log it cannot read; this answers the softer question an audit
    asks — *is this log still foldable* — and returns the problems instead of stopping
    at the first one.
    """
    try:
        fold_surface(list(events))
    except SurfaceError as error:
        return [str(error)]
    return []


__all__ = [
    "APPEND",
    "SurfaceError",
    "check_citations",
    "fold_surface",
    "replace_op",
    "shadowed_by",
    "surface_events",
    "transcript_events",
]
