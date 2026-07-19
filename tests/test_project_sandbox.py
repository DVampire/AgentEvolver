from pathlib import Path

import pytest

from agentevolver.sandbox.project import ProjectSandbox


def test_promote_staged_extension_with_audit_record(tmp_path: Path) -> None:
    shared_extension = tmp_path / "shared-extension"
    sandbox = ProjectSandbox.create(
        tmp_path / "project",
        shared_extension_root=shared_extension,
        package_root=tmp_path / "package",
    )
    staged_tool = sandbox.extension_root / "tool" / "hello_tool.py"
    staged_tool.parent.mkdir(parents=True)
    staged_tool.write_text("def hello():\n    return 'hello'\n", encoding="utf-8")

    validation = sandbox.validate()
    assert validation["file_count"] == 1
    report = sandbox.promote()

    promoted = shared_extension / "tool" / "hello_tool.py"
    assert promoted.read_text(encoding="utf-8") == staged_tool.read_text(encoding="utf-8")
    assert report["promoted"][0]["destination"] == str(promoted)
    assert sandbox.manifest_path.exists()


def test_promote_refuses_overwrite_without_opt_in(tmp_path: Path) -> None:
    shared_extension = tmp_path / "shared-extension"
    sandbox = ProjectSandbox.create(tmp_path / "project", shared_extension_root=shared_extension)
    staged_tool = sandbox.extension_root / "tool" / "hello_tool.py"
    staged_tool.parent.mkdir(parents=True)
    staged_tool.write_text("VALUE = 'staged'\n", encoding="utf-8")
    target = shared_extension / "tool" / "hello_tool.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 'shared'\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        sandbox.promote()

    report = sandbox.promote(overwrite=True)
    assert report["backup_root"] is not None
    assert target.read_text(encoding="utf-8") == "VALUE = 'staged'\n"
