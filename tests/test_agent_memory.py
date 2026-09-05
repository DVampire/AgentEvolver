"""Thread continuity and file memory, exercised without a model or background run."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from agentevolver.agent.context.assembler import ContextAssembler
from agentevolver.agent.context.conversation import Conversation
from agentevolver.agent.loop.agent import Agent
from agentevolver.agent.loop.decision import Decision
from agentevolver.memory.project import ProjectNotes
from agentevolver.message.types import AssistantMessage, CompactionMessage, HumanMessage, SystemMessage
from agentevolver.paths import P, path_manager
from agentevolver.runtime.envelopes import TaskEnvelope
from agentevolver.runtime.kernel import Kernel
from agentevolver.runtime.states import ProcessState


class RememberingAgent(Agent):
    """Expose the exact context on each turn without spending any tokens."""

    async def system_messages(self, ctx):
        return [SystemMessage(content="fixed rules")]

    async def think(self, step, live=()):
        self.last_context = self.assembler.build(self.conversation)
        return Decision(text="Remembered: " + self.task)

    async def _emit_start(self):
        pass

    async def _post_step(self, step, decision, results):
        self.save_thread()

    async def on_exit(self, status):
        pass


async def idle(proc, turns):
    async def wait():
        while proc.turns < turns or proc.state != ProcessState.IDLE:
            if proc.exited:
                raise AssertionError(proc.error)
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 2)


@pytest.mark.asyncio
async def test_resident_keeps_history_and_other_participant_does_not(bound_session):
    kernel = Kernel()
    try:
        a, b = RememberingAgent(), RememberingAgent()
        p = await kernel.spawn(a, "Persona A", resident=True, start_idle=True,
                               ctx=SimpleNamespace(id="a", extra={}))
        q = await kernel.spawn(b, "Persona B", resident=True, start_idle=True,
                               ctx=SimpleNamespace(id="b", extra={}))
        await kernel.send(p, TaskEnvelope(task="I want a sailboat"))
        await idle(p, 1)
        fixed = [m.model_dump() for m in a.conversation.system]
        await kernel.send(p, TaskEnvelope(task="Did you implement my request?"))
        await idle(p, 2)
        text = "\n".join(m.text for m in a.last_context)
        assert "I want a sailboat" in text and "Did you implement my request?" in text
        assert text.count("<task>") == 1
        assert [m.model_dump() for m in a.conversation.system] == fixed
        assert not any("Persona A" in m.text for m in a.conversation.items if m.role == "user")
        await kernel.send(q, TaskEnvelope(task="My first visit"))
        await idle(q, 1)
        assert "sailboat" not in "\n".join(m.text for m in b.last_context)
        assert a._thread_path != b._thread_path
    finally:
        await kernel.shutdown(timeout=2)


@pytest.mark.asyncio
async def test_explicit_resume_preserves_thread_after_process_replacement(bound_session):
    kernel = Kernel()
    try:
        ctx = SimpleNamespace(id="original", extra={})
        a = RememberingAgent()
        p = await kernel.spawn(a, "Build a sailboat", ctx=ctx, thread_id="design")
        await kernel.wait(p, timeout=2)
        b = RememberingAgent()
        q = await kernel.spawn(b, "Continue", ctx=ctx, thread_id="design", resume=True)
        await kernel.wait(q, timeout=2)
        assert "Build a sailboat" in "\n".join(m.text for m in b.last_context)
        assert b.conversation.turns == 2
        # A new independent assignment does not accidentally inherit the disk history.
        c = RememberingAgent()
        r = await kernel.spawn(c, "Unrelated", ctx=SimpleNamespace(id="new", extra={}))
        await kernel.wait(r, timeout=2)
        assert "sailboat" not in "\n".join(m.text for m in c.last_context)
    finally:
        await kernel.shutdown(timeout=2)


def test_snapshot_preserves_provider_state_and_rejects_other_route(tmp_path):
    conversation = Conversation(task="Keep the full text")
    conversation.checkpoint = CompactionMessage(
        content="Readable checkpoint", provider_state={"responses": {"opaque": "signed"}},
    )
    conversation.append(AssistantMessage(content="x" * 50000,
                                         provider_state={"anthropic": {"signature": "original"}}))
    path = tmp_path / "thread.json"
    conversation.save(path, model="route-a", agent="builder")
    restored = Conversation.load(path, model="route-a", agent="builder")
    assert restored.items[0].model_dump() == conversation.items[0].model_dump()
    assert restored.checkpoint.model_dump() == conversation.checkpoint.model_dump()
    with pytest.raises(ValueError, match="original model"):
        Conversation.load(path, model="route-b", agent="builder")


@pytest.mark.asyncio
async def test_failed_resume_does_not_overwrite_saved_history(bound_session):
    path = path_manager.get(P.SESSION_AGENT_CONTEXT, thread_id="bad", create=True)
    path.write_text('{"invalid":true}')
    kernel = Kernel()
    try:
        p = await kernel.spawn(RememberingAgent(), "continue", thread_id="bad", resume=True)
        await kernel.wait(p, timeout=2)
        assert p.error
        assert path.read_text() == '{"invalid":true}'
    finally:
        await kernel.shutdown(timeout=2)


def test_fold_retains_incoming_request_with_its_answer():
    c = Conversation(task="initial")
    c.append(AssistantMessage(content="old answer"))
    c.note("new requirement")
    c.append(AssistantMessage(content="new answer"))
    c.fold("older summary", 1)
    assert [m.text for m in c.items] == ["new requirement", "new answer"]
    ContextAssembler().build(c)


def test_private_notes_have_stable_project_and_separate_actor_identity(bound_session):
    a = ProjectNotes("/run/one", project_id="echo", actor_id="alice")
    again = ProjectNotes("/run/two", project_id="echo", actor_id="alice")
    b = ProjectNotes("/run/one", project_id="echo", actor_id="bob")
    other = ProjectNotes("/run/one", project_id="other", actor_id="alice")
    assert a.dir == again.dir and a.dir != b.dir and a.dir != other.dir
    assert not a.dir.is_relative_to(path_manager.get(P.OUTPUT))


def test_index_is_stable_skips_unsafe_files_and_does_not_use_seen(bound_session, tmp_path):
    notes = ProjectNotes(str(tmp_path), actor_id="a")
    notes.dir.mkdir(parents=True)
    (notes.dir / "a.md").write_text("---\nname: wrong\ndescription: first\nseen: 999\n---\nbody")
    (notes.dir / "b.md").write_text("---\ndescription: second\nupdated: 9999\n---\nbody")
    secret = tmp_path / "secret.md"
    secret.write_text("---\ndescription: do-not-import\n---\nsecret")
    (notes.dir / "link.md").symlink_to(secret)
    (notes.dir / "invalid.md").write_bytes(b"\xff")
    index = notes.index()
    assert index.index("a.md — first") < index.index("b.md — second")
    assert "999" not in index and "do-not-import" not in index and "wrong" not in index
    assert str(notes.dir) in index and "Bash" in index


@pytest.mark.asyncio
async def test_memory_disable_and_browser_isolation(bound_session):
    ctx = SimpleNamespace(id="a", extra={})
    assert await Agent(use_memory=False).memory_context(ctx) == ""
    browser = Agent(use_memory=True, capability_allowlists={"tool": ["done_tool"]})
    assert await browser.memory_context(ctx) == "" and browser.project_context(ctx) == ""


@pytest.mark.asyncio
async def test_container_does_not_get_unreachable_host_memory(bound_session, monkeypatch):
    monkeypatch.setenv("AGENTEVOLVER_EXEC_CONTAINER", "task-container")
    assert await Agent(use_memory=True).memory_context(SimpleNamespace(id="a", extra={})) == ""


@pytest.mark.asyncio
async def test_real_prompt_keeps_references_below_all_system_rules(monkeypatch):
    async def code_mode(self):
        return "Code-mode rules"
    async def memory(self, ctx):
        return "Learned claim"
    monkeypatch.setattr(Agent, "code_mode_section", code_mode)
    monkeypatch.setattr(Agent, "project_context", lambda self, ctx: "Project claim")
    monkeypatch.setattr(Agent, "memory_context", memory)
    monkeypatch.setattr(Agent, "inherited_context", lambda self, ctx: "Runtime rules")
    monkeypatch.setattr(Agent, "_parent_turns", lambda self: "")
    agent = Agent(system="Authoritative rules")
    messages = await agent.system_messages(None)
    assert [m.role for m in messages] == ["system", "system", "system", "user", "user"]
    ContextAssembler().build(Conversation(system=messages, task="current task"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "unsuccessful", "empty"])
async def test_named_prompt_failure_cannot_silently_fall_back(monkeypatch, failure):
    from agentevolver.prompt import prompt_manager

    async def render(*args, **kwargs):
        if failure == "exception":
            raise OSError("template unavailable")
        return SimpleNamespace(success=failure != "unsuccessful", message="template missing",
                               data={"messages": [HumanMessage(content="not system instructions")]})
    async def modules(*args):
        return {}
    monkeypatch.setattr(type(prompt_manager), "__call__", render)
    monkeypatch.setattr(Agent, "prompt_modules", modules)
    agent = Agent(prompt_name="required_rules", system="less restrictive fallback")
    with pytest.raises(RuntimeError, match="Required prompt"):
        await agent.system_messages(None)


def test_legacy_archive_preserves_full_text_and_distinct_sources(tmp_path):
    from agentevolver.memory.project import ProjectMemoryStore
    store = ProjectMemoryStore(str(tmp_path))
    store.path = tmp_path / "legacy.json"
    command = "python - <<'PY'\n    print('important indentation')\nPY"
    assert store.remember("observed_commands", command, source="trace:s:1")
    assert not store.remember("observed_commands", command, source="trace:s:1")
    assert not store.remember("observed_commands", command, source="trace:s:2")
    item = json.loads(store.path.read_text())["sections"]["observed_commands"][0]
    assert item["text"] == command
    assert item["sources"] == ["trace:s:1", "trace:s:2"]


def test_file_memory_bounds_only_numbered_display_records():
    from agentevolver.memory.default.file_system_memory import FileSystemMemory
    from agentevolver.memory.default.tiered import MemoryRecord, _SessionState
    memory = FileSystemMemory(recent_max=3, recent_fetch=2)
    state = _SessionState("s", "task", "", 2)
    for seq in range(30):
        memory._append_recent(state, MemoryRecord(ts="now", event="observed", seq=seq, detail="full" * 10000))
    assert [r.seq for r in state.recent] == [27, 28, 29]
    assert all(r.detail == "full" * 10000 for r in state.recent)
    assert "Full numbered events remain in Trace" in memory._render_history(state)


def test_memory_root_override_and_owner_isolation(bound_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTEVOLVER_MEMORY_ROOT", str(tmp_path / "persistent"))
    a = ProjectNotes("/workspace", project_id="project", actor_id="a")
    assert a.dir.is_relative_to(tmp_path / "persistent")
    path_manager.bind_session("other-owner", "s")
    b = ProjectNotes("/workspace", project_id="project", actor_id="a")
    assert a.dir != b.dir


@pytest.mark.asyncio
async def test_selected_memory_backend_controls_index(bound_session, monkeypatch):
    from agentevolver.memory.server import MemoryManagerServer
    from agentevolver.memory.types import Memory
    class Ranked(Memory):
        def index(self, notes):
            return "ranked index at " + str(notes.dir)
    async def get_info(self, name):
        assert name == "ranked"
        return SimpleNamespace(instance=Ranked())
    monkeypatch.setattr(MemoryManagerServer, "get_info", get_info)
    result = await Agent(use_memory=True, memory_name="ranked").memory_context(
        SimpleNamespace(id="a", extra={}),
    )
    assert result.startswith("ranked index at ")


@pytest.mark.asyncio
async def test_failed_backend_falls_back_to_full_file_index(bound_session, monkeypatch):
    from agentevolver.memory.server import MemoryManagerServer
    async def get_info(self, name):
        raise ValueError("backend unavailable")
    monkeypatch.setattr(MemoryManagerServer, "get_info", get_info)
    result = await Agent(use_memory=True, memory_name="broken").memory_context(
        SimpleNamespace(id="a", extra={}),
    )
    assert "Durable project memories live in" in result


@pytest.mark.asyncio
async def test_pressure_estimate_counts_live_attachments(monkeypatch):
    assembler = ContextAssembler(context_window=1000, retain_turns=1, compact_body_tokens=0,
                                  compact_after_turns=0, fold_at_pressure=0.5)
    c = Conversation()
    c.extend([AssistantMessage(content="old"), HumanMessage(content="next request"),
              AssistantMessage(content="recent")])
    attachment = HumanMessage(content="large current observation")
    def estimate(messages):
        return 900 if any(m.text == attachment.text for m in messages) else 10
    monkeypatch.setattr("agentevolver.model.pressure.estimate_tokens", estimate)
    assert assembler.fold_reason(c) == ""
    assert "capacity" in assembler.fold_reason(c, attachments=[attachment])


@pytest.mark.asyncio
async def test_opaque_only_compaction_does_not_discard_history(monkeypatch):
    agent = Agent(retain_recent_steps=1)
    agent.conversation.extend([AssistantMessage(content="old"), AssistantMessage(content="recent")])
    async def native(self, messages):
        return {"provider_state": {"responses": {"opaque": "x"}}}
    async def text(self, messages):
        return ""
    monkeypatch.setattr(Agent, "native_checkpoint", native)
    monkeypatch.setattr(Agent, "text_checkpoint", text)
    moved, _ = await agent._fold("test")
    assert not moved and agent.conversation.turns == 2 and agent.conversation.checkpoint is None


@pytest.mark.asyncio
async def test_compaction_keeps_complete_immutable_source_archives(tmp_path, monkeypatch):
    agent = Agent(retain_recent_steps=1, compact_verify=False)  # Archive mechanics, not semantic judgment.
    agent._thread_path = tmp_path / "thread.json"
    original = "Important original detail, never slice this. " * 300
    agent.conversation.extend([AssistantMessage(content=original), AssistantMessage(content="recent")])
    async def native(self, messages):
        return None
    async def text(self, messages):
        return "The task is ongoing; original evidence is available in the source snapshot."
    monkeypatch.setattr(Agent, "native_checkpoint", native)
    monkeypatch.setattr(Agent, "text_checkpoint", text)
    moved, _ = await agent._fold("test")
    assert moved
    archives = list(tmp_path.glob("thread/archive/*.json"))
    assert len(archives) == 1
    first = archives[0]
    source = first.read_bytes()
    assert json.loads(source)["items"][0]["content"] == original
    assert str(first) in agent.conversation.checkpoint.text
    agent.conversation.extend([AssistantMessage(content="Second body " * 600), AssistantMessage(content="latest")])
    moved, _ = await agent._fold("test")
    assert moved
    archives = list(tmp_path.glob("thread/archive/*.json"))
    assert len(archives) == 2 and first.read_bytes() == source
    second = next(path for path in archives if path != first)
    assert str(first) in json.loads(second.read_text())["checkpoint"]["content"]


@pytest.mark.asyncio
async def test_failed_compaction_archive_prevents_discard_and_model_call(tmp_path, monkeypatch):
    agent = Agent(retain_recent_steps=1)
    agent._thread_path = tmp_path / "thread.json"
    agent.conversation.extend([AssistantMessage(content="old"), AssistantMessage(content="recent")])
    before = [message.model_dump() for message in agent.conversation.items]
    def broken(*args, **kwargs):
        raise OSError("disk unavailable")
    async def unexpected(*args, **kwargs):
        pytest.fail("Must not spend tokens when source cannot be archived")
    monkeypatch.setattr(Conversation, "save", broken)
    monkeypatch.setattr(Agent, "native_checkpoint", unexpected)
    moved, detail = await agent._fold("test")
    assert not moved and "archive failed" in detail
    assert [message.model_dump() for message in agent.conversation.items] == before
