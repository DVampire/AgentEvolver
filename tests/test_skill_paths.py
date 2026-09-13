"""Skill source discovery remains usable after changing cwd or compacting history."""
from pathlib import Path

import pytest

from agentevolver.agent.context.assembler import ContextAssembler
from agentevolver.agent.context.conversation import Conversation
from agentevolver.message.types import AssistantMessage
from agentevolver.skill.context import SkillContextManager
from agentevolver.skill.server import SkillManagerServer


@pytest.mark.asyncio
async def test_nested_skill_paths_are_absolute_in_native_schema_and_invocation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = Path("skills/finance/research_method")
    (source / "scripts/__pycache__").mkdir(parents=True)
    (source / "references/nested").mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: research_method\ndescription: Investigate a question\nversion: 1.0.0\n---\n"
        "Method body marker. Read [details](references/nested/detail.md).\n"
        "Run python {skill_dir}/scripts/check.py or python scripts/check.py.\n")
    (source / "scripts/check.py").write_text("print('checked')\n")
    (source / "scripts/__pycache__/check.pyc").write_bytes(b"irrelevant bytecode")
    (source / "scripts/legacy.pyc").write_bytes(b"irrelevant bytecode")
    (source / "references/nested/detail.md").write_text("Resolve sibling files from here.")
    source = source.resolve()
    manager = SkillContextManager(base_dir=str(tmp_path / "runtime"))
    cfg = manager._parse_skill_dir(Path("skills/finance/research_method"))
    manager._skill_configs[cfg.name] = cfg
    server = SkillManagerServer()
    server.skill_context_manager = manager

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    first = await server.get_schema(cfg.name)
    manifest = str(source / "SKILL.md")
    assert manifest in first["function"]["description"]
    assert "Method body marker" not in first["function"]["description"]
    assert (await server.function_callings())[0][0] == first
    response = await server(name=cfg.name, input={})
    assert response.success and response.data["manifest_path"] == manifest
    assert response.message.startswith(f"Skill source: {manifest}")
    assert str(source / "references/nested/detail.md") in response.message
    assert response.message.count(f"python {source}/scripts/check.py") == 2
    assert "{skill_dir}" not in response.message and "__pycache__" not in response.message
    assert "legacy.pyc" not in response.message
    assert all(Path(p).is_absolute() and Path(p).is_file() for p in cfg.scripts + cfg.references)

    # The source locator is carried by the native schema even if old skill calls
    # disappear into a history checkpoint. No procedure is copied into live context.
    history = Conversation(task="Research")
    for i in range(3):
        history.note(response.message if i == 0 else "Continue")
        history.append(AssistantMessage(content="Recorded the result"))
    assert history.fold("Progress summary without skill paths", keep_turns=1) > 0
    ContextAssembler().build(history)
    assert manifest not in str(history.items) and manifest not in str(history.checkpoint)
    assert await server.get_schema(cfg.name) == first


@pytest.mark.asyncio
async def test_source_locator_tracks_the_active_skill_revision(tmp_path):
    manager = SkillContextManager(base_dir=str(tmp_path / "runtime"))
    server = SkillManagerServer()
    server.skill_context_manager = manager
    descriptions = []
    for version in ("1.0.0", "1.0.1"):
        root = tmp_path / version / "method"
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(
            f"---\nname: method\ndescription: Method\nversion: {version}\n---\nCurrent instructions.\n")
        manager._skill_configs["method"] = manager._parse_skill_dir(root)
        schema = await server.get_schema("method")
        descriptions.append(schema["function"]["description"])
        reply = await server(name="method", input={})
        assert str(root / "SKILL.md") in descriptions[-1]
        assert reply.data["version"] == version and str(root / "SKILL.md") in reply.message
    assert descriptions[0] != descriptions[1]
