"""Project instructions are layered by active path without crossing trust boundaries.

Overrides must shadow ordinary files, nested rules must follow root-to-leaf order, and
symlinks or paths outside the workspace must never import host content. Memory and Claude
instructions remain compatible inputs alongside AGENTS files.
"""

from agentevolver.agent.project_context import load_project_context
from agentevolver.memory.project import ProjectMemoryStore


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


def test_project_context_layers_rules_for_active_task_paths(tmp_path):
    source = tmp_path / "packages" / "api" / "handler.py"
    source.parent.mkdir(parents=True)
    source.write_text("pass", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("root rule", encoding="utf-8")
    (tmp_path / "packages" / "AGENTS.md").write_text("package rule", encoding="utf-8")
    (source.parent / "AGENTS.override.md").write_text("api override", encoding="utf-8")
    (source.parent / "AGENTS.md").write_text("shadowed api rule", encoding="utf-8")

    context = load_project_context(str(tmp_path), [str(source)])

    assert (
        context.index("root rule") < context.index("package rule") < context.index("api override")
    )
    assert "shadowed api rule" not in context


def test_project_context_ignores_active_paths_outside_workspace(tmp_path):
    outside = tmp_path.parent / "outside-project-context"
    outside.mkdir(exist_ok=True)
    (outside / "AGENTS.md").write_text("outside rule", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("inside rule", encoding="utf-8")

    context = load_project_context(str(tmp_path), [str(outside / "file.py")])

    assert "inside rule" in context and "outside rule" not in context


def test_project_context_budget_preserves_the_closest_scope(tmp_path):
    source = tmp_path / "packages" / "api" / "handler.py"
    source.parent.mkdir(parents=True)
    source.write_text("pass", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("root-memory-" * 4_000, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("generic-root-rule", encoding="utf-8")
    (source.parent / "AGENTS.md").write_text(
        "closest-api-rule", encoding="utf-8",
    )

    context = load_project_context(str(tmp_path), [str(source)])

    assert len(context) <= 32_000
    assert "closest-api-rule" in context
    assert "generic-root-rule" in context
    assert context.index("generic-root-rule") < context.index("closest-api-rule")


def test_project_memory_keeps_evidence_deduplicated_and_refuses_secrets(tmp_path):
    """Automatic memory is durable evidence, not an unrestricted scratchpad."""
    store = ProjectMemoryStore(str(tmp_path))
    store.path = tmp_path / "automatic-memory.json"

    assert store.remember(
        "verified_commands",
        "python -m pytest tests/test_project_context.py",
        source="trace:session:12",
    )
    assert not store.remember(
        "verified_commands",
        "python -m pytest tests/test_project_context.py",
        source="trace:session:13",
    )
    assert not store.remember(
        "credentials",
        "api_key=do-not-store",
        source="trace:session:14",
    )

    rendered = store.render()
    assert rendered.count("python -m pytest") == 1
    assert "trace:session:12" in rendered
    assert "do-not-store" not in rendered
