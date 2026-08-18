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
from agentevolver.paths import RELATIVE, P, path_manager
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
              "project_key": "key", "module": "skill", "conversation_id": "cid"}

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


def test_missing_placeholder_is_rejected_rather_than_written_literally(monkeypatch, tmp_path: Path) -> None:
    """A forgotten parameter must fail loudly, not create a dir named '{session_id}'.

    ``str.format`` leaves an unfilled placeholder alone rather than complaining,
    so the caller gets a plausible-looking path and the mistake surfaces days
    later as a literal ``{session_id}`` directory holding one run's files.
    """
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="session_id"):
        path_manager.get(P.SESSION_WORKSPACE, owner="local")


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
    table = {
        "workspace": path_manager.get(P.SESSION_WORKSPACE, owner="o", session_id="s").name,
        "log": path_manager.get(P.SESSION_TRACE, owner="o", session_id="s").parent.name,
    }
    assert sandbox.workspace_root.name == table["workspace"], (
        f"the table says a session's workspace is {table['workspace']!r}; "
        f"the sandbox creates {sandbox.workspace_root.name!r}")
    assert sandbox.log_root.name == table["log"], (
        f"the table puts session logs under {table['log']!r}; "
        f"the sandbox creates {sandbox.log_root.name!r}")


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

    root = Path(__file__).resolve().parents[1] / "agentevolver"
    pattern = re.compile(
        r'os\.path\.join\(\s*(?:config\.)?(?:log_root|workspace_root|base_root|project_root)\s*,'
        r'\s*["\'][\w.]+["\']\s*\)'
        r'|(?:log_root|workspace_root|base_root|project_root)\s*/\s*["\'][\w.]+["\']'
    )
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in str(path) or "/skill/" in str(path):
            continue          # bundled skill scripts are documents, not framework code
        if path.parent.name == "paths":
            continue          # the authority itself, whose docs quote the shape it replaced
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#") or "path_manager" in line:
                continue
            if pattern.search(line):
                offenders.append(f"{path.relative_to(root.parent)}:{number}: {line.strip()[:90]}")
    assert not offenders, (
        "these join a directory onto a root themselves; declare it in the layout table "
        "and resolve it with `path_manager.under`:\n" + "\n".join(offenders))
