"""Path manager: the single authority on where anything is written.

Every path the framework writes is declared in :data:`LAYOUT` and resolved through this
manager, so the whole disk contract is one table instead of joins scattered across the
gateway, the sandbox, the IDE and half a dozen others. Moving a directory becomes a
one-line edit to the table.

The manager answers three kinds of question, and they are kept apart below because they
fail differently:

**Roots** — where the tree hangs off. Three of them, each with its own environment
override, and none derived from the others. Static methods: they depend on the process's
environment, not on any state this object holds.

**The bound session** — which run's directories the session-scoped keys mean. A run's
roots used to be computed once and then *carried*: packed into ``ctx.extra`` as six
strings and handed down through every manager, agent, hook and tool. Both halves of that
went wrong. The values drifted from their names — ``ctx.extra["extension_root"]`` was the
session's writable staging tree while ``config.extension_root`` was the shared library, so
one name meant two opposite directories depending on which module you read it in, and an
agent told the wrong one wrote where promotion would refuse to look. And anything holding
the dict could rewrite it, which left the table advisory: a copy in flight was as
authoritative as the table itself. Bound instead, the table answers directly, wherever the
question is asked, and a caller deep inside a tool no longer has to have been handed a
path to know where it may write.

**Resolution** — a key plus its parameters to an absolute path. One template per key, one
place that formats it, and a missing parameter is an error rather than a directory
literally named ``{owner}``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from agentevolver.paths.types import LAYOUT, P

#: Placeholders in a layout template: ``{owner}``, ``{session_id}``, ``{module}``, …
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class PathManagerServer:
    """Resolve layout keys to absolute paths, and answer questions about the tree."""

    def __init__(self) -> None:
        self._layout: Dict[P, str] = dict(LAYOUT)
        #: Whose session this run belongs to. Filled into ``{owner}`` / ``{session_id}``
        #: for any caller that does not pass them. A single value because runs are
        #: serialized — the task queue has one worker, and the gateway rebinds the shared
        #: runtime per task — which is the assumption ``config.workspace_root`` has always
        #: made too.
        self._owner: Optional[str] = None
        self._session_id: Optional[str] = None
        #: Paths the layout genuinely cannot compute, keyed by the entry they stand in
        #: for. See :meth:`override`.
        self._overrides: Dict[P, Path] = {}
        #: Called after every rebind. A manager that keeps a directory derived from the
        #: session subscribes here, so a session change reaches it without the caller
        #: having to name it. See :meth:`on_rebind`.
        self._listeners: List[Callable[[], None]] = []

    # ================================================================== #
    # 1. Roots — where the tree hangs off
    # ================================================================== #
    @staticmethod
    def project_dir() -> Path:
        """The directory the whole tree hangs off — the current project, or an override.

        ``AGENTEVOLVER_HOME`` keeps its historical meaning: point everything somewhere
        else (a shared volume, a scratch disk, a test's temp directory).
        """
        override = os.environ.get("AGENTEVOLVER_HOME")
        return Path(override).expanduser().resolve() if override else Path.cwd().resolve()

    @staticmethod
    def extension_dir() -> Path:
        """Where shared, durable components live — the library promotion writes into.

        ``AGENTEVOLVER_EXTENSION_ROOT`` moves just this root (a component library on
        another volume, or a temp dir in a test) while ``AGENTEVOLVER_HOME`` moves the
        whole tree and takes this root with it. Resolved here rather than in each caller:
        skill, connector, canvas and the extension manager each used to answer this
        question themselves, and under ``AGENTEVOLVER_HOME`` they answered it differently.
        """
        override = os.environ.get("AGENTEVOLVER_EXTENSION_ROOT")
        if override:
            path = Path(override).expanduser()
            if not path.is_absolute():
                path = PathManagerServer.project_dir() / path
            return path.resolve()
        return PathManagerServer.project_dir() / LAYOUT[P.EXTENSION]

    @staticmethod
    def package_dir() -> Path:
        """The installed package directory — shipped resources, read-only to a run.

        Derived from this file's location rather than from the project directory, because
        an installed package and the tree it writes into need not be the same checkout.
        """
        return Path(__file__).resolve().parents[1]

    def writable_roots(self) -> Tuple[Path, Path]:
        """The only two directories the framework may write to.

        Exposed so tests can assert the rule rather than trusting convention: ``output/``
        for generated, machine- and user-specific state, and ``extension/`` for shared,
        durable components.
        """
        return self.get(P.OUTPUT), self.get(P.EXTENSION)

    # ================================================================== #
    # 2. The bound session — whose run the session keys mean
    # ================================================================== #
    def bind_session(self, owner: str, session_id: str) -> None:
        """Make ``owner`` / ``session_id`` the default for every session-scoped key.

        Called once when a run's session is established: by the direct entry points before
        any manager initializes, and by the gateway per task on its serialized path. After
        this, ``get(P.SESSION_EXTENSION)`` needs no arguments — which is the point, since
        the caller asking may be a tool three layers down that was never handed a path.

        Rebinding is normal and clears any override, because an override describes one
        run's environment and must not outlive it.
        """
        if (owner, session_id) != (self._owner, self._session_id):
            self._overrides.clear()
        self._owner, self._session_id = str(owner), str(session_id)
        self._announce()

    def _listener_list(self) -> List[Callable[[], None]]:
        """The subscriber list, created on demand.

        This manager is shared process-wide and may have been constructed before the field
        existed, in which case ``__init__`` never runs again for it and a plain attribute
        would be missing on exactly the long-lived instance everything uses. The same shape
        has bitten ``plan_manager`` and ``ExtensionManager`` already.
        """
        listeners = getattr(self, "_listeners", None)
        if listeners is None:
            listeners = []
            self._listeners = listeners
        return listeners

    def on_rebind(self, listener: Callable[[], None]) -> None:
        """Be told when the bound session changes, so a derived directory can follow it.

        Six managers kept their own copy of "the current log root", and the gateway
        re-pointed each of them by name on every session change. Six copies that can
        disagree, held in step by remembering to add a line — and the line that is
        forgotten writes one session's files into the previous session's directory,
        without erroring.

        So the transition announces itself. That is the fix ``plan_manager`` already
        documents for the same shape: a state that changes without saying so leaves every
        holder of a value derived from it quietly wrong.

        Idempotent — a manager that initializes twice subscribes once.
        """
        listeners = self._listener_list()
        if listener not in listeners:
            listeners.append(listener)

    def _announce(self) -> None:
        """Tell every subscriber the session moved.

        A listener that raises must not stop the rebind: the session has already changed,
        and refusing to finish because one manager failed would leave the others bound to
        a session the caller believes it has left.
        """
        for listener in tuple(self._listener_list()):
            try:
                listener()
            except Exception as error:                              # noqa: BLE001
                from agentevolver.logger import logger
                logger.warning(f"| ⚠️ Path rebind listener failed: {error}")

    def unbind_session(self) -> None:
        """Forget the current session; session-scoped keys need explicit parameters again.

        Not merely tidiness. The sandbox boundary is enforced only for a run that has a
        session, so a leaked binding turns unrelated code — a bare script, the next test —
        into a run whose allowed roots belong to somebody else.
        """
        self._owner = self._session_id = None
        self._overrides.clear()

    @property
    def session(self) -> Optional[Tuple[str, str]]:
        """``(owner, session_id)`` of the bound run, or ``None`` outside one.

        Read rather than inferred: "no session" has to be distinguishable from "a session
        whose roots happen to look like the defaults", because the sandbox boundary is
        switched on by the former and not by the latter.
        """
        if self._owner is None or self._session_id is None:
            return None
        return self._owner, self._session_id

    def session_roots(self) -> Dict[str, Path]:
        """The roots this run owns, each under the name it is known by. Empty outside a run.

        The single place that answers "what may this run touch", so the sandbox check, the
        registration hook and the prompt an agent reads all describe the same boundary.

        ``extension`` and ``shared_extension`` are named apart because they were not named
        apart before: the first is the session's own staging tree, the second is the
        durable library that only promotion writes to, and a reader had to know which
        module they were in to tell which one they had.
        """
        bound = self.session
        if bound is None:
            return {}
        # Unparameterised on purpose. `owner` and `session_id` *are* the bound session, so
        # passing them said nothing extra — and `get` treats explicit params as "tell me
        # about a specific session", which is exactly the call an override does not answer.
        #
        # That cost the sandbox boundary. A run inside a container overrides
        # `SESSION_WORKSPACE` to the mount point, `/workspace`; this dict fed
        # `session_writable_roots()` the host-layout path instead, so
        # `write_file_tool` on `/workspace/cmatrix.c` — the deliverable, at the path the
        # task document names — came back "Sandbox denied write outside allowed roots"
        # while `bash` heredocs into the same directory worked. Sixteen refusals in one
        # ProgramBench instance, and the agent concluded `/workspace` was a symlink.
        roots = {
            name: self.get(key)
            for name, key in (
                ("project", P.SESSION),
                ("workspace", P.SESSION_WORKSPACE),
                ("log", P.SESSION_LOG),
                ("extension", P.SESSION_EXTENSION),
            )
        }
        roots["shared_extension"] = self.get(P.EXTENSION)
        roots["package"] = self.package_dir()
        return roots

    def override(self, key: P, path: str | Path) -> None:
        """Declare a path the layout cannot compute.

        One real case: a task running inside a container sees its workspace at the mount
        point, and no host-side table can derive a mount point from the host path. Such a
        path is still resolved *through* here, so it is one visible exception rather than a
        value substituted downstream where nothing distinguishes it from a default.

        Applies only to the unparameterised call — see :meth:`get`. Cleared by
        :meth:`bind_session` on a new session and by :meth:`unbind_session`.
        """
        self._overrides[key] = Path(path)

    # ================================================================== #
    # 3. Resolution — key + parameters to an absolute path
    # ================================================================== #
    def get(self, key: P, *, create: bool = False, **params: str) -> Path:
        """Resolve a layout key to an absolute path.

        Parameters the bound session knows — ``owner``, ``session_id`` — may be omitted
        and are filled in from it. Passing them explicitly asks about a *specific*
        session, which is why an override (a statement about the current run) does not
        answer such a call.

        Args:
            key: Which path, from :class:`~agentevolver.paths.types.P`.
            create: Create the directory (or the parent, for a file key) first.
            **params: Values for the template's placeholders, e.g. ``owner``.

        Raises:
            KeyError: The key has no entry in the layout.
            ValueError: A placeholder the template needs was neither supplied nor known
                from the bound session — raised rather than silently producing a directory
                literally named ``{owner}``, which is painful to trace back.
        """
        try:
            template = self._layout[key]
        except KeyError:
            raise KeyError(f"No layout entry for {key!r}") from None

        if not params and key in self._overrides:
            return self._materialize(self._overrides[key], create=create)

        required = set(_PLACEHOLDER.findall(template))
        params = {**self._session_params(required), **params}
        missing = required - params.keys()
        if missing:
            raise ValueError(f"{key.value} needs {sorted(missing)}; got {sorted(params)}")

        root, template = self._rebase(template)
        path = (root / template.format(**params)) if template else root
        return self._materialize(path, create=create)

    def under(self, root: str | Path, key: P, *, create: bool = False, **params: str) -> Path:
        """Resolve a key against a root the caller supplies.

        :meth:`get` hangs a template off one of the three roots, which is right for
        everything with a fixed home. Two families do not have one: a manager's working
        directory follows whichever log root the run is bound to, and a project's
        sub-roots are built for a directory the sandbox is handed, which need not be a
        session at all.

        The names are still the table's. They were joined by each caller instead —
        ``os.path.join(config.log_root, "memory")`` in the memory server and again in its
        context, and twenty other modules did the same — so renaming one meant finding
        forty-odd copies, and the table had no say in it.

        Args:
            root: The directory to resolve against.
            key: Which path, from :class:`~agentevolver.paths.types.P`.
            create: Create the directory first.
            **params: Values for the template's placeholders, e.g. ``module``.
        """
        template = self._layout[key]
        required = set(_PLACEHOLDER.findall(template))
        missing = required - params.keys()
        if missing:
            raise ValueError(f"{key.value} needs {sorted(missing)}; got {sorted(params)}")
        path = Path(root).expanduser() / template.format(**params)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def _session_params(self, required: Set[str]) -> Dict[str, str]:
        """What the bound session contributes to a template's placeholders.

        Only the two it knows, and only where they are actually required. Filling
        ``{module}`` or ``{conversation_id}`` from a session would convert a missing
        argument into a confidently wrong path instead of an error.
        """
        filled: Dict[str, str] = {}
        if "owner" in required and self._owner is not None:
            filled["owner"] = self._owner
        if "session_id" in required and self._session_id is not None:
            filled["session_id"] = self._session_id
        return filled

    @classmethod
    def _rebase(cls, template: str) -> Tuple[Path, str]:
        """Split a template into the root it hangs off and the rest of the path.

        Everything under ``extension/`` resolves against :meth:`extension_dir`, so
        relocating that root moves the whole subtree with it; everything else hangs off
        the project directory.
        """
        prefix = LAYOUT[P.EXTENSION]
        if template == prefix:
            return cls.extension_dir(), ""
        if template.startswith(f"{prefix}/"):
            return cls.extension_dir(), template[len(prefix) + 1:]
        return cls.project_dir(), template

    @staticmethod
    def _materialize(path: Path, *, create: bool) -> Path:
        """Create ``path`` when asked, treating a key with a suffix as a file.

        A file key's *parent* is what wants creating; making the file itself a directory
        is the kind of error that surfaces much later, as an unreadable JSON document.
        """
        if create:
            (path.parent if path.suffix else path).mkdir(parents=True, exist_ok=True)
        return path

    # ================================================================== #
    # 4. Introspection — the table as data
    # ================================================================== #
    def params_for(self, key: P) -> List[str]:
        """Placeholders a key needs — for callers building paths generically."""
        return sorted(set(_PLACEHOLDER.findall(self._layout[key])))

    def describe(self) -> Dict[str, str]:
        """The whole layout as plain data, for logging and diagnostics."""
        return {key.value: template for key, template in self._layout.items()}


#: Global path manager instance. One per process, like the layout it serves.
path_manager = PathManagerServer()

__all__ = ["PathManagerServer", "path_manager"]
