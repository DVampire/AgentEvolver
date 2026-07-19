from pathlib import Path

from agentevolver.config.config import process_general
from agentevolver.utils.path_utils import data_path, extension_root, home_dir, project_path
from mmengine import Config as MMConfig


def test_user_home_is_the_base_for_writable_roots(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "agent-home"
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(root))
    monkeypatch.delenv("AGENTEVOLVER_EXTENSION_ROOT", raising=False)

    assert home_dir() == root.resolve()
    assert Path(data_path("output/run")).is_relative_to(root.resolve())
    # User-level storage remains separate; the default extension tree is project-owned.
    assert extension_root() == (Path.cwd() / "extension").resolve()


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
