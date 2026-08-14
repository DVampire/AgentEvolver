"""SessionQueryServer — read a past run's trace log back.

Read-only, and deliberately so. Trace owns writing; this owns the questions asked of
what was written. Nothing here opens a log for append, rewrites an event, or deletes a
file, so a query can never damage the record it is querying.

Two facts shape the whole module:

*One directory, several runs.* ``output/<owner>/sessions/<project>/log/trace`` holds one
JSONL file per run — the run the session was opened for, and one per sub-agent it
spawned. The unit of retrieval is the file, not the directory, because the sub-agent run
is very often the one that did the work being looked for.

*Everything is bounded.* Every method takes a cap and reports when it hit one. An
unbounded answer is not a nicer answer: a search that returns four hundred hits costs
the caller its remaining context and tells it less than twenty would have.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.session.query.types import (
    EventHit,
    EventWindow,
    SearchPage,
    SessionHit,
    SessionOutline,
    SessionRecord,
)
from agentevolver.trace.surface import SurfaceError, fold_surface
from agentevolver.utils import Singleton

#: Runs read while answering one question. Reached, the answer says so — a capped
#: search reported as a complete one is how an agent concludes work was never done.
MAX_RUNS_SCANNED = 400

#: Hits one search returns, by default and at most. The model does not get to raise
#: the ceiling: a limit the caller controls is a limit that gets set to 1000 the first
#: time a search looks thin, and the prompt pays for it.
DEFAULT_HITS = 20
MAX_HITS = 50

#: Characters of text around a match. Enough to recognise the hit and decide whether
#: to read the event; far too little to serve as the read itself, which is the point.
EXCERPT_CHARS = 240

#: One outline page, by default and at most, plus how much of an event fits on a line.
DEFAULT_OUTLINE = 40
MAX_OUTLINE = 200
LINE_CHARS = 160

#: Neighbours either side of an exactly-read event. Small on purpose: a window is for
#: seeing what surrounded a step, and anything larger is an outline read badly.
MAX_WINDOW = 10

#: How much of an event's ``input`` joins its searchable text. A tool call's arguments
#: are how a run is recognised ("the run that edited fibonacci.py"), but one argument
#: can be an entire file, and indexing that whole would make every long paste match
#: everything.
MAX_INPUT_TEXT = 4_000

#: Fields whose text is searched, besides ``input``. ``message`` carries the stringified
#: result of every ``*_call`` event, so tool and skill output is covered by it.
_TEXT_FIELDS = ("label", "message", "reasoning", "error", "action_name", "agent_name")

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class _SurfaceRow:
    """The two fields :func:`fold_surface` reads, lifted off a stored row.

    The fold is duck-typed (``getattr`` over ``Sequence[Any]``), and stored rows are
    plain dicts. Validating each row into a ``TraceEvent`` just to fold it would make
    the whole read fail on one row written by a newer version — a log that is mostly
    readable would report as unreadable.
    """

    seq_no: Optional[int]
    surface_op: Any
    source_event_seqs: Optional[List[int]]


def _row_text(row: Dict[str, Any]) -> str:
    """Everything about one event that is worth searching."""
    parts = [str(row.get(field) or "") for field in _TEXT_FIELDS]
    payload = row.get("input")
    if isinstance(payload, dict) and payload:
        parts.append(json.dumps(payload, ensure_ascii=False)[:MAX_INPUT_TEXT])
    return "\n".join(part for part in parts if part)


def _excerpt(text: str, terms: List[str]) -> str:
    """The text around the first matching term, collapsed to one line."""
    flat = _WHITESPACE.sub(" ", text).strip()
    lowered = flat.lower()
    at = min((lowered.find(t) for t in terms if lowered.find(t) >= 0), default=-1)
    if at < 0:
        return flat[:EXCERPT_CHARS]
    start = max(0, at - EXCERPT_CHARS // 3)
    window = flat[start:start + EXCERPT_CHARS]
    return ("…" if start else "") + window + ("…" if start + EXCERPT_CHARS < len(flat) else "")


def _clip(text: Any, limit: int = LINE_CHARS) -> str:
    flat = _WHITESPACE.sub(" ", str(text or "")).strip()
    return flat if len(flat) <= limit else flat[:limit] + "…"


class SessionQueryServer(metaclass=Singleton):
    """Find, read, and search the trace logs of runs that have already finished."""

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def logs(self, *, owner: Optional[str] = None) -> List[Tuple[str, str, Path]]:
        """Every run's log file as ``(owner, project, path)``, newest first.

        Newest first because retrieval is nearly always about recent work, and because
        every cap below cuts from the end: if something has to be dropped, it should be
        the run from three weeks ago rather than the one from this morning.
        """
        found: List[Tuple[str, str, Path]] = []
        output = path_manager.get(P.OUTPUT)
        if not output.is_dir():
            return found

        owners = [owner] if owner else [
            entry.name for entry in output.iterdir()
            # A leading dot marks machine-level runtime state (``.runtime``), which is
            # not an owner and holds no sessions.
            if entry.is_dir() and not entry.name.startswith(".")
        ]
        for candidate in sorted(owners):
            sessions = path_manager.get(P.SESSIONS, owner=candidate)
            if not sessions.is_dir():
                continue
            for project in sessions.iterdir():
                if not project.is_dir():
                    continue
                trace = path_manager.get(P.SESSION_TRACE, owner=candidate, session_id=project.name)
                if not trace.is_dir():
                    continue
                for log in trace.glob("*.jsonl"):
                    found.append((candidate, project.name, log))
        found.sort(key=lambda item: item[2].stat().st_mtime, reverse=True)
        return found

    def sessions(self, *, owner: Optional[str] = None,
                 limit: int = MAX_RUNS_SCANNED) -> List[SessionRecord]:
        """Summarise past runs, newest first."""
        return [self._summarise(o, project, log)
                for o, project, log in self.logs(owner=owner)[:limit]]

    def find(self, session_id: str, *, owner: Optional[str] = None) -> Optional[SessionRecord]:
        """The run with this id, or ``None``.

        Ids are unique in practice but not by construction, so the newest wins and the
        older ones are logged rather than silently preferred or silently merged.
        """
        matches = [(o, project, log) for o, project, log in self.logs(owner=owner)
                   if log.stem == session_id]
        if not matches:
            return None
        if len(matches) > 1:
            logger.warning(
                f"| ⚠️ {len(matches)} logs claim session {session_id!r}; reading the newest "
                f"({matches[0][2]})"
            )
        return self._summarise(*matches[0])

    def related(self, session_id: str, *, owner: Optional[str] = None) -> List[SessionRecord]:
        """The other runs filed under the same session directory.

        This is the lineage that actually holds here. Trace writes a sub-agent's run
        into its parent's trace directory, so co-location is a fact of the record;
        ``parent_session_id`` in an ``agent_start`` names the parent *agent runtime*,
        which is not a trace session id and does not resolve to one.
        """
        record = self.find(session_id, owner=owner)
        if record is None:
            return []
        return [self._summarise(o, project, log)
                for o, project, log in self.logs(owner=record.owner)
                if project == record.project and log.stem != session_id]

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def rows(self, session_id: str, *, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        """One run's events, as written, in write order.

        Dicts rather than ``TraceEvent`` instances: this is the durable record, and a
        reader that validated it would refuse a whole log over one field a later
        version added. Callers that need the schema import it from ``trace``.
        """
        record = self.find(session_id, owner=owner)
        return self._rows(Path(record.path)) if record else []

    def outline(self, session_id: str, *, owner: Optional[str] = None,
                start: int = 0, limit: int = DEFAULT_OUTLINE,
                surface_only: bool = True) -> Optional[SessionOutline]:
        """One page of a run, one line per event.

        ``surface_only`` reads the history as it stood at the end — a compaction
        summary in place of what it summarised. That is what the run's own agent saw,
        and it is the right default for "what happened here". Turn it off to read the
        raw append order, which is what actually happened, summaries and originals
        both.
        """
        record = self.find(session_id, owner=owner)
        if record is None:
            return None
        rows = self._rows(Path(record.path))
        limit = max(1, min(int(limit), MAX_OUTLINE))

        error = ""
        if surface_only:
            try:
                nodes = fold_surface([self._surface_row(row) for row in rows])["nodes"]
                by_seq = {row.get("seq_no"): row for row in rows}
                selected = [by_seq[seq] for seq in nodes if seq in by_seq]
            except SurfaceError as failure:
                # Fall back to write order rather than returning nothing. A log whose
                # replacements no longer fold is still a readable log; refusing it would
                # hide the very session someone is investigating *because* it is odd.
                error = str(failure)
                selected = rows
        else:
            selected = rows

        page = selected[start:start + limit]
        return SessionOutline(
            record=record,
            entries=[self._line(record, row) for row in page],
            total=len(selected),
            start=start,
            surface_only=surface_only and not error,
            surface_error=error,
        )

    def event_window(self, session_id: str, seq_no: int, *, owner: Optional[str] = None,
                     before: int = 0, after: int = 0) -> Optional[EventWindow]:
        """One event whole, its neighbours, and how it is linked to other events."""
        record = self.find(session_id, owner=owner)
        if record is None:
            return None
        rows = self._rows(Path(record.path))
        index = next((i for i, row in enumerate(rows) if row.get("seq_no") == seq_no), None)
        if index is None:
            return None

        before = max(0, min(int(before), MAX_WINDOW))
        after = max(0, min(int(after), MAX_WINDOW))
        target = rows[index]

        shadowed: List[int] = []
        try:
            for replacement in fold_surface([self._surface_row(row) for row in rows])["replacements"]:
                if replacement["seq"] == seq_no:
                    shadowed = list(replacement["shadowed"])
        except SurfaceError:
            # The citation on the event itself still answers the question the fold
            # would have; a log that no longer folds must not lose its way back from a
            # summary to the originals.
            shadowed = list(target.get("source_event_seqs") or [])

        return EventWindow(
            session_id=session_id,
            owner=record.owner,
            target=target,
            before=rows[max(0, index - before):index],
            after=rows[index + 1:index + 1 + after],
            shadowed=shadowed,
            shadowed_by=next((row.get("seq_no") for row in rows
                              if seq_no in (row.get("source_event_seqs") or [])), None),
            derived_from=list(target.get("source_event_seqs") or []),
            paired_with=self._pair(rows, target),
        )

    # ------------------------------------------------------------------
    # Searching
    # ------------------------------------------------------------------

    def search_sessions(self, query: str, *, owner: Optional[str] = None,
                        agent_name: Optional[str] = None,
                        limit: int = DEFAULT_HITS) -> SearchPage:
        """Runs whose log carries every term of ``query``, anywhere in it.

        Anywhere in the run, not all in one event — a description like "fibonacci
        generator fifteenth term" is spread across the task, the tool calls, and the
        answer, and requiring one event to carry all of it finds nothing. The
        highest-scoring single event still comes back as ``best``, which is where to
        start reading.
        """
        terms = self._terms(query)
        limit = max(1, min(int(limit), MAX_HITS))
        hits: List[SessionHit] = []
        scanned = 0

        for source in self.logs(owner=owner)[:MAX_RUNS_SCANNED]:
            scanned += 1
            rows, skipped = self._read(source[2])
            if agent_name and not any(row.get("agent_name") == agent_name for row in rows):
                continue
            texts = [(row, _row_text(row)) for row in rows]
            if not terms or not all(any(t in text.lower() for _, text in texts) for t in terms):
                continue

            record = self._summarise(*source, rows=rows, skipped=skipped)
            scored = [(self._score(text, terms), row, text) for row, text in texts]
            matched = [item for item in scored if item[0] == len(terms)]
            top = max(scored, key=lambda item: item[0], default=None)
            hits.append(SessionHit(
                record=record,
                matches=len(matched),
                best=self._hit(record, top[1], top[2], terms, top[0]) if top else None,
            ))
            if len(hits) >= limit:
                break

        return SearchPage(sessions=hits, scanned=scanned,
                          truncated=len(hits) >= limit or scanned >= MAX_RUNS_SCANNED)

    def search_events(self, query: str, *, session_id: Optional[str] = None,
                      owner: Optional[str] = None, event_type: Optional[str] = None,
                      action_name: Optional[str] = None,
                      limit: int = DEFAULT_HITS) -> SearchPage:
        """Events carrying every term of ``query``, in one run or across all of them.

        Every term in the *same* event here, unlike :meth:`search_sessions`. The
        answer is a coordinate to read from, so a hit that is only half the query
        would send the caller to the wrong event.
        """
        terms = self._terms(query)
        limit = max(1, min(int(limit), MAX_HITS))
        sources = self.logs(owner=owner)
        if session_id:
            sources = [item for item in sources if item[2].stem == session_id][:1]

        hits: List[EventHit] = []
        scanned = 0
        for source in sources[:MAX_RUNS_SCANNED]:
            scanned += 1
            rows, skipped = self._read(source[2])
            record = self._summarise(*source, rows=rows, skipped=skipped)
            for row in rows:
                if event_type and row.get("event_type") != event_type:
                    continue
                if action_name and row.get("action_name") != action_name:
                    continue
                text = _row_text(row)
                if not terms or self._score(text, terms) < len(terms):
                    continue
                hits.append(self._hit(record, row, text, terms, len(terms)))
                if len(hits) >= limit:
                    return SearchPage(events=hits, scanned=scanned, truncated=True)

        return SearchPage(events=hits, scanned=scanned, truncated=scanned >= MAX_RUNS_SCANNED)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _terms(query: str) -> List[str]:
        """Query words, lowercased and de-duplicated, order preserved."""
        seen: Dict[str, None] = {}
        for word in str(query or "").lower().split():
            seen.setdefault(word, None)
        return list(seen)

    @staticmethod
    def _score(text: str, terms: Iterable[str]) -> int:
        lowered = text.lower()
        return sum(1 for term in terms if term in lowered)

    @staticmethod
    def _surface_row(row: Dict[str, Any]) -> _SurfaceRow:
        return _SurfaceRow(seq_no=row.get("seq_no"), surface_op=row.get("surface_op"),
                           source_event_seqs=row.get("source_event_seqs"))

    @staticmethod
    def _read(path: Path) -> Tuple[List[Dict[str, Any]], int]:
        """Parse one JSONL log into rows, plus the count of lines that were not rows.

        A half-written last line is normal: the writer appends and flushes per event,
        so a log read while its run is still going can end mid-record. Refusing the
        file over that would make a live session unreadable, which is exactly when it
        is most interesting — so bad lines are counted and carried, never silently
        dropped and never fatal.
        """
        rows: List[Dict[str, Any]] = []
        skipped = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
                    else:
                        skipped += 1
        except OSError as error:
            logger.warning(f"| ⚠️ Could not read session log {path}: {error}")
        return rows, skipped

    def _rows(self, path: Path) -> List[Dict[str, Any]]:
        return self._read(path)[0]

    def _summarise(self, owner: str, project: str, path: Path,
                   rows: Optional[List[Dict[str, Any]]] = None,
                   skipped: int = 0) -> SessionRecord:
        """Build one run's summary from its log."""
        if rows is None:
            rows, skipped = self._read(path)
        record = SessionRecord(session_id=path.stem, owner=owner, project=project,
                               path=str(path), event_count=len(rows),
                               unreadable_lines=skipped)
        for row in rows:
            stamp = str(row.get("timestamp") or "")
            if stamp:
                record.started_at = record.started_at or stamp
                record.ended_at = stamp
            agent = str(row.get("agent_name") or "")
            if agent and agent not in record.agent_names:
                record.agent_names.append(agent)
            task_id = str(row.get("task_id") or "")
            if task_id and task_id not in record.task_ids:
                record.task_ids.append(task_id)
            if row.get("event_type") == "agent_start" and not record.task:
                payload = row.get("input")
                record.task = _clip(payload.get("task") if isinstance(payload, dict) else "", 400)
            if row.get("event_type") == "agent_end":
                # Last end wins: a run whose agent finished more than once is reported
                # by how it actually ended, not by its first attempt.
                record.outcome = _clip(row.get("message"), 400)
                record.success = row.get("success")
        return record

    @staticmethod
    def _hit(record: SessionRecord, row: Dict[str, Any], text: str,
             terms: List[str], score: int) -> EventHit:
        return EventHit(
            session_id=record.session_id,
            owner=record.owner,
            seq_no=int(row.get("seq_no") or 0),
            event_type=str(row.get("event_type") or ""),
            agent_name=str(row.get("agent_name") or ""),
            action_name=str(row.get("action_name") or ""),
            timestamp=str(row.get("timestamp") or ""),
            excerpt=_excerpt(text, terms),
            terms_matched=score,
        )

    @staticmethod
    def _line(record: SessionRecord, row: Dict[str, Any]) -> EventHit:
        """One outline entry. An ``EventHit`` with nothing matched — same coordinates."""
        payload = row.get("input")
        body = row.get("message") or row.get("reasoning")
        if not body and isinstance(payload, dict):
            body = payload.get("task") or json.dumps(payload, ensure_ascii=False)
        return EventHit(
            session_id=record.session_id,
            owner=record.owner,
            seq_no=int(row.get("seq_no") or 0),
            event_type=str(row.get("event_type") or ""),
            agent_name=str(row.get("agent_name") or ""),
            action_name=str(row.get("action_name") or ""),
            timestamp=str(row.get("timestamp") or ""),
            excerpt=_clip(body),
            terms_matched=0,
        )

    @staticmethod
    def _pair(rows: List[Dict[str, Any]], target: Dict[str, Any]) -> Optional[int]:
        """The ``*_start`` for a result, or the result for a ``*_start``.

        Paired on ``call_id`` when the log has one, and on ``(step, index)`` when it
        does not — logs written before ``call_id`` existed still pair rather than
        losing the link between a call and what it returned.
        """
        kind = str(target.get("event_type") or "")
        if not kind.endswith(("_start", "_call")) or kind.startswith("agent"):
            return None
        wanted = kind.replace("_start", "_call") if kind.endswith("_start") else kind.replace("_call", "_start")
        call_id = (target.get("metadata") or {}).get("call_id")
        for row in rows:
            if row.get("event_type") != wanted:
                continue
            if call_id:
                if (row.get("metadata") or {}).get("call_id") == call_id:
                    return row.get("seq_no")
                continue
            if (row.get("step_number"), row.get("action_index")) == \
                    (target.get("step_number"), target.get("action_index")):
                return row.get("seq_no")
        return None


session_query = SessionQueryServer()

__all__ = [
    "DEFAULT_HITS",
    "DEFAULT_OUTLINE",
    "MAX_HITS",
    "MAX_OUTLINE",
    "MAX_RUNS_SCANNED",
    "MAX_WINDOW",
    "SessionQueryServer",
    "session_query",
]
