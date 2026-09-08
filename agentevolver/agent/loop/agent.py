"""The agent: a process whose main function is think-and-act.

Everything here is either the declaration of what this agent is, or the loop that runs
it. Prompt layout belongs to
:class:`~agentevolver.agent.context.assembler.ContextAssembler`, capability dispatch to
:class:`~agentevolver.agent.loop.router.ToolRouter`, execution order to
:class:`~agentevolver.agent.loop.executor.ActionExecutor`, and scheduling, suspension and
messaging to :mod:`agentevolver.runtime`. What is left is small enough to read at once.

The loop::

    for step in range(max_step):
        await proc.gate()          # safe point: signals, delivered messages
        note = await on_step(step) # optional policy and progress advice
        decision = await think()   # one model call
        if decision.final: return  # no tool call means the model answered
        results = await act(...)   # concurrent when provably safe

Most agents are pure declaration: a name, a prompt, a model, a step budget. Overriding
``think`` changes what the model sees; overriding ``on_event`` changes how a child's
report is handled. Overriding ``act`` or ``__call__`` should be rare — those are the same
for every agent, which is exactly what the previous base class could not say.

Imports reach into ``agent.context``'s submodules rather than its package, because the
package also exposes the agent registry, and the registry imports ``agent.types``, which
imports this module.
"""

from __future__ import annotations

import json
import asyncio
import copy
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.agent.context.assembler import ContextAssembler, context_assembler
from agentevolver.agent.context.conversation import Conversation
from agentevolver.agent.loop.decision import ActionCall, ActionResult, Decision
from agentevolver.agent.loop.executor import ActionExecutor
from agentevolver.agent.loop.router import CapabilityRouter, ToolRouter
from agentevolver.logger import logger
from agentevolver.message.types import HumanMessage, Message, SystemMessage
from agentevolver.response.types import Response, ResponseType

#: Consecutive failed model calls before the run gives up. A model that cannot be
#: reached produces no tool calls, so without this the loop retries an impossible
#: request until the step budget is gone and then reports the wrong cause.
MAX_MODEL_FAILURES = 3

#: Consecutive turns cut off at the output limit before giving up.
MAX_TRUNCATED_TURNS = 3

#: Characters of the parent's history a dispatched child inherits. A parent's history
#: has no natural size, and the most recent turns are the ones its decision rested on.
INHERITED_CONTEXT_MAX = 12_000


class Agent(BaseModel):
    """A think-and-act process.

    The fields are the declaration — what the registry reads and what a config file
    sets. Everything below them is the loop, which is the same for every agent.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    # -- identity, read by the registry -------------------------------------
    name: str = Field(default="agent", description="Registered name of this agent.")
    description: str = Field(
        default="A think-and-act agent.", description="What this agent is for."
    )
    metadata: Dict[str, Any] = Field(default={}, description="Free-form agent metadata.")
    version: str = Field(default="1.0.0", description="Version of the agent.")
    agent_type: str = Field(
        default="tool_calling", description="Execution contract: tool_calling|procedural."
    )

    # -- what it runs with --------------------------------------------------
    base_dir: str = Field(default="", description="This agent's own workspace.")
    model_name: str = Field(default="", description="Model route for every turn.")
    prompt_name: str = Field(
        default="", description="Prompt template rendered into the fixed layer."
    )
    system: str = Field(
        default="", description="Literal system prompt, used when no template is named."
    )
    max_step: int = Field(default=30, description="Steps before the run is cut off.")
    max_actions: int = Field(default=10, description="Actions accepted from one turn.")
    permission_mode: str = Field(
        default="workspace_write",
        description="read_only | workspace_write | danger_full_access",
    )
    enable_evolving: bool = Field(
        default=False, description="Whether this agent may be self-optimized."
    )
    include_agents: bool = Field(
        default=False,
        description="Project sub-agents into the roster. This is what makes an orchestrator.",
    )
    use_plan: bool = Field(
        default=False,
        description="Read and maintain this session's plan.md during automatic planning.",
    )
    defer_capabilities_after: int = Field(
        default=40, description="Roster size past which schemas are loaded on demand."
    )
    #: Scope this agent's roster per capability type. Absent = every loaded entity of
    #: that type; ``[]`` = none. A declaration rather than an override, because "which
    #: capabilities may I see" is a property of the agent, not a step in assembling a
    #: prompt — expressing it as a method is what let each actor acquire a different
    #: assembly path.
    capability_allowlists: Dict[str, List[str]] = Field(default={})
    #: Capability types whose evolved components join this agent's allowlist as they
    #: register, without anyone granting them.
    #:
    #: Only meaningful for an agent that HAS an allowlist; an absent allowlist already
    #: means "everything". For a restricted agent the allowlist is an isolation
    #: contract stated in code — a website visitor holds one tool and must not reach the
    #: workspace — and that contract cannot be widened by a component that did not exist
    #: when it was written.
    #:
    #: Declaring acceptance is what lets it be, and it is declared here rather than
    #: granted at runtime because a grant needs an edge between the granter and the
    #: granted. A publisher does not know its subscribers, and two peers have no edge at
    #: all, so per-process granting cannot reach the topologies that need it most.
    #: Declaring in advance needs no edge and stays reviewable: this line, in this file.
    #:
    #: What is accepted is PROVISIONAL. A component is live the moment it registers —
    #: there is no candidate pool and no promotion step — so acceptance hands over
    #: something that has not been evaluated yet, and may still be rolled back. That is
    #: the framework's register-is-live contract rather than a gap here, but it means
    #: this field says "I will work with what this run produces", not "I will work with
    #: what this run has proven".
    #:
    #: A name that later unloads stays in the list and is simply skipped: a roster is
    #: built from what is registered, so a dead name reaches no model.
    accepts_evolved: List[str] = Field(default=[])

    # -- route capabilities this agent opts into. Declared per agent because they change
    # -- what a turn can do, not merely how fast it runs; a route that has not declared
    # -- the same capability ignores them.
    programmatic_tool_calling: bool = Field(
        default=True, description="Let a capable route run eligible read-only tools itself."
    )
    native_multi_agent: bool = Field(
        default=False, description="Use a provider's own orchestration when it has one."
    )
    async_tool_calling: bool = Field(
        default=False, description="Overlap explicitly safe reads with native model generation."
    )
    max_concurrent_subagents: int = Field(
        default=3, description="Ceiling for provider-side sub-agent fan-out."
    )
    compact_output_tokens: int = Field(
        default=2048, description="Size budget for a checkpoint summary."
    )
    compact_verify: bool = Field(default=True, description="Audit semantic coverage before replacing history.")
    capability_schema_tokens: int = Field(
        default=8000, ge=0,
        description="Soft initial schema token budget; core and discovered capabilities stay visible. 0 disables.",
    )
    # The fold policy, declared rather than inherited. These were accepted by
    # `extra="allow"` and never read: the agent took the shared module-level assembler
    # and rebuilt it only when `compact_output_tokens` differed, so a config asking to
    # fold at 60k folded at the shared default of 100k and nothing said otherwise.
    # `None` means "whatever the shared assembler was built with".
    compact_body_tokens: Optional[int] = Field(
        default=None, description="Fold once history's body exceeds this many tokens."
    )
    compact_input_tokens: Optional[int] = Field(
        default=None, ge=0,
        description="Full input compaction trigger, including cached tokens and schemas; 0 disables."
    )
    retain_recent_steps: Optional[int] = Field(
        default=None, description="Whole turns kept verbatim after a fold."
    )
    compact_after_steps: Optional[int] = Field(
        default=None, description="Fold once history holds this many turns."
    )
    fold_at_pressure: Optional[float] = Field(
        default=None, description="Fold once the request fills this share of the window."
    )
    memory_name: str = Field(
        default="", description="Memory backend this agent reads and writes."
    )
    use_memory: bool = Field(
        default=False, description="Whether this run keeps durable memory at all."
    )
    max_token: Optional[int] = Field(
        default=None, gt=0, description="Cumulative input and output token budget per assignment."
    )
    allow_token_budget_override: bool = Field(
        default=True, description="Allow a delegating agent to narrow this role's token budget."
    )
    timeout: Optional[float] = Field(
        default=None, gt=0, description="Wall-clock budget per assignment, or None."
    )

    #: Environments this agent acts in. Read before every step, so what the agent is
    #: told about a machine or a page is what is true now.
    env_names: List[str] = Field(default=[])

    def __init__(self, base_dir: str = "", **kwargs: Any) -> None:
        # `env_name` is the older spelling, and three config files on disk still use it.
        # Folded here rather than in each actor: a value a config sets must not be
        # silently ignored, and it was — an actor that exposed `env_name` as a property
        # swallowed it, so the run mounted whatever the default happened to be.
        legacy_name = kwargs.pop("env_name", None)
        if legacy_name and not kwargs.get("env_names"):
            kwargs["env_names"] = [str(legacy_name)]
        super().__init__(base_dir=base_dir, **kwargs)

        # -- collaborators. Not fields: they are wiring, not declaration, and a
        # -- subclass or a test replaces them by assignment.
        self.router: ToolRouter = kwargs.get("router") or CapabilityRouter(
            include_agents=self.include_agents
        )
        self.assembler: ContextAssembler = kwargs.get("assembler") or context_assembler
        # One policy, not two: whatever this agent declares wins over the shared
        # assembler's default, and an undeclared field keeps that default. Rebuilt only
        # when something actually differs, so agents that declare nothing keep sharing
        # one object.
        declared = {
            "retain_turns": self.retain_recent_steps,
            "compact_after_turns": self.compact_after_steps,
            "compact_body_tokens": self.compact_body_tokens,
            "compact_input_tokens": self.compact_input_tokens,
            "fold_at_pressure": self.fold_at_pressure,
            "compact_output_tokens": self.compact_output_tokens,
        }
        settings = {
            "retain_turns": self.assembler.retain_turns,
            "compact_after_turns": self.assembler.compact_after_turns,
            "compact_body_tokens": self.assembler.compact_body_tokens,
            "compact_input_tokens": self.assembler.compact_input_tokens,
            "fold_at_pressure": self.assembler.fold_at_pressure,
            "compact_output_tokens": self.assembler.compact_output_tokens,
        }
        overrides = {
            key: value for key, value in declared.items()
            if value is not None and value != settings[key]
        }
        if overrides:
            self.assembler = ContextAssembler(
                **{**settings, **overrides},
                max_folds=self.assembler.max_folds,
                context_window=self.assembler.context_window,
            )
        self.executor = ActionExecutor(self.router)
        #: Step middleware. Each is ``async (agent, step) -> str``; what it returns rides
        #: in this step's live layer. Mandatory resource limits are enforced by the loop.
        self.middleware: List[Any] = list(kwargs.get("middleware") or ())

        # -- the process handle, bound by the kernel at spawn. The agent's only door
        # -- back into the runtime: gate(), recv(), report(), ask_parent().
        self.proc: Any = None

        # -- per-run state
        self.conversation = Conversation()
        self.ctx: Any = None
        self.task: str = ""
        self.step: int = 0
        self._routing: Dict[str, Any] = {}
        # Reuse the system prompt's scoped policy decision in live planning. Target
        # mutability is independent of whether this agent can run an evolution loop.
        self._evolution_policy_enabled = False
        self._notes: List[str] = []
        self._model_failures = 0
        self._truncated_turns = 0
        #: Folds attempted this run, for the fold number the compaction events report.
        self._folds = 0
        #: Consecutive folds that reclaimed nothing — the number the budget is against.
        #:
        #: The bound exists because a history that cannot shrink would otherwise be asked
        #: once per step for the rest of the run, producing the same request and the same
        #: refusal each time. Counting *every* fold against it made the guard fire on the
        #: opposite case: a 90-step browser agent folding every few steps is folding
        #: working, and it spent all 32 in one dispatch, after which every remaining step
        #: refused to fold, overflowed, and failed — twenty-two minutes of a run that then
        #: reported a rejected release, because a long run is exactly what folding is for.
        self._unproductive_folds = 0
        self._input_token_ratio = 1.0
        #: Reported tokens waiting to be forwarded to optional constraint hooks.
        #: Held rather than counted inline because the guard runs once per step and must
        #: see each turn's spend exactly once.
        self._unspent_tokens = 0
        self._used_tokens = 0
        self._started_at = 0.0
        #: How many consecutive completion attempts a single blocker has refused. A
        #: contract gate that never opens is a protocol fault, and a run that cannot
        #: name it as one spends its whole budget retrying: measured at 58 of 133 steps
        #: alternating deploy and done against a gate whose input could not change.
        self._thread_process: str = ""
        self._thread_path: Any = None

    async def initialize(self) -> None:
        """Called once by the registry after construction."""

    def fresh(self) -> "Agent":
        """A new instance of this agent from the same declaration.

        The registry holds one object per name — that is the *program*. Running it
        produces a *process*, and a process needs its own conversation and its own step
        counter. Sharing one object between concurrent runs is what forced the previous
        design to key per-run state by ref name inside the agent itself.
        """
        declared = {
            name: getattr(self, name)
            for name in type(self).model_fields
            if name != "base_dir"
        }
        clone = type(self)(base_dir=self.base_dir, **declared)
        clone.middleware = copy.deepcopy(self.middleware)
        clone.assembler = self.assembler
        return clone

    # ==================================================================
    # main()
    # ==================================================================

    async def __call__(
        self,
        task: str = "",
        files: Optional[Sequence[str]] = None,
        ctx: Any = None,
        **kwargs: Any,
    ) -> Response:
        """Run one assignment to completion and return its result."""
        from agentevolver.agent.loop.guards import BudgetExhausted

        deadline = asyncio.timeout(self.timeout)
        try:
            async with deadline:
                try:
                    response = await self._run(task, files, ctx, **kwargs)
                except BudgetExhausted as spent:
                    response = self._failed(f"Stopped by a resource budget: {spent}")
                return await self.finalize(response)
        except TimeoutError:
            if not deadline.expired():
                raise  # An unrelated tool timeout is not the assignment deadline.
            return self._failed(f"Wall-clock budget reached ({self.timeout}s)")
        finally:
            self.save_thread()

    async def finalize(self, response: Response) -> Response:
        """Last look at the run's own result, before the caller gets it.

        Distinct from ``on_exit``, which the kernel calls with a status and cannot change
        anything: an agent that has to act on *what it produced* — registering a component
        it just generated, for instance — needs the Response itself.
        """
        return response

    async def _run(
        self,
        task: str,
        files: Optional[Sequence[str]],
        ctx: Any,
        **kwargs: Any,
    ) -> Response:
        self.ctx = ctx
        self._started_at = time.time()
        self._used_tokens = 0
        self._unspent_tokens = 0
        self.step = 0
        task, files = await self.prepare_task(task, list(files or ()), ctx)
        self.task = self._task_text(task, files)
        proc = self.proc
        continuing = bool(proc is not None and proc.resident
                          and self._thread_process == proc.pid)
        if not continuing:
            self._thread_path = None
            self._thread_process = ""
            if proc is not None:
                from agentevolver.paths import P, path_manager
                if path_manager.session is not None:
                    self._thread_path = path_manager.get(
                        P.SESSION_AGENT_CONTEXT, thread_id=proc.thread_id,
                    )
            if proc is not None and proc.resume_thread:
                if self._thread_path is None:
                    raise ValueError("Thread resume requires a bound session")
                self.conversation = Conversation.load(
                    self._thread_path, model=self.model_name, agent=self.name,
                )
                # Rules may have changed since the host restarted. Refresh once,
                # never on each ordinary subscriber wake.
                self.conversation.set_system(await self.system_messages(ctx))
                continuing = True
                proc.resume_thread = False
            else:
                self.conversation = Conversation(task=self.task)
                self.conversation.set_system(await self.system_messages(ctx))
            self._thread_process = proc.pid if proc is not None else ""
        if continuing:
            # Runtime repeats a subscriber's identity for stateless agents. The
            # identity already lives in this thread's task anchor.
            incoming = self.task
            brief = str(getattr(proc, "brief", "") or "")
            if brief and incoming.startswith(brief + "\n\n"):
                incoming = incoming[len(brief) + 2:]
            self.conversation.note(incoming)
        self._notes = []
        self._model_failures = 0
        self._truncated_turns = 0
        self._folds = 0
        self._unproductive_folds = 0
        self._input_token_ratio = 1.0
        self._unspent_tokens = 0
        # Per-run, like everything above it: a resident process runs many turns, and a
        # gate that blocked the previous one must not count against this one.
        await self._emit_start()

        for step in range(self.max_step):
            self.step = step
            self.check_budget()
            if self.proc is not None:
                # The safe point. Signals apply here and messages are delivered here, so
                # a suspend or a child's report never lands mid-turn.
                await self.proc.gate()

            from agentevolver.agent.loop.guards import BudgetExhausted
            from agentevolver.hook.events import HookEvent

            # Optional middleware may also end the run —
            # so PRE_STEP is announced only once this step is actually going to happen.
            # Announced before the gate, an exhausted budget produced a PRE_STEP with no
            # POST_STEP and an observer had recorded a step that never ran. The action
            # level had the order right all along: gate, then announce.
            try:
                live = await self._live_blocks(step)
            except BudgetExhausted as spent:
                return self._failed(f"Stopped by a resource budget: {spent}")
            await self._events.emit(
                HookEvent.PRE_STEP,
                {"agent_name": self.name, "step_number": step, **self._identity()},
                ctx=ctx,
            )
            await self._fold_if_needed(live)
            self.check_budget()

            decision = await self.think(step, live)
            self.count_usage(decision.usage)
            try:
                self.check_budget()
            except BudgetExhausted as spent:
                # Keep paid-for usage and a closed protocol turn, but run no new actions.
                skipped = [getattr(self, "_early_results", {}).get(call.id) or ActionResult(
                    call=call, error=f"Not executed: {spent}",
                ) for call in decision.calls]
                if not decision.error and not decision.truncated:
                    self.conversation.add_turn(
                        decision.as_assistant(), [result.as_message() for result in skipped],
                    )
                await self._post_step(step, decision, skipped)
                raise

            if decision.error:
                # An overflow is the one model error the run can act on: the request was
                # never sent, and the next rebuild is smaller. Deliberately not counted
                # toward the give-up threshold.
                if decision.overflowed and await self.make_room():
                    continue
                verdict = self._handle_model_error(decision)
                if verdict is not None:
                    return verdict
                continue
            if decision.truncated:
                verdict = self._handle_truncation()
                if verdict is not None:
                    return verdict
                continue

            self._model_failures = 0
            self._truncated_turns = 0

            if decision.final:
                self.conversation.append(decision.as_assistant())
                # Closed like any other step. A text-only ending ran a whole step —
                # a model call, a recorded turn — and returning here without POST_STEP
                # left the last turn of every such run out of the trajectory.
                await self._post_step(step, decision, ())
                return self._finish(decision.text or decision.reasoning)

            if len(decision.calls) > self.max_actions:
                logger.warning(
                    f"| ✂️ [{self.name}] {len(decision.calls)} calls in one turn; "
                    f"running the first {self.max_actions}"
                )
                # Keep unanswered calls in the protocol; act returns explicit skips.

            results = await self.act(decision)
            self.conversation.add_turn(
                decision.as_assistant(), [result.as_message() for result in results]
            )
            await self._post_step(step, decision, results)

            finished = next((result for result in results if result.final), None)
            if finished is not None:
                return self._finish(finished.output)

            self._notes.extend(
                f"Action {result.call.name!r} failed: {result.error}"
                for result in results if result.error
            )

        return self._exhausted()

    # ==================================================================
    # The two halves of a step
    # ==================================================================

    async def think(self, step: int, live: Sequence[str] = ()) -> Decision:
        """One model call. The main seam: override to change what the model sees."""
        from agentevolver.model import model_manager
        from agentevolver.model.types import accumulate_stream

        tools, routing = await self.router.schemas(self, self.ctx)
        self._routing = routing
        messages = self.assembler.build(
            self.conversation, live=live, attachments=self.attachments()
        )
        request = self.request_input(messages, tools)
        estimated_input = 0
        if self.assembler.compact_input_tokens and model_manager.get_model_config(self.model_name) is not None:
            estimated_input = model_manager.measure(self.model_name, request)["estimated_tokens_after"]

        logger.info(f"| 🔄 [{self.name}] step {step + 1}/{self.max_step}")
        self._early_results = {}
        try:
            stream = model_manager.stream(
                name=self.model_name, input=request, ctx=self.ctx,
            )
            if self.async_tool_calling:
                eligible = {tool.name for tool in tools
                            if (getattr(tool, "metadata", None) or {}).get("programmatic")}
                accumulated, self._early_results = await self.executor.consume(
                    stream, agent=self, ctx=self.ctx, routing=routing, eligible=eligible,
                    execution=self._execution_context(),
                )
            else:
                accumulated = await accumulate_stream(stream)
        except Exception as error:  # noqa: BLE001 - a model fault is a decision, not a crash
            from agentevolver.model.pressure import ContextOverflowError
            from agentevolver.runtime.errors import BudgetExhausted

            if isinstance(error, BudgetExhausted):
                raise

            logger.error(f"| ❌ [{self.name}] model call failed: {error}")
            return Decision(
                error=f"{type(error).__name__}: {error}",
                overflowed=isinstance(error, ContextOverflowError),
            )
        decision = self._decide(accumulated)
        if estimated_input and not decision.error:
            from agentevolver.model.types import TokenUsage

            usage = TokenUsage.from_raw(decision.usage)
            if usage is not None:
                full_input = usage.total - usage.output_tokens
                if full_input > 0:
                    self._input_token_ratio = max(1.0, full_input / estimated_input)
        return decision

    def trace_coordinates(self) -> Dict[str, Any]:
        """Who this request belongs to, for anything that reaches a model on our behalf.

        Not only the agent's own turn: compaction summarises and audits through the model
        too, and those calls carried no coordinates at all. Their trace rows arrived with
        no agent, no task and no step, so the run dashboard listed them as ``unknown``
        beside the very agent whose history was being folded — 76 rows in one demo, and
        the usage they reported rolled up to nobody.
        """
        return {
            "task_id": getattr(self.proc, "pid", "") or "",
            "agent_name": self.name,
            "step_number": self.step,
        }

    def request_input(self, messages: Sequence[Message], tools: Any) -> Dict[str, Any]:
        """Everything one request carries beyond its messages.

        Each field has a consumer that silently degrades without it, which is why they
        are assembled in one named place rather than inline:

        ``trace_context``            joins the request snapshot to this agent's step, so
                                     a trajectory cites an id instead of guessing from
                                     timestamps.
        ``compaction_policy``        lets the model layer negotiate a route's *native*
                                     compaction. Omitted, native compaction never
                                     engages and the thresholds fall back to defaults.
        ``runtime_features``         programmatic tool calling and native multi-agent,
                                     both opt-in per route.
        ``reasoning_effort``         a score lever, not a cost knob.
        ``trace_integrity_profile``  fail-closed durability for training / high-risk runs.
        """
        extra = getattr(self.ctx, "extra", None) or {}
        payload: Dict[str, Any] = {
            "messages": list(messages),
            "tools": tools,
            "trace_context": self.trace_coordinates(),
            "compaction_policy": self.assembler.compaction_policy(),
            "runtime_features": {
                "programmatic_tool_calling": self.programmatic_tool_calling,
                "async_tool_calling": self.async_tool_calling,
                # Native multi-agent is an orchestration backend, never a way to turn a
                # leaf actor into an orchestrator by accident.
                "multi_agent": self.native_multi_agent and self.include_agents,
                "max_concurrent_subagents": self.max_concurrent_subagents,
            },
        }
        if extra.get("child_reasoning_effort"):
            payload["reasoning_effort"] = extra["child_reasoning_effort"]
        # `trace_integrity_profile` used to be forwarded from `extra` here. It is a
        # configured setting — `configs/base.py` declares it and `config/validate.py`
        # checks it — and nothing ever wrote it into a context, so this only ever
        # forwarded nothing while implying a per-run override existed. The model layer
        # reads the configured value directly.
        return payload

    async def act(self, decision: Decision) -> List[ActionResult]:
        """Run this turn's actions. Rarely worth overriding."""
        results = await self.executor.run(
            decision.calls[:self.max_actions],
            agent=self,
            ctx=self.ctx,
            routing=self._routing,
            execution=self._execution_context(),
            prepared=getattr(self, "_early_results", {}),
        )
        results.extend(ActionResult(call=call, error="Not executed: per-step action limit exceeded")
                       for call in decision.calls[self.max_actions:])
        self._early_results = {}
        return results

    # ==================================================================
    # Seams
    # ==================================================================

    async def code_mode_section(self) -> str:
        """The calling convention for a program, when this agent can run one.

        Rendered from the same roster the tool schemas come from, and only when the
        program transport is actually in it: an agent that cannot run a program has no
        use for a convention, and the block would be prompt it pays for every step.
        """
        from agentevolver.code import BATCH_CALL_TOOL
        from agentevolver.tool import tool_manager
        from agentevolver.tool.default.execution.sdk import code_mode_section

        allowed = (getattr(self.ctx, "extra", None) or {}).get(
            "tool_allowlist", self.capability_allowlists.get("tool"),
        )
        names = list(allowed) if allowed is not None else await tool_manager.list()
        if BATCH_CALL_TOOL not in names:
            return ""
        # Native schemas already describe the arguments. A second fixed SDK becomes
        # stale when discovery or permissions change; the bridge uses this turn's roster.
        return code_mode_section()

    async def prepare_task(
        self, task: str, files: List[str], ctx: Any, *,
        private_roles: Sequence[str] = (),
        manifest_updates: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, List[str]]:
        """What this run is actually about, before the first prompt is built.

        Launchers declare roles and obligations in the task manifest. This common
        lifecycle resolves attachments, registers subscribers through runtime and
        projects public input. Domain tools interpret their own task declarations.
        Domain actors need no override for this flow.
        """
        from agentevolver.task.context import (
            bind_manifest,
            parse_manifest,
            public_manifest,
            subscriber_declarations,
        )

        bound = bind_manifest(task, files)
        if bound is None:
            return task, files
        manifest = bound[2]
        declared_private = manifest.get("private_attachment_roles", [])
        if not isinstance(declared_private, list) or any(not isinstance(role, str) for role in declared_private):
            raise ValueError("private_attachment_roles must be a list of roles")
        updates = dict(manifest_updates or {})
        declarations = subscriber_declarations(manifest)
        if declarations:
            from agentevolver.runtime import kernel

            jobs = await kernel.bootstrap_subscribers(declarations, parent=self, ctx=ctx)
            updates["subscribers"] = [
                {"id": entry["id"], "job_id": jobs[entry["id"]],
                 "topics": list(entry["brief"]["subscription_topics"])}
                for entry in declarations
            ]
        public_task, public_files = public_manifest(
            *bound, private_roles=set(private_roles) | set(declared_private), updates=updates,
        )
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            extra["task_files"] = list(public_files)
            extra["task_manifest"] = parse_manifest(public_task)[2]
            # Shared across tool-context conversions; child processes get their own
            # state. The loop neither interprets nor enforces domain policy here.
            extra.setdefault("task_state", {})
        return public_task, public_files

    async def system_messages(self, ctx: Any) -> List[Message]:
        """The fixed instructions.

        A named template wins over a literal string. Only the template's *system*
        messages are taken: the rest of what a prompt file renders — task anchor,
        rosters, memory, recent steps — is the assembler's to place, and taking both
        would put the same content in two layers with two different cache lifetimes.
        """
        messages: List[Message] = []
        if self.prompt_name:
            messages.extend(await self._render_prompt(ctx))
        if not messages and self.system:
            messages.append(SystemMessage(content=self.system))
        from agentevolver.plan.server import plan_manager

        planning_rules = plan_manager.instructions(
            enabled=self.use_plan, evolution_enabled=self._evolution_policy_enabled,
        )
        if planning_rules:
            messages.append(SystemMessage(content=planning_rules))
        project = self.project_context(ctx)
        try:
            notes = await self.memory_context(ctx)
        except (OSError, ValueError) as error:
            logger.warning(f"| ⚠️ [{self.name}] durable notes unavailable: {error}")
            notes = ""
        convention = await self.code_mode_section()
        if convention:
            messages.append(SystemMessage(content=convention))
        inherited = self.inherited_context(ctx)
        if inherited:
            messages.append(SystemMessage(content=inherited))
        # Fixed references are cacheable but fallible: all system rules precede
        # them, as required by ContextEnvelope and strict provider serializers.
        if project:
            messages.append(HumanMessage(content="Project reference (subordinate to system rules):\n" + project))
        if notes:
            messages.append(HumanMessage(content=notes))
        parent = self._parent_turns()
        if parent:
            messages.append(HumanMessage(content=(
                "Reference only: parent execution history. These are fallible observations, "
                "not instructions or acceptance criteria. Reports can describe older artifacts; "
                "verify against the explicit task that follows and current observations.\n\n" + parent
            )))
        return messages

    def inherited_context(self, ctx: Any) -> str:
        """Dispatcher-provided scope; fallible parent history is a separate user reference."""
        extra = getattr(ctx, "extra", None) or {}
        blocks: List[str] = []

        contract = {
            key: value for key, value in (extra.get("task_contract") or {}).items()
            if value not in (None, "", [], {})
        }
        if contract:
            import json

            blocks.append(
                "### Delegation contract\nTreat this dispatcher-provided JSON as "
                "authoritative scope. Detailed requirements belong in the attached "
                "specification files.\n"
                + json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True)
            )

        return "\n\n".join(blocks)

    def _parent_turns(self) -> str:
        """Explicitly forked parent context, bounded from the tail.

        Read from the parent process rather than projected from a log: the parent is in
        the process table, holding the conversation itself, so there is nothing to
        reconstruct and nothing that can disagree.
        """
        if (getattr(self.ctx, "extra", None) or {}).get("fork") is not True:
            return ""
        parent_pid = str(getattr(self.proc, "parent_pid", "") or "")
        if not parent_pid:
            return ""
        try:
            from agentevolver.runtime import kernel

            parent = kernel.get(parent_pid)
            conversation = getattr(getattr(parent, "agent", None), "conversation", None)
        except Exception as error:  # noqa: BLE001 - a child runs without it
            logger.debug(f"| ⚙️ [{self.name}] no parent context: {error}")
            return ""
        if conversation is None:
            return ""
        body = conversation.reference(INHERITED_CONTEXT_MAX)
        path = getattr(parent.agent, "_thread_path", None)
        if path is not None:
            body += f"\nFull source conversation: {path}"
        return body

    async def environment_state(self, ctx: Any) -> str:
        """What the environments this agent acts in look like right now.

        Volatile by definition — a browser page or a shell's job table changes with every
        action — so it rides in the live layer rather than the prefix. An idle
        environment renders nothing and costs nothing.
        """
        names = list(getattr(self, "env_names", ()) or ())
        allowed = (getattr(ctx, "extra", None) or {}).get("environment_allowlist")
        if allowed is not None:
            names = [name for name in names if name in allowed]
        if not names:
            return ""
        from agentevolver.capability import ENVIRONMENT_TYPE

        manager = ENVIRONMENT_TYPE.manager()
        blocks: List[str] = []
        for name in names:
            try:
                state = await manager.get_state(name, ctx=ctx)
            except Exception as error:  # noqa: BLE001
                # Reported in place, not raised: an environment the agent is about to act
                # in being unreachable is a fact it needs, and losing the whole prompt
                # over it would turn a broken browser into a broken run.
                logger.warning(f"| ⚠️ [{self.name}] could not read {name} state: {error}")
                blocks.append(f"<environment name=\"{name}\">unavailable — {error}</environment>")
                continue
            body = state.get("state") if isinstance(state, dict) else state
            if body:
                blocks.append(f"<environment name=\"{name}\">\n{body}\n</environment>")
        return "\n\n".join(blocks)

    def project_context(self, ctx: Any) -> str:
        """Project references, captured in the fixed user context at thread start."""
        from agentevolver.agent.context import load_project_context
        from agentevolver.paths import path_manager

        extra = getattr(ctx, "extra", None) or {}
        allowed = extra.get("tool_allowlist", self.capability_allowlists.get("tool"))
        if allowed is not None and "bash_tool" not in allowed:
            # Browser-only participants must not learn the product's source files
            # or another actor's notes through an otherwise hidden prompt path.
            return ""
        roots = path_manager.session_roots() or {}
        workspace = str(roots.get("workspace", "") or self.base_dir)
        if not workspace:
            return ""
        try:
            return load_project_context(
                workspace,
                active_paths=extra.get("task_files") or (),
                source_workspace=extra.get("source_workspace"),
                include_memory=self.use_memory,
            )
        except Exception as error:  # noqa: BLE001 - a missing AGENTS.md is not a failure
            logger.debug(f"| ⚙️ [{self.name}] project context unavailable: {error}")
            return ""

    async def memory_context(self, ctx: Any) -> str:
        """An actor's notes index in this session; bodies are read with existing Bash."""
        import os

        from agentevolver.memory import memory_manager
        from agentevolver.memory.project import ProjectNotes
        from agentevolver.paths import path_manager

        extra = getattr(ctx, "extra", None) or {}
        allowed = extra.get("tool_allowlist", self.capability_allowlists.get("tool"))
        if not self.use_memory or (allowed is not None and "bash_tool" not in allowed):
            return ""
        # A host path is not a container path. Benchmark shells currently mount only
        # the task checkout; do not advertise unreachable host memory or share cases.
        if os.environ.get("AGENTEVOLVER_EXEC_CONTAINER"):
            return ""
        roots = path_manager.session_roots()
        workspace = str(roots.get("workspace") or self.base_dir)
        actor = str(extra.get("memory_actor_id") or (
            getattr(ctx, "id", "") if getattr(ctx, "parent_session_id", "") else self.name
        ) or self.name)
        notes = ProjectNotes(workspace, actor_id=actor)
        if notes.dir is None:
            return ""
        notes.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return await memory_manager.index(self.memory_name, notes)

    async def _render_prompt(self, ctx: Any) -> List[Message]:
        """Render the named prompt template and keep its system half."""
        from agentevolver.prompt import prompt_manager

        try:
            response = await prompt_manager(
                name=self.prompt_name,
                input={
                    "system_modules": await self.prompt_modules(ctx),
                    "agent_modules": {},
                },
            )
        except Exception as error:  # noqa: BLE001 - preserve the renderer's cause
            raise RuntimeError(f"Required prompt {self.prompt_name!r} could not be rendered: {error}") from error
        if not getattr(response, "success", False):
            raise RuntimeError(f"Required prompt {self.prompt_name!r} failed: {response.message}")
        messages = (response.data or {}).get("messages") or []
        system = [message for message in messages if getattr(message, "role", "") == "system"]
        if not any(message.text.strip() for message in system):
            raise RuntimeError(f"Required prompt {self.prompt_name!r} returned no system instructions")
        return system

    async def prompt_modules(self, ctx: Any) -> Dict[str, Any]:
        """Values a prompt template may interpolate. Override to add more."""
        from agentevolver.paths import path_manager

        # Target mutability (enable_evolving) is not permission to run evolution.
        # Use the scoped roster, including deferred capabilities, once per assignment.
        evolution_enabled = False
        if self.include_agents and self.permission_mode != "read_only":
            from agentevolver.agent.context.capabilities import catalog

            _, routing = await self.router.schemas(self, ctx)
            routes = {tuple(route) for route in routing.values()}
            routes.update(tuple(item.get("route") or ()) for item in catalog(ctx, self.name)
                          if isinstance(item, dict))
            # What the policy needs is the means to inspect, to record an adoption, and the
            # skill that says how — the work itself is this agent's, not a roster of workers
            # it must be able to dispatch. Requiring those workers here meant removing one of
            # them switched the policy off silently, which is not a decision a missing roster
            # entry should be making.
            required = {
                ("tool", "adoption_tool"), ("tool", "inspect_tool"),
                ("skill", "self_evolving_skill"),
            }
            evolution_enabled = required.issubset(routes)
        self._evolution_policy_enabled = evolution_enabled
        roots = path_manager.session_roots() or {}
        return {
            "evolution_enabled": evolution_enabled,
            "max_actions": self.max_actions,
            "workspace_root": str(path_manager.execution_path(roots.get("workspace", "") or self.base_dir)),
            "project_root": str(roots.get("project", "")),
            "package_root": str(roots.get("package", "")),
            "extension_root": str(roots.get("extension", "")),
            "shared_extension_root": str(roots.get("shared_extension", "")),
            "log_root": str(roots.get("log", "")),
        }

    def attachments(self) -> List[Message]:
        """Images this run has read, re-sent with every request.

        Held by the attachment manager for the session rather than by the conversation:
        they are bytes a tool produced, not a turn anyone took, and replaying them as
        history would put a picture where an assistant message belongs.
        """
        session = str(getattr(self.ctx, "id", "") or "")
        if not session:
            return []
        try:
            from agentevolver.attachment import attachment_manager
            from agentevolver.message.types import ContentPartText, HumanMessage

            live = attachment_manager.live(session)
            if not live:
                return []
            parts: List[Any] = [
                ContentPartText(text="Images you read, in the order you read them:")
            ]
            for attachment in live:
                parts.append(ContentPartText(text=f"\n[{attachment.source_path}]"))
                parts.append(attachment_manager.content_part(attachment))
            return [HumanMessage(content=parts)]
        except Exception as error:  # noqa: BLE001 - no images is the normal case
            logger.debug(f"| ⚙️ [{self.name}] attachments unavailable: {error}")
            return []

    def allow_read_only(self, name: str, args: Dict[str, Any]) -> bool:
        """Whether a read_only agent may make this one otherwise-refused call.

        Narrow by construction: an evaluator has to be able to record its own verdict,
        and that is a mutating tool. Default is no, so an agent opts in per tool rather
        than the permission mode having exceptions built into it.
        """
        return False

    async def on_step(self, step: int) -> str:
        """Run the middleware chain; its notes ride in this step's live layer."""
        from agentevolver.agent.loop.guards import BudgetExhausted

        notes: List[str] = []
        for middleware in self.middleware:
            try:
                note = await middleware(self, step)
            except BudgetExhausted:
                # The one thing a guard is allowed to end the run with. Swallowed here it
                # would be logged as "middleware failed" and the budget would never stop
                # anything — the exact failure this catch-all exists to prevent elsewhere.
                raise
            except Exception as error:  # noqa: BLE001 - a broken guard must not end a run
                logger.warning(f"| ⚠️ [{self.name}] middleware failed: {error}")
                continue
            if note:
                notes.append(str(note))
        return "\n\n".join(notes)

    async def on_event(self, envelope: Any, proc: Any) -> None:
        """A message arrived at a safe point. By default it becomes context.

        This is the whole of an orchestrator's inbound half: a child's report, a
        published event or a reply is read on the next step like anything else the agent
        learned. Override to act on one immediately.
        """
        self.conversation.note(
            self.render_event(envelope), event_id=str(getattr(envelope, "id", "") or ""),
        )
        self.save_thread()

    def render_event(self, envelope: Any) -> str:
        """How a delivered message reads in the prompt."""
        text = getattr(envelope, "text", "") or ""
        if getattr(envelope, "final", False):
            return (
                f'<child-finished pid="{envelope.sender}" '
                f'status="{envelope.exit_status}">\n{text}\n</child-finished>'
            )
        if getattr(envelope, "blocked", False):
            # The pid is the reply address, so it is named in the instruction and not
            # only in the attribute: a parent that has to guess the `task_id` answers
            # the wrong child, or none, and the child waits out its timeout instead.
            return (
                f'<child-blocked pid="{envelope.sender}">\n{text}\n</child-blocked>\n'
                f'Answer it this turn with reply_tool(task_id="{envelope.sender}"), '
                "either with concrete guidance or telling it to stop and report. It is "
                "parked until you do."
            )
        if getattr(envelope, "topic", ""):
            return (
                f'<event topic="{envelope.topic}" type="{envelope.event_type}">\n'
                f"{envelope.payload}\n</event>"
            )
        return f"<message>\n{text or envelope.summary()}\n</message>"

    # -- kernel lifecycle hooks; all optional, all no-ops by default ---------

    async def on_start(self, task: str, proc: Any) -> None:
        """Once, before the first turn."""

    async def on_suspend(self) -> None:
        """Held at a safe point. Release volatile resources here."""

    async def on_resume(self) -> None:
        """Released. Rebuild what ``on_suspend`` let go."""

    async def on_land(self, reason: str) -> None:
        """Landing: the one chance to persist a partial result.

        Every graceful ending runs this, a normal finish included — it is not the
        stop *signal*'s handler. Named for the landing rather than for the signal
        because `HookEvent.ON_STOP` already means the other thing: the agent decided
        it was done. One name for two opposite causes is how an observer ends up
        recording a cancellation as a completion.
        """

    async def on_exit(self, status: Any) -> None:
        """Before the process is marked exited. Release what the run left running.

        A backgrounded command, a PTY shell and an indexing language server outlive the
        step that started them by design; nothing outlived the *run* on purpose, and the
        only reaper was an ``atexit`` that never fires in a long-lived host. Best-effort
        throughout: the work is already done, and failing here would turn a completed run
        into a failed one over clean-up.
        """
        session = str(getattr(self.ctx, "id", "") or "")
        if not session:
            return
        for label, release in (
            ("jobs", self._release_jobs),
            ("terminals", self._release_terminals),
            ("attachments", self._release_attachments),
            ("capabilities", self._release_capabilities),
        ):
            for attempt in range(3):
                try:
                    release(session)
                    break
                except Exception as error:  # noqa: BLE001
                    if attempt < 2:
                        await asyncio.sleep(0.05 * (attempt + 1))
                        continue
                    logger.warning(f"| ⚠️ [{self.name}] could not release {label}: {error}")
                    if self.proc is not None:
                        self.proc.cleanup_errors.append({"phase": label, "error": str(error)})

    @staticmethod
    def _release_jobs(session: str) -> None:
        from agentevolver.job import job_manager

        job_manager.forget(session)

    @staticmethod
    def _release_terminals(session: str) -> None:
        from agentevolver.terminal import terminal_manager

        terminal_manager.forget(session)

    @staticmethod
    def _release_attachments(session: str) -> None:
        """Drop the run's live images. The committed bytes on disk are left alone."""
        from agentevolver.attachment import attachment_manager

        attachment_manager.release(session)

    def _release_capabilities(self, session: str) -> None:
        from agentevolver.agent.context.capabilities import forget

        forget(self.ctx, self.name)


    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    @property
    def _events(self) -> Any:
        from agentevolver.agent.loop.events import events

        return events

    def _identity(self) -> Dict[str, Any]:
        """The coordinates every lifecycle event is tagged with."""
        return {
            "task_id": getattr(self.proc, "pid", "") or "",
            "memory_name": self.memory_name or None,
            "use_memory": bool(self.use_memory and self.memory_name),
            "parent_session_id": getattr(self.ctx, "parent_session_id", None),
            "subtask_id": getattr(self.ctx, "subtask_id", None),
        }

    def count_usage(self, raw: Optional[Dict[str, Any]]) -> None:
        """Count reported input (including cache) and output once, not reasoning twice."""
        from agentevolver.model.types import TokenUsage

        usage = raw if isinstance(raw, TokenUsage) else TokenUsage.from_raw(raw)
        if usage is not None:
            self._used_tokens += usage.total
            self._unspent_tokens += usage.total
            budget = getattr(self.proc, "budget", None)
            if budget is not None:
                budget.record(usage)

    def check_budget(self) -> None:
        """Mandatory limit; optional middleware cannot disable it."""
        from agentevolver.agent.loop.guards import BudgetExhausted

        budget = getattr(self.proc, "budget", None)
        if budget is not None:
            budget.check()
        if self.max_token is not None and self._used_tokens >= self.max_token:
            raise BudgetExhausted(f"Token limit reached ({self._used_tokens}/{self.max_token})")

    def spend_step_tokens(self) -> int:
        """Take reported input/output tokens since the last optional constraint check."""
        spent, self._unspent_tokens = self._unspent_tokens, 0
        return spent

    async def _emit_start(self) -> None:
        from agentevolver.hook.types import HookEvent

        body = {"task": self.task, "agent_name": self.name, **self._identity()}
        await self._events.emit(HookEvent.ON_START, body, ctx=self.ctx)

    async def _post_step(
        self, step: int, decision: Decision, results: Sequence[ActionResult]
    ) -> None:
        """One completed step, as observers see it.

        Emitted after the turn is whole — the assistant message and every tool result are
        already in the conversation — so an observer that projects a training record sees
        a valid turn rather than half of one.
        """
        from agentevolver.hook.types import HookEvent

        self.save_thread()
        await self._events.emit(
            HookEvent.POST_STEP,
            {
                "agent_name": self.name,
                "step_number": step,
                "reasoning": decision.reasoning,
                "assistant_text": decision.text,
                "provider_state": decision.provider_state,
                "step_usage": decision.usage,
                "step_tokens": int((decision.usage or {}).get("output_tokens") or 0),
                "plan": [
                    {"id": r.call.id, "name": r.call.name, "type": "tool",
                     "description": "", "args": r.call.signature()}
                    for r in results
                ],
                "done": any(r.final for r in results),
                **self._identity(),
            },
            ctx=self.ctx,
        )

    def save_thread(self) -> None:
        """Keep the last complete boundary; disk failure must be visible, not fatal."""
        if (self._thread_path is None or not self.conversation.complete
                or self._thread_process != getattr(self.proc, "pid", "")):
            return
        try:
            self.conversation.save(self._thread_path, model=self.model_name, agent=self.name)
        except (OSError, ValueError, TypeError) as error:
            logger.warning(f"| ⚠️ [{self.name}] thread checkpoint not saved: {error}")

    async def _emit_stop(self, response: Response) -> None:
        from agentevolver.hook.types import HookEvent

        await self._events.emit(
            HookEvent.ON_STOP,
            {
                "agent_name": self.name,
                "result": response.message,
                "success": response.success,
                **self._identity(),
            },
            ctx=self.ctx,
        )

    # ==================================================================
    # Internals
    # ==================================================================

    def _decide(self, accumulated: Dict[str, Any]) -> Decision:
        """Turn an accumulated stream into a Decision."""
        raw_calls = accumulated.get("tool_calls") or []
        stop_reason = str(accumulated.get("stop_reason") or "")
        calls = [
            ActionCall(
                id=str(getattr(call, "id", "") or f"call_{index}"),
                name=str(getattr(call, "name", "")),
                args=dict(getattr(call, "input", {}) or {}),
                caller=getattr(call, "caller", None),
            )
            for index, call in enumerate(raw_calls)
        ]
        if stop_reason == "max_tokens":
            # Arguments assembled from a truncated stream are plausible and wrong.
            calls = []
        text = str(accumulated.get("text") or "")
        # Normalised, not raw. Every provider reports input, output, cache_write and
        # cache_read under its own names; taking the raw dict here left the whole record
        # unusable downstream, so nothing could say whether a prompt was ever cached.
        from agentevolver.model.types import TokenUsage

        usage = TokenUsage.from_raw(accumulated.get("usage"))
        return Decision(
            text=text,
            reasoning=str(accumulated.get("thinking") or "") or text,
            calls=calls,
            provider_state=dict(accumulated.get("provider_state") or {}),
            usage=usage.model_dump() if usage is not None else None,
            stop_reason=stop_reason,
        )

    async def _live_blocks(self, step: int) -> List[str]:
        """This step's volatile layer: notes carried in, plus middleware output."""
        from agentevolver.plan.server import plan_manager

        blocks = list(self._notes)
        self._notes = []
        planning = plan_manager.context(
            str(getattr(self.ctx, "id", "") or ""), enabled=self.use_plan,
            evolution_enabled=self._evolution_policy_enabled,
            include_rules=False,
        )
        self.conversation.observe("plan", planning)
        state = await self.environment_state(self.ctx)
        if state:
            blocks.append(state)
        note = await self.on_step(step)
        if note:
            blocks.append(note)
        return blocks

    async def _fold_if_needed(self, live: Sequence[str]) -> None:
        """Fold history before cost or capacity becomes a problem."""
        from agentevolver.model import model_manager

        pressure = None
        if model_manager.get_model_config(self.model_name) is not None:
            tools, _ = await self.router.schemas(self, self.ctx)
            messages = self.assembler.build(
                self.conversation, live=live, attachments=self.attachments(),
            )
            pressure = model_manager.measure(self.model_name, self.request_input(messages, tools))
        reason = self.assembler.fold_reason(
            self.conversation, live=live, folds=self._unproductive_folds,
            attachments=self.attachments(),
            request_pressure=pressure,
            input_token_ratio=self._input_token_ratio,
        )
        if not reason:
            return
        logger.info(f"| 🗜️ [{self.name}] compacting before this step: {reason}")
        await self.make_room(trigger=reason)

    async def make_room(self, *, trigger: str = "overflow") -> bool:
        """Fold the oldest history into a checkpoint. Returns whether anything moved.

        The only recovery there is when a provider refuses a request for length, and the
        scheduled path when the assembler says history has grown enough. ``False`` means
        the run should stop trying: either there is nothing left to fold, or the fold
        budget is spent, and the ordinary error path should report the overflow honestly.

        Announced with PRE_COMPACT / POST_COMPACT. Those events existed but only the
        memory tier raised them, so the fold that actually changes what the model sees
        was the one nothing observed: a trace showed the token count drop by half
        between two steps with no record of why, which reads like a metering fault.
        """
        from agentevolver.hook.types import HookEvent

        before = self.assembler.body_tokens(self.conversation)
        await self._events.emit(
            HookEvent.PRE_COMPACT,
            {
                "trigger": trigger,
                "fold": self._folds + 1,
                "max_folds": self.assembler.max_folds,
                "tokens": before,
                "messages": len(self.conversation),
                **self._identity(),
            },
            ctx=self.ctx,
        )
        moved, detail = await self._fold(trigger)
        after = self.assembler.body_tokens(self.conversation)
        # Only a fold that reclaimed nothing spends the budget. Room recovered is the
        # whole point of the call, so charging it would make the guard against a history
        # that cannot shrink fire on one that shrinks every time it is asked.
        if moved and after < before:
            self._unproductive_folds = 0
        else:
            self._unproductive_folds += 1
        await self._events.emit(
            HookEvent.POST_COMPACT,
            {
                "trigger": trigger,
                "folded": moved,
                "detail": detail,
                "tokens_before": before,
                "tokens_after": after,
                "reclaimed": max(0, before - after),
                "unproductive_folds": self._unproductive_folds,
                "messages": len(self.conversation),
                **self._identity(),
            },
            ctx=self.ctx,
        )
        return moved

    async def _fold(self, trigger: str) -> Tuple[bool, str]:
        """The fold itself. Returns whether history moved, and why when it did not."""
        if self._unproductive_folds >= self.assembler.max_folds:
            logger.error(
                f"| 🛑 [{self.name}] history still does not fit after "
                f"{self._unproductive_folds} fold(s) that reclaimed nothing; "
                "not folding further"
            )
            return False, "fold budget spent"
        source = self.assembler.summarize_source(self.conversation)
        if not source:
            logger.warning(f"| 🗜️ [{self.name}] nothing left to fold")
            return False, "nothing left to fold"

        self._folds += 1  # Failed attempts cost tokens too; never retry without a bound.

        archive = None
        if self._thread_path is not None:
            from uuid import uuid4

            from agentevolver.paths import P, path_manager

            archive = path_manager.under(
                self._thread_path.parent, P.LOG_CONTEXT_ARCHIVE,
                thread_id=self._thread_path.stem, digest=uuid4().hex,
            )
            try:
                self.conversation.save(archive, model=self.model_name, agent=self.name)
            except (OSError, ValueError, TypeError) as error:
                logger.warning(f"| ⚠️ [{self.name}] compaction source archive failed: {error}")
                return False, "source archive failed; history retained"

        # Native first, and its own summary counts as the readable checkpoint. Asking
        # the summariser as well would spend a second model call on text the provider
        # already wrote — the portable path exists for routes that produced none.
        native = await self.native_checkpoint(source)
        self.check_budget()
        provider_state = (native or {}).get("provider_state") or None
        summary = str((native or {}).get("summary") or "").strip()
        if not summary:
            summary = await self.text_checkpoint(source)
        if not summary:
            # Do not discard the only readable evidence in favour of an opaque item:
            # the next call may need a provider-neutral fallback.
            return False, "no portable checkpoint was produced; history retained"
        for attempt in range(2):
            if not provider_state:
                # Reject ineffective candidates before paying for semantic verification.
                candidate = summary + (f"\n\nFull pre-compaction source snapshot: {archive}" if archive else "")
                valid, reason = self.assembler.valid_checkpoint(
                    candidate, source,
                    self.conversation.checkpoint.text if self.conversation.checkpoint else "",
                    retained=self.conversation.observations,
                )
                if not valid:
                    return False, f"checkpoint rejected before audit: {reason}"
            if not self.compact_verify:
                break
            from agentevolver.hook.default.compact import CompactHook

            evidence = "\n".join([
                f"Task: {self.conversation.task}",
                self.conversation.checkpoint.text if self.conversation.checkpoint else "",
                *(self._render_for_checkpoint(message) for message in source),
            ])
            removed_ids = {id(message) for message in source}
            observation_ids = {id(message) for message in self.conversation.observations}
            retained = "\n".join([
                f"Unchanged task: {self.conversation.task}",
                *(self._render_for_checkpoint(message) for message in self.conversation.items
                  if id(message) not in removed_ids or id(message) in observation_ids),
            ])
            audit = await CompactHook.verify(
                source=evidence, summary=summary, model=self.model_name, ctx=self.ctx,
                trace_context=self.trace_coordinates(),
                retained_context=retained,
            )
            self.count_usage(getattr(audit, "usage", None))
            self.check_budget()
            if getattr(audit, "approved", False):
                break
            # One targeted repair uses actual findings, rather than regenerating the
            # same flawed summary on every later step. Transport failures or malformed
            # audits are not actionable findings. Neither candidate can alter history
            # until all size and semantic checks pass.
            try:
                findings = json.loads(audit.output or "")
            except (ValueError, TypeError):
                findings = {}
            if attempt == 0 and isinstance(findings, dict) and any(
                isinstance(findings.get(key), list) and findings[key]
                for key in ("omissions", "contradictions")
            ):
                logger.info(f"| 🗜️ [{self.name}] repairing checkpoint from semantic audit (one retry)")
                summary = await self.text_checkpoint(
                    source, previous_summary=summary, audit_feedback=audit.output,
                )
                provider_state = None
                if summary:
                    continue
            self._notes.append("Checkpoint rejected; exact history retained. Audit: " + (audit.output or "unavailable"))
            return False, "semantic audit rejected or unavailable; history retained"
        if archive is not None:
            summary += f"\n\nFull pre-compaction source snapshot: {archive}"
        folded = self.assembler.fold(
            self.conversation, summary, provider_state=provider_state
        )
        if folded:
            return True, "native" if provider_state else "text"
        return False, "checkpoint was rejected as no improvement"

    async def native_checkpoint(
        self, messages: Sequence[Message]
    ) -> Optional[Dict[str, Any]]:
        """Ask the route for a provider-native checkpoint, if it has one.

        Returns the provider's whole result — ``provider_state`` to carry on the
        checkpoint message, and a ``summary`` when the provider wrote readable text —
        or None. A route must declare ``native_compaction`` *and* its client must
        implement it, and Anthropic's beta additionally refuses below 50k input tokens;
        every one of those returns None here and the portable checkpoint stands alone.
        """
        from agentevolver.model import model_manager

        try:
            history = list(messages)
            if self.conversation.checkpoint is not None:
                history.insert(0, self.conversation.checkpoint)
            result = await model_manager.compact_history(
                self.model_name,
                history,
                session_id=str(getattr(self.ctx, "id", "") or ""),
                task_id=getattr(self.proc, "pid", "") or "",
                agent_name=self.name,
                step_number=self.step,
                # Native providers count with their own tokenizer while this side uses a
                # conservative estimate. Leave headroom so a valid provider summary is
                # not rejected and regenerated every step.
                max_output_tokens=max(256, int(self.compact_output_tokens * 0.75)),
            )
        except Exception as error:  # noqa: BLE001 - the text checkpoint remains authoritative
            logger.warning(
                f"| ⚠️ [{self.name}] native compaction unavailable; using the text "
                f"checkpoint ({error})"
            )
            return None
        if not result:
            return None
        self.count_usage(result.get("usage"))
        logger.info(
            f"| 🗜️ [{self.name}] installed native {result.get('format')} checkpoint"
        )
        return result

    async def text_checkpoint(
        self, messages: Sequence[Message], *, previous_summary: str = "", audit_feedback: str = "",
    ) -> str:
        """The portable checkpoint, written by the shared ``compact`` hook.

        The hook rather than a summary prompt of our own: it already owns the instruction
        that names the headings worth keeping, and a second copy here would drift from it
        silently.
        """
        if not messages:
            return ""
        from agentevolver.hook.server import hook_manager
        from agentevolver.hook.types import HookEvent

        existing = self.conversation.checkpoint.text if self.conversation.checkpoint else ""
        if self.conversation.checkpoint and self.conversation.checkpoint.provider_state:
            readable = existing.replace("<memory-checkpoint>", "").replace("</memory-checkpoint>", "").strip()
            if not readable:
                logger.warning(f"| ⚠️ [{self.name}] opaque-only legacy checkpoint cannot be summarized as text")
                return ""
        try:
            result = await hook_manager(
                name="compact",
                input={
                    "event": HookEvent.DIRECT_CALL,
                    "items": [self._render_for_checkpoint(m) for m in messages],
                    "existing_summary": existing,
                    "task": self.conversation.task,
                    "previous_summary": previous_summary,
                    "audit_feedback": audit_feedback,
                    "model_name": self.model_name,
                    "max_output_tokens": self.compact_output_tokens,
                    "trace_context": self.trace_coordinates(),
                },
                ctx=self.ctx,
            )
        except Exception as error:  # noqa: BLE001 - failing to fold is not failing the run
            logger.warning(f"| ⚠️ [{self.name}] could not write a checkpoint: {error}")
            return ""
        self.count_usage(getattr(result, "usage", None))
        self.check_budget()
        return (getattr(result, "output", "") or "").strip()

    @staticmethod
    def _render_for_checkpoint(message: Message) -> str:
        """One folded message as a line the summariser can read.

        Image parts are named, never rendered. ``Message.text`` stringifies a content
        part, and an image part stringifies to its whole ``data:image/...;base64,`` URL —
        so a single screenshot in the folded history would put megabytes of base64 into
        the summariser's prompt and again into the auditor's. The provider-native path is
        different and unaffected: it is handed the real messages precisely so it can
        compact the images themselves.
        """
        from agentevolver.message.types import ContentPartText

        role = getattr(message, "role", "?")
        content = getattr(message, "content", None)
        if isinstance(content, list):
            text = "\n".join(
                f"[{getattr(part, 'type', 'attachment')}]"
                if not isinstance(part, ContentPartText) else part.text
                for part in content
            ).strip()
        else:
            text = (getattr(message, "text", "") or "").strip()
        calls = getattr(message, "tool_calls", None) or []
        if calls:
            named = ", ".join(f"{call.function.name}({call.function.arguments})" for call in calls)
            text = f"{text}\n  calls: {named}".strip()
        name = getattr(message, "name", None)
        head = f"{role}" + (f"[{name}]" if name else "")
        return f"{head}: {text}"

    def _execution_context(self) -> Dict[str, Any]:
        """Coordinates the tool pipeline records against this step."""
        return {
            "session_id": str(getattr(self.ctx, "id", "") or ""),
            "agent_name": self.name,
            "step_number": self.step,
            "task_id": getattr(self.proc, "pid", "") or "",
        }

    def _handle_model_error(self, decision: Decision) -> Optional[Response]:
        """Count a failed model call; return a Response once it is hopeless."""
        self._model_failures += 1
        if self._model_failures >= MAX_MODEL_FAILURES:
            return self._failed(
                f"The model could not be called after {self._model_failures} attempts: "
                f"{decision.error}"
            )
        self._notes.append(f"The previous model call failed: {decision.error}")
        return None

    def _handle_truncation(self) -> Optional[Response]:
        """Count a turn cut off at the output limit."""
        self._truncated_turns += 1
        if self._truncated_turns >= MAX_TRUNCATED_TURNS:
            return self._failed(
                f"The model hit its output limit {self._truncated_turns} times before "
                "completing an action."
            )
        self._notes.append(
            "Your previous response hit the output limit before the action was "
            "complete, so it was discarded. Take a much smaller action: split a large "
            "file into chunks, or make a targeted edit."
        )
        return None

    @staticmethod
    def _task_text(task: str, files: Optional[Sequence[str]]) -> str:
        if not files:
            return task
        listed = "\n".join(f"- {path}" for path in files)
        return f"{task}\n\n<files>\n{listed}\n</files>"

    def _finish(self, result: str) -> Response:
        logger.info(f"| ✅ [{self.name}] finished in {self.step + 1} step(s)")
        return self._respond(True, result or "")

    def _failed(self, reason: str) -> Response:
        logger.error(f"| ❌ [{self.name}] {reason}")
        return self._respond(False, reason)

    def _respond(self, success: bool, message: str) -> Response:
        """Build the run's Response and tell observers it ended.

        One exit for every ending — finished, failed, out of budget, out of steps — so
        the stop event exists once instead of once per outcome. The emit is scheduled
        rather than awaited: a Response is the caller's, and an observer that is slow to
        write must not hold it up.
        """
        import asyncio

        response = Response(
            type=ResponseType.AGENT, success=success, message=message,
            data={"steps": self.step + 1, "elapsed": time.time() - self._started_at},
        )
        try:
            asyncio.get_running_loop().create_task(self._emit_stop(response))
        except RuntimeError:  # pragma: no cover - no loop, nothing to observe with
            pass
        return response

    def _exhausted(self) -> Response:
        return self._failed(
            f"Reached the step budget ({self.max_step}) without finishing."
        )

    def __str__(self) -> str:
        return f"{type(self).__name__}(name={self.name})"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Agent({self.name}, model={self.model_name!r}, max_step={self.max_step})"


__all__ = [
    "INHERITED_CONTEXT_MAX",
    "MAX_MODEL_FAILURES",
    "MAX_TRUNCATED_TURNS",
    "Agent",
]
