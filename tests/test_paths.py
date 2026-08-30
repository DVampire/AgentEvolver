"""The layout table is the only place a path is decided, and it has exactly two roots.

Everything the framework writes goes under ``output/`` (generated, disposable
state) or ``extension/`` (shared, durable components), and both move as one unit
when ``AGENTEVOLVER_HOME`` is set. The damage from breaking that is quiet rather
than loud: ``extension/`` once resolved against ``cwd`` while the rest of the
tree honoured the override, so under a relocated home the skill manager wrote
components into a directory the config layer never read. A path invented outside
the table is how a root-owned ``.agentevolver/`` appeared in a third location,
outside the chown loop that hands ``output/`` back to the host user.
"""

from pathlib import Path

import pytest
from mmengine import Config as MMConfig

from agentevolver.config.config import process_general
from agentevolver.paths import FILES, RELATIVE, P, path_manager
from agentevolver.utils.path_utils import data_path, extension_root, home_dir, project_path


def test_agentevolver_home_relocates_the_whole_tree(monkeypatch, tmp_path: Path) -> None:
    """AGENTEVOLVER_HOME moves the tree root — every part of it, not just output/.

    ``extension/`` used to be exempt: it resolved against ``cwd`` in
    ``extension_root()`` while the layout put it under the override, so setting
    this variable relocated generated state and left every shared component
    behind.
    """
    root = tmp_path / "agent-home"
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(root))
    monkeypatch.delenv("AGENTEVOLVER_EXTENSION_ROOT", raising=False)

    assert home_dir() == (root / "output" / ".runtime").resolve()
    assert Path(data_path("run")).is_relative_to(root.resolve())
    assert extension_root() == (root / "extension").resolve()
    assert Path(project_path("output/demo")) == (root / "output" / "demo").resolve()


def test_extension_root_override_moves_only_that_root(monkeypatch, tmp_path: Path) -> None:
    """AGENTEVOLVER_EXTENSION_ROOT relocates shared components, nothing else.

    The two variables read like one knob with two names, and the tempting
    implementation moves the whole tree for either. It must not: several runs
    share one component library while each keeps its own generated state, so an
    override that dragged ``output/`` along would file one deployment's sessions
    inside another deployment's library.
    """
    home, shared = tmp_path / "agent-home", tmp_path / "shared-components"
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(home))
    monkeypatch.setenv("AGENTEVOLVER_EXTENSION_ROOT", str(shared))

    assert path_manager.get(P.EXTENSION) == shared.resolve()
    assert path_manager.get(P.EXTENSION_MODULE, module="skill") == (shared / "skill").resolve()
    assert extension_root() == shared.resolve()
    # Generated state stays with the tree root.
    assert path_manager.get(P.OUTPUT) == (home / "output").resolve()


def test_every_resolver_agrees_on_where_extension_lives(monkeypatch, tmp_path: Path) -> None:
    """Skill, connector, config and the layout must answer this identically.

    Each of them used to resolve ``extension/`` itself — against ``cwd``, or an
    env var, or ``project_path`` — so under a relocated tree they disagreed and
    components were written to a directory nothing else read.
    """
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    monkeypatch.delenv("AGENTEVOLVER_EXTENSION_ROOT", raising=False)
    expected = (tmp_path / "extension").resolve()

    from agentevolver.connector.context import ConnectorContextManager
    from agentevolver.skill.context import SkillContextManager

    assert extension_root() == expected
    assert Path(SkillContextManager().extension_skills_dir) == expected / "skill"
    assert Path(ConnectorContextManager().extension_connectors_dir) == expected / "connector"
    config = process_general(MMConfig(dict(
        project_root="output/demo", workspace_root="output/demo/workspace",
        log_root="output/demo/log", log_path="agent.log")))
    assert Path(config.extension_root) == expected


def test_every_declared_path_stays_inside_the_two_roots(monkeypatch, tmp_path: Path) -> None:
    """The layout table is the disk contract — nothing may escape output/ or extension/.

    Asserted rather than left to convention: a third location is exactly how
    .agentevolver/ appeared, root-owned and outside the chown loop that hands
    output/ back to the host user.
    """
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    monkeypatch.delenv("AGENTEVOLVER_EXTENSION_ROOT", raising=False)
    output, extension = path_manager.writable_roots()
    sample = {"owner": "someone", "session_id": "sid", "run_id": "rid",
              "project_key": "key", "module": "skill", "conversation_id": "cid",
              "digest": "digest"}

    for key in P:
        if key in RELATIVE:
            # A fragment joined onto a root the caller supplies, so the rule applies to
            # that root. Checked below instead: the fragment must stay a fragment.
            continue
        resolved = path_manager.get(key, **{p: sample[p] for p in path_manager.params_for(key)})
        assert resolved.is_relative_to(output) or resolved.is_relative_to(extension), (
            f"{key.value} -> {resolved} escapes both writable roots"
        )


def test_a_relative_key_cannot_escape_the_root_it_is_joined_to() -> None:
    """`under` joins a fragment onto a caller's root, so the fragment decides nothing.

    An absolute template, or one starting `../`, would silently ignore that root — and
    these are exactly the keys a manager resolves against whatever log root the run is
    bound to, so escaping one would write outside the session.
    """
    from agentevolver.paths.types import LAYOUT

    for key in RELATIVE:
        template = LAYOUT[key]
        assert not template.startswith(("/", "~")), f"{key.value} is absolute: {template}"
        assert ".." not in template.split("/"), f"{key.value} climbs out: {template}"
        resolved = path_manager.under("/somewhere", key,
                                      **{p: "x" for p in path_manager.params_for(key)})
        assert resolved.is_relative_to("/somewhere"), f"{key.value} escaped: {resolved}"


def test_file_keys_create_their_parent_and_directory_keys_create_themselves(tmp_path) -> None:
    target = path_manager.under(
        tmp_path, P.LOG_TASK_VIEW, filename="request.with.dots", create=True,
    )
    directory = path_manager.under(
        tmp_path, P.LOG_MODULE, module="directory.with.dots", create=True,
    )

    assert P.LOG_TASK_VIEW in FILES
    assert target.parent.is_dir() and not target.exists()
    assert directory.is_dir()


def test_every_layout_key_has_a_framework_caller() -> None:
    """Dead entries are misleading promises about directories the framework owns."""
    import re

    root = Path(__file__).resolve().parents[1]
    sources = [
        path.read_text(encoding="utf-8")
        for tree in (root / "agentevolver", root / "examples")
        for path in tree.rglob("*.py")
        if path != root / "agentevolver" / "paths" / "types.py"
    ]
    unused = [
        key.name for key in P
        if not any(re.search(rf"\bP\.{key.name}\b", source) for source in sources)
    ]
    assert not unused, f"layout keys with no framework caller: {unused}"


def test_missing_placeholder_is_rejected_rather_than_written_literally(monkeypatch, tmp_path: Path) -> None:
    """A forgotten parameter must fail loudly, not create a dir named '{session_id}'.

    ``str.format`` leaves an unfilled placeholder alone rather than complaining,
    so the caller gets a plausible-looking path and the mistake surfaces days
    later as a literal ``{session_id}`` directory holding one run's files.
    """
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="session_id"):
        path_manager.get(P.SESSION_WORKSPACE, owner="local")


def test_placeholder_values_cannot_escape_the_declared_layout(tmp_path: Path) -> None:
    """A tag/owner is a name, not a second unvalidated path language."""
    with pytest.raises(ValueError, match="one component"):
        path_manager.get(P.OWNER, owner="../outside")
    with pytest.raises(ValueError, match="one component"):
        path_manager.under(tmp_path, P.LOG_MODULE, module="trace/../../outside")
    with pytest.raises(ValueError, match="one component"):
        path_manager.bind_session("../outside", "session")


def test_runtime_output_is_relative_to_the_current_project(monkeypatch, tmp_path: Path) -> None:
    """With no override, everything hangs off the directory the run started in.

    The last two assertions are the ones worth keeping: resolving a root must not
    create it. Directories are made when something is written, so a resolver that
    calls ``mkdir`` leaves an empty ``workspace/`` and ``log/`` behind for every
    session a client opens and abandons.
    """
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.delenv("AGENTEVOLVER_HOME", raising=False)
    monkeypatch.delenv("AGENTEVOLVER_EXTENSION_ROOT", raising=False)

    config = process_general(MMConfig(dict(
        project_root="output/demo",
        workspace_root="output/demo/workspace",
        log_root="output/demo/log",
        log_path="agent.log",
    )))

    assert Path(project_path("output/demo")) == project / "output" / "demo"
    assert Path(config.project_root) == project / "output" / "demo"
    assert Path(config.extension_root) == project / "extension"
    assert Path(config.project_root) == path_manager.get(P.OUTPUT) / "demo"
    assert not Path(config.workspace_root).exists()
    assert not Path(config.log_root).exists()


def test_tag_is_the_default_output_namespace_when_project_root_is_omitted(
    monkeypatch, tmp_path: Path,
) -> None:
    """A direct-run tag must not silently fall back to the unrelated ``local`` tree."""
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    resolved = process_general(MMConfig(dict(tag="swebench_pro_agent_baseline",
                                             log_path="agent.log")))

    expected = path_manager.get(P.OWNER, owner="swebench_pro_agent_baseline")
    assert Path(resolved.project_root) == expected
    assert Path(resolved.workspace_root) == path_manager.under(
        expected, P.PROJECT_WORKSPACE,
    )
    assert Path(resolved.log_root) == path_manager.under(expected, P.PROJECT_LOG)


def test_shipped_entry_configs_do_not_override_the_tag_namespace() -> None:
    """One hard-coded project_root would recreate the local/tag split."""
    root = Path(__file__).resolve().parents[1] / "configs"
    offenders = []
    for source in sorted(root.glob("*.py")):
        if source.name == "__init__.py":
            continue
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("project_root"):
                offenders.append(f"{source.name}:{number}: {line.strip()}")
    assert not offenders, (
        "shipped configs must let PathManager derive output from output_owner/tag:\n"
        + "\n".join(offenders)
    )


def test_swebench_launcher_has_no_local_session_owner_literal() -> None:
    """Host and inner launch paths both derive the namespace from the loaded config."""
    root = Path(__file__).resolve().parents[1]
    programbench = (root / "examples" / "run_programbench.py").read_text(encoding="utf-8")
    swebench = (root / "examples" / "run_swebench_pro.py").read_text(encoding="utf-8")

    assert 'SESSION_OWNER = "local"' not in programbench
    assert "SESSION_OWNER" not in swebench
    assert 'os.path.join("output", "swebench_pro_runs"' not in swebench


def test_the_sandbox_names_its_roots_the_same_way_the_table_does(monkeypatch, tmp_path: Path) -> None:
    """Two places decide where a session's workspace and log sit.

    The layout table says `output/{owner}/sessions/{id}/workspace`, and `ProjectSandbox`
    builds `project / "workspace"` from a root it is handed — which is usually that same
    session directory but may be any path, so it cannot ask the table. The leaf names are
    therefore written twice, and a rename in the table would leave the sandbox creating
    the old one: every session would have both, one of them empty, and which one a given
    reader found would depend on which of the two it asked.
    """
    from agentevolver.sandbox.project import ProjectSandbox

    sandbox = ProjectSandbox.create(tmp_path / "sess")
    for name, key, built in (
        ("workspace", P.SESSION_WORKSPACE, sandbox.workspace_root),
        ("log", P.SESSION_LOG, sandbox.log_root),
        ("extension", P.SESSION_EXTENSION, sandbox.extension_root),
    ):
        declared = path_manager.get(key, owner="o", session_id="s").name
        assert built.name == declared, (
            f"the table says a session's {name} is {declared!r}; "
            f"the sandbox creates {built.name!r}")


def test_a_direct_runner_binds_its_roots_before_the_managers_start(monkeypatch) -> None:
    """`bind_session_roots` after the managers initialize is too late.

    Every manager derives its own directory from `config.log_root` when it initializes,
    so binding afterwards leaves the first half of the run writing to the tag-level
    `output/<owner>/log` and only the second half reaching the session. The evolution
    runners did exactly that: an agent's log landed outside the session it belonged to.
    """
    import re

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for script in sorted((root / "examples").glob("run_*.py")):
        text = script.read_text(encoding="utf-8")
        if "bind_session_roots" not in text:
            continue
        bind = text.index("bind_session_roots(config")
        for manager in ("logger.initialize(", "task_manager.initialize("):
            if manager in text and text.index(manager) < bind:
                offenders.append(f"{script.name}: {manager} runs before bind_session_roots")
    assert not offenders, offenders


def test_no_module_joins_its_own_directory_onto_a_root() -> None:
    """The layout table is the only place that decides where anything goes.

    Every manager used to join its own working directory itself —
    `os.path.join(config.log_root, "memory")` — and each did it twice, once in its
    server and once in its context, so one rule was written forty-odd times in code the
    table had no view of. Renaming a directory meant finding every copy, and a missed one
    is silent: that manager simply keeps writing where it always did while everything
    else has moved.

    `path_manager.under(root, P.LOG_MODULE, module=...)` is the supported way to ask.
    """
    import re

    project = Path(__file__).resolve().parents[1]
    pattern = re.compile(
        r'os\.path\.join\(\s*(?:config\.)?(?:log_root|workspace_root|base_root|project_root)\s*,'
        r'\s*["\'][\w.]+["\']\s*\)'
        r'|(?:log_root|workspace_root|base_root|project_root)\s*/\s*["\'][\w.]+["\']'
    )
    offenders = []
    sources = sorted((project / "agentevolver").rglob("*.py")) + \
              sorted((project / "examples").rglob("*.py"))
    for path in sources:
        if "__pycache__" in str(path) or "/skill/" in str(path):
            continue          # bundled skill scripts are documents, not framework code
        if path.parent.name == "paths":
            continue          # the authority itself, whose docs quote the shape it replaced
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#") or "path_manager" in line:
                continue
            if pattern.search(line):
                offenders.append(f"{path.relative_to(project)}:{number}: {line.strip()[:90]}")
    assert not offenders, (
        "these join a directory onto a root themselves; declare it in the layout table "
        "and resolve it with `path_manager.under`:\n" + "\n".join(offenders))


# ---------------------------------------------------------------------------
# The bound session
# ---------------------------------------------------------------------------
def test_a_session_scoped_key_needs_no_arguments_once_the_run_is_bound() -> None:
    """The reason binding exists.

    A tool three layers down asking where it may write has no owner or session id to pass
    — it was never handed them. Before, the roots were copied into `ctx.extra` and carried
    to it; the copy could be rewritten by anything holding the dict, and `extension_root`
    there meant the session's staging tree while the identically-named `config` attribute
    meant the shared library.
    """
    path_manager.bind_session("someone", "run7")
    try:
        assert path_manager.get(P.SESSION_EXTENSION) == path_manager.get(
            P.SESSION_EXTENSION, owner="someone", session_id="run7")
    finally:
        path_manager.unbind_session()


def test_outside_a_session_the_same_key_is_an_error_rather_than_a_guess() -> None:
    """Unbound, there is no run to answer for, and a guess would be a wrong path.

    Specifically it would be `output/{owner}/...` with the braces intact, which surfaces
    much later as a directory nobody can explain.
    """
    assert path_manager.session is None
    with pytest.raises(ValueError, match="owner"):
        path_manager.get(P.SESSION_EXTENSION)


def test_binding_a_different_session_does_not_leave_the_previous_ones_override() -> None:
    """An override describes one run's environment — a container's view of its mount.

    Carried into the next run it would point that run's workspace at a directory belonging
    to a container that is already gone.
    """
    path_manager.bind_session("o", "first")
    path_manager.override(P.SESSION_WORKSPACE, "/workspace")
    assert path_manager.get(P.SESSION_WORKSPACE) == Path("/workspace")
    try:
        path_manager.bind_session("o", "second")
        assert path_manager.get(P.SESSION_WORKSPACE) != Path("/workspace")
    finally:
        path_manager.unbind_session()


def test_an_override_answers_for_this_run_however_it_is_named() -> None:
    """`override` states where *this* run's workspace is, and naming the run is still it.

    This asserted the opposite — that passing the bound owner and session id resolved
    past the override — on the grounds that ProgramBench needs the agent's `/workspace`
    and the host's real directory in one process. It does not: `bind_task_workspace` is
    called from `run_inner`, inside the container, and the launcher is a separate process
    that never overrides anything. The file has exactly one `path_manager.override` call.

    Meanwhile the behaviour it pinned had a cost. Every key nested under the workspace —
    `plan.md`, `notebooks` — resolves with `owner=`/`session_id=` from callers holding a
    context, and each of those calls quietly resolved past the mount point to the host
    layout path. `plan_manager.approve` names the session whose plan it is writing, and
    that spelling alone put the file where the agent is not permitted to write.

    The escape hatch is not lost, it is named: `under(project_dir(), key, ...)` says
    "resolve against the layout root" outright, instead of relying on the side effect of
    passing parameters.
    """
    path_manager.bind_session("o", "run9")
    path_manager.override(P.SESSION_WORKSPACE, "/workspace")
    try:
        assert path_manager.get(P.SESSION_WORKSPACE) == Path("/workspace")
        assert path_manager.get(P.SESSION_WORKSPACE, owner="o",
                                session_id="run9") == Path("/workspace")

        # A genuinely different run is a different question.
        other = path_manager.get(P.SESSION_WORKSPACE, owner="o", session_id="run10")
        assert other != Path("/workspace") and other.is_absolute()

        # And the host path for *this* run, asked for as such.
        host = path_manager.under(path_manager.project_dir(), P.SESSION_WORKSPACE,
                                  owner="o", session_id="run9")
        assert host != Path("/workspace") and host.is_absolute()
    finally:
        path_manager.unbind_session()


def test_session_roots_name_the_staging_tree_apart_from_the_shared_library() -> None:
    """The distinction the old naming lost.

    `extension` is where this run writes; `shared_extension` is the durable library that
    only promotion writes into. Spelled the same, an agent told the shared one writes
    where promotion refuses to look, and the run fails at its last step having done all
    the work.
    """
    path_manager.bind_session("o", "run10")
    try:
        roots = path_manager.session_roots()
        assert set(roots) == {"project", "workspace", "log", "extension",
                              "shared_extension", "package"}
        assert roots["extension"] != roots["shared_extension"]
        assert roots["extension"].is_relative_to(roots["project"])
        assert not roots["shared_extension"].is_relative_to(roots["project"])
    finally:
        path_manager.unbind_session()


def test_unbinding_closes_the_sandbox_boundary_rather_than_leaving_it_open() -> None:
    """A leaked binding is not a harmless leftover.

    The sandbox check is enforced only for a bound run, so a binding left behind makes
    unrelated code — the next test, a bare script — a run whose allowed roots belong to
    somebody else. Nineteen tests failed exactly this way, refusing their own `tmp_path`
    as "outside allowed roots".
    """
    from agentevolver.sandbox.project import check_session_path

    path_manager.bind_session("o", "run11")
    outside = str(Path("/etc/cron.d/pwned"))
    assert check_session_path(None, outside, write=True) is not None
    path_manager.unbind_session()
    assert check_session_path(None, outside, write=True) is None


def test_no_root_travels_through_a_context() -> None:
    """The rule the binding replaced, kept from coming back.

    A root read out of `ctx.extra` is a copy, and a copy is authoritative to whoever holds
    it: any code with the dict could widen its own sandbox, and the two spellings of
    `extension_root` — the session's staging tree in some modules, the shared library in
    others — were indistinguishable at the point of use. Both failures were quiet. This
    scan is loud.

    Scoped to the six root names, so an unrelated key in `extra` (a sandbox handle, a
    plugin allowlist) is unaffected — those are not paths and do not belong to the table.
    """
    import re

    root = Path(__file__).resolve().parents[1]
    roots = "project_root|workspace_root|log_root|extension_root|package_root|shared_extension_root"
    pattern = re.compile(
        rf"""(extra|roots)\s*(\.get\(\s*|\[\s*)["']({roots})["']"""
    )
    offenders = []
    for source in sorted((root / "agentevolver").rglob("*.py")) + \
                  sorted((root / "examples").glob("*.py")):
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "``" in line:
                continue          # a comment or a docstring may name the old shape
            if pattern.search(line):
                offenders.append(f"{source.relative_to(root)}:{number}: {stripped}")
    assert not offenders, (
        "these read a root out of a context; ask path_manager.session_roots() instead:\n"
        + "\n".join(offenders)
    )
