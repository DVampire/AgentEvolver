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
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from agentevolver.paths.types import FILES, LAYOUT, P

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
        self._leases = 0
        self._workspace: ContextVar[Optional[Path]] = ContextVar("execution_workspace", default=None)
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

    @classmethod
    def package_resource(cls, *parts: str) -> Path:
        """Resolve a shipped read-only resource and keep it inside the package."""
        root = cls.package_dir()
        path = root.joinpath(*parts).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"package resource escapes package root: {parts!r}")
        return path

    def writable_roots(self) -> Tuple[Path, ...]:
        """Declared storage roots, not a grant of agent filesystem permissions."""
        return self.get(P.OUTPUT), self.get(P.EXTENSION), self.get(P.MEMORY)

    @staticmethod
    def memory_dir() -> Path:
        """Durable notes survive output cleanup; an override can use a persistent volume."""
        path = Path(os.environ.get("AGENTEVOLVER_MEMORY_ROOT") or LAYOUT[P.MEMORY]).expanduser()
        if not path.is_absolute():
            path = PathManagerServer.project_dir() / path
        return path.resolve()

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
        owner, session_id = str(owner), str(session_id)
        self._validate_components({"owner": owner, "session_id": session_id})
        if self._leases:
            if (owner, session_id) != self.session:
                raise RuntimeError("Cannot rebind an active runtime session; stop and join it or use another process")
            return
        if (owner, session_id) != (self._owner, self._session_id):
            self._overrides.clear()
        self._owner, self._session_id = owner, session_id
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
        if self._leases:
            raise RuntimeError("Cannot unbind an active runtime session; stop and join it first")
        self._owner = self._session_id = None
        self._overrides.clear()

    @property
    def leased(self) -> bool:
        """Whether running processes or their cleanup still own this session."""
        return bool(self._leases)

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
        if self._leases:
            raise RuntimeError("Cannot change shared path overrides while runtime processes are active")
        self._overrides[key] = Path(path)

    @contextmanager
    def lease(self):
        """Pin shared session roots until the runtime has joined all cleanup."""
        self._leases += 1
        try:
            yield
        finally:
            self._leases -= 1

    @contextmanager
    def workspace(self, path: str | Path):
        """Override only the executing task's workspace, inherited by its children.

        No rebind notification: log, memory backend, and registry roots remain those
        of the parent session. This is a cwd mapping, not an OS security boundary.
        """
        token = self._workspace.set(Path(path).expanduser().resolve())
        try:
            yield
        finally:
            self._workspace.reset(token)

    # ================================================================== #
    # 3. Resolution — key + parameters to an absolute path
    # ================================================================== #
    def get(self, key: P, *, create: bool = False, **params: str) -> Path:
        """Resolve a layout key to an absolute path.

        Parameters the bound session knows — ``owner``, ``session_id`` — may be omitted
        and are filled in from it. Passing them names a *specific* session, and an
        override is a statement about the current run, so the two only meet when the
        session named is the bound one — which they often are. Naming the run you are
        in is not a way to be told about a different one, and it used to be: `approve()`
        passes the id of the session whose plan it is writing, and that spelling alone
        moved the file to a path the agent could not write.

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

        # An empty value is not an answer. A caller with an optional `owner` passes ""
        # when it has none, and keeping it made `{**session_params, **params}` overwrite
        # the bound session's value with nothing — so two modules grew their own
        # `owner or bound_owner` to undo it. Dropped here, and neither needs to.
        params = {name: value for name, value in params.items() if value not in ("", None)}
        self._validate_components(params)

        if self._names_the_bound_session(params):
            workspace = self._workspace.get()
            prefix = self._layout[P.SESSION_WORKSPACE]
            if workspace is not None and (key == P.SESSION_WORKSPACE or template.startswith(prefix + "/")):
                suffix = template[len(prefix):].lstrip("/")
                required = set(_PLACEHOLDER.findall(suffix))
                values = {**self._session_params(required), **params}
                path = workspace / suffix.format(**values) if suffix else workspace
                return self._materialize(path, create=create, is_file=key in FILES)
            if key in self._overrides:
                return self._materialize(
                    self._overrides[key], create=create, is_file=key in FILES,
                )
            nested = self._under_an_override(key, template)
            if nested is not None:
                return self._materialize(nested, create=create, is_file=key in FILES)

        required = set(_PLACEHOLDER.findall(template))
        params = {**self._session_params(required), **params}
        missing = required - params.keys()
        if missing:
            raise ValueError(f"{key.value} needs {sorted(missing)}; got {sorted(params)}")

        root, template = self._rebase(template)
        path = (root / template.format(**params)) if template else root
        return self._materialize(path, create=create, is_file=key in FILES)

    def _names_the_bound_session(self, params: Dict[str, str]) -> bool:
        """Whether this call is about the run that is bound, however it was spelled.

        Nothing passed means the current run. So does passing its own owner and session
        id, which is what a caller holding a context does — and treating that as "a
        different session" is how an override stopped answering for the very run that
        declared it.

        Any other parameter (`module`, `run_id`, a different owner) makes it a question
        about something else, and overrides stay out of it.
        """
        if not params:
            return True
        bound = self.session
        if bound is None:
            return False
        owner, session_id = bound
        known = {"owner": owner, "session_id": session_id}
        return all(name in known and value == known[name] for name, value in params.items())

    def _under_an_override(self, key: P, template: str) -> Optional[Path]:
        """The path for a key that lives *inside* an overridden one, or None.

        An override replaces one key. Everything nested under it kept resolving from the
        table, so overriding `SESSION_WORKSPACE` to a container's mount point moved the
        workspace and left `workspace/plan.md` and `workspace/notebooks` at the host
        layout path. Two names for one directory, and whichever a caller happened to use
        decided whether it worked.

        Cascading here rather than at each nested key, because there is nothing special
        about those two: the rule is that a declaration about a directory is a declaration
        about what is in it, and the next key added under an overridden one gets it for
        free.

        The suffix carries no placeholders in practice — a nested key adds fixed segments
        to its parent — and one that did would be resolved against the same session
        parameters, so it is formatted rather than assumed literal.
        """
        for overridden, replacement in self._overrides.items():
            parent = self._layout.get(overridden)
            if not parent:
                continue
            prefix = parent.rstrip("/") + "/"
            if not template.startswith(prefix):
                continue
            suffix = template[len(prefix):]
            required = set(_PLACEHOLDER.findall(suffix))
            if required:
                params = self._session_params(required)
                if required - params.keys():
                    return None
                suffix = suffix.format(**params)
            return Path(replacement) / suffix
        return None

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
        self._validate_components(params)
        required = set(_PLACEHOLDER.findall(template))
        missing = required - params.keys()
        if missing:
            raise ValueError(f"{key.value} needs {sorted(missing)}; got {sorted(params)}")
        path = Path(root).expanduser() / template.format(**params)
        return self._materialize(path, create=create, is_file=key in FILES)

    def resolve_under(
        self, root: str | Path, relative: str | Path, *, create: bool = False,
    ) -> Path:
        """Resolve a config/user-supplied relative path without letting it escape ``root``.

        Layout-owned names belong in :data:`LAYOUT` and use :meth:`under`. This method is
        only for the remaining dynamic suffix supplied by configuration (for example a
        plugin's ``base_dir``); centralising it keeps absolute paths and ``..`` from
        silently bypassing the configured run root.
        """
        base = Path(root).expanduser().resolve()
        value = Path(relative).expanduser()
        candidate = value.resolve() if value.is_absolute() else (base / value).resolve()
        if not candidate.is_relative_to(base):
            raise ValueError(f"path {str(relative)!r} escapes managed root {base}")
        return self._materialize(candidate, create=create, is_file=False)

    def entry_under(self, root: str | Path, relative: str | Path) -> Path:
        """Return an unfollowed direct child for safe ``lstat``/symlink checks."""
        base = Path(root).expanduser().resolve()
        value = Path(relative)
        if value.is_absolute() or len(value.parts) != 1 or value.name in ("", ".", ".."):
            raise ValueError(f"path {str(relative)!r} is not one managed child of {base}")
        return base / value.name

    @staticmethod
    def _validate_components(params: Dict[str, str]) -> None:
        """Reject placeholder values that turn one declared component into a path."""
        for name, raw in params.items():
            value = str(raw)
            if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
                raise ValueError(f"path parameter {name} must be one component, got {value!r}")

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
        memory = LAYOUT[P.MEMORY]
        if template == memory:
            return cls.memory_dir(), ""
        if template.startswith(f"{memory}/"):
            return cls.memory_dir(), template[len(memory) + 1:]
        prefix = LAYOUT[P.EXTENSION]
        if template == prefix:
            return cls.extension_dir(), ""
        if template.startswith(f"{prefix}/"):
            return cls.extension_dir(), template[len(prefix) + 1:]
        return cls.project_dir(), template

    @staticmethod
    def _materialize(path: Path, *, create: bool, is_file: bool) -> Path:
        """Create a declared directory or a declared file's parent when asked.

        A file key's *parent* is what wants creating; making the file itself a directory
        is the kind of error that surfaces much later, as an unreadable JSON document.
        """
        if create:
            (path.parent if is_file else path).mkdir(parents=True, exist_ok=True)
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
