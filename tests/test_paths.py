from pathlib import Path

import pytest

from agentevolver.config.config import process_general
from agentevolver.paths import P, path_manager
from agentevolver.utils.path_utils import data_path, extension_root, home_dir, project_path
from mmengine import Config as MMConfig


def test_agentevolver_home_relocates_the_whole_tree(monkeypatch, tmp_path: Path) -> None:
    """AGENTEVOLVER_HOME moves the tree root, not one directory inside it.

    There is a single tree now, owned by the path manager, so the override picks
    where that tree lives; runtime state sits at output/.runtime beneath it.
    """
    root = tmp_path / "agent-home"
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(root))
    monkeypatch.delenv("AGENTEVOLVER_EXTENSION_ROOT", raising=False)

    assert home_dir() == (root / "output" / ".runtime").resolve()
    assert Path(data_path("run")).is_relative_to(root.resolve())
    # The default extension tree is project-owned, not user-owned.
    assert extension_root() == (Path.cwd() / "extension").resolve()


def test_every_declared_path_stays_inside_the_two_roots(monkeypatch, tmp_path: Path) -> None:
    """The layout table is the disk contract — nothing may escape output/ or extension/.

    Asserted rather than left to convention: a third location is exactly how
    .agentevolver/ appeared, root-owned and outside the chown loop that hands
    output/ back to the host user.
    """
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    output, extension = path_manager.writable_roots()
    sample = {"owner": "someone", "session_id": "sid", "run_id": "rid", "project_key": "key"}

    for key in P:
        resolved = path_manager.get(key, **{p: sample[p] for p in path_manager.params_for(key)})
        assert resolved.is_relative_to(output) or resolved.is_relative_to(extension), (
            f"{key.value} -> {resolved} escapes both writable roots"
        )


def test_missing_placeholder_is_rejected_rather_than_written_literally(monkeypatch, tmp_path: Path) -> None:
    """A forgotten parameter must fail loudly, not create a dir named '{session_id}'."""
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="session_id"):
        path_manager.get(P.SESSION_WORKSPACE, owner="local")


def test_runtime_output_is_relative_to_the_current_project(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path / "agent-home"))

    config = process_general(MMConfig(dict(
        project_root="output/demo",
        workspace_root="output/demo/workspace",
        log_root="output/demo/log",
        log_path="agent.log",
    )))

    assert Path(project_path("output/demo")) == project / "output" / "demo"
    assert Path(config.project_root) == project / "output" / "demo"
    assert not Path(config.project_root).is_relative_to(tmp_path / "agent-home")
    assert Path(config.extension_root) == project / "extension"
    assert not Path(config.workspace_root).exists()
    assert not Path(config.log_root).exists()
