from agentevolver.agent.project_context import load_project_context


def test_project_context_prefers_agents_override_and_reads_memory(tmp_path):
    (tmp_path / "AGENTS.md").write_text("ordinary", encoding="utf-8")
    (tmp_path / "AGENTS.override.md").write_text("override", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude rules", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("known fact", encoding="utf-8")

    context = load_project_context(str(tmp_path))

    assert "override" in context and "ordinary" not in context
    assert "claude rules" in context and "known fact" in context


def test_project_context_does_not_follow_instruction_symlinks(tmp_path):
    outside = tmp_path.parent / "outside-agents.md"
    outside.write_text("host secret", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to(outside)

    assert load_project_context(str(tmp_path)) == ""


def test_symlinked_override_falls_back_to_regular_agents_file(tmp_path):
    outside = tmp_path.parent / "outside-override.md"
    outside.write_text("host secret", encoding="utf-8")
    (tmp_path / "AGENTS.override.md").symlink_to(outside)
    (tmp_path / "AGENTS.md").write_text("project rules", encoding="utf-8")

    context = load_project_context(str(tmp_path))

    assert "project rules" in context and "host secret" not in context
