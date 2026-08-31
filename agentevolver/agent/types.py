"""Agent Context Protocol (agent manager) Types

Core type definitions for the Agent Context Protocol and common Agent
abstractions, aligned with the design of `agentevolver.tool.types`.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import uuid
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import Any, ClassVar, Dict, List, Optional, Type, Tuple


from pydantic import BaseModel, ConfigDict, Field

from agentevolver.code import BATCH_CALL_TOOL, GuardedDispatch
from agentevolver.config import config
from agentevolver.dynamic import dynamic_manager
from inspect import isawaitable

from agentevolver.logger import logger
from agentevolver.memory import memory_manager
from agentevolver.memory.default.tiered import TieredMemory
from agentevolver.capability import MOUNTED_TYPES, mounted_type as mounted_type_entry
from agentevolver.message import ContentPartText, HumanMessage, Message
from agentevolver.prompt import prompt_manager
from agentevolver.tool import tool_manager
from agentevolver.skill import skill_manager
from agentevolver.connector import connector_manager
from agentevolver.environment import environment_manager
from agentevolver.constraint import (
    constraint_manager,
    render_status_text,
    StepConstraint,
    TokenConstraint,
    WallTimeConstraint,
)
from agentevolver.session import BaseContext
from agentevolver.constraint import Constraint
from agentevolver.registry import CONSTRAINT
from agentevolver.response import Response
from agentevolver.utils import (
    assemble_workspace_path,
    get_extension_root,
    get_package_root,
)

# Tools that mutate the framework / deliverables. A read_only agent (e.g. an evaluator)
# is refused these at dispatch time — a coarse guard so a "read-only" agent cannot edit
# source, commit, deploy, or roll back evolution. Read/inspect/probe tools (and calling
# the target under test) stay allowed so evaluators still work. Op-level enforcement
# (allow reads, deny writes per call) is future work.
_READ_ONLY_DENIED_TOOLS = {
    "write_file_tool", "edit_file_tool", "git_tool", "deploy_tool", "evolution_tool",
}

#: The capabilities whose presence means "this run can self-evolve". Checked
#: against the live roster to decide whether the prompt's self-evolution rules
#: are rendered at all — see Agent._evolution_enabled.
_EVOLUTION_TOOL = "evolution_tool"
_EVOLUTION_SKILL = "self_evolving_skill"
_EVOLUTION_AGENTS = frozenset({"generate_agent", "optimize_agent", "evaluate_agent"})

#: Blocked no-progress proposals tolerated before a run is terminated, as a fraction of
#: its step budget rather than a fixed count. Every blocked proposal already costs the
#: agent a turn and pushes a correction back at it; terminating is the separate, final
#: judgement that it will never move on.
#:
#: This was a fixed 3, calibrated when budgets were tens of steps. At 1000 it killed a run
#: on step 27 — 2.7% of the budget spent, 973 steps left — while the agent was re-reading
#: output it had correctly captured moments earlier. That is flailing and worth pushing
#: back on, but not worth ending a run over that early: the deliverable at step 27 is
#: almost nothing, and the agent had the material it needed.
_NO_PROGRESS_STRIKE_BUDGET_FRACTION = 0.05

#: Floors and a ceiling on the above, so a tiny budget still gets a few corrections and a
#: huge one does not spend a quarter of itself circling.
_NO_PROGRESS_STRIKES_MIN = 3
_NO_PROGRESS_STRIKES_MAX = 25

#: Extra allowance before the run has changed anything at all. Ending such a run
#: guarantees an empty deliverable, so more corrective pushback is the cheaper mistake.
_NO_PROGRESS_STRIKES_BEFORE_ANY_CHANGE = 8

#: Consecutive model-call failures before a run stops instead of retrying. Three is
#: enough to ride out a transient upstream error and few enough that a misconfiguration
#: is reported rather than converted into an exhausted budget.
_THINK_FAILURES_BEFORE_GIVING_UP = 3

#: Consecutive *degenerate* turns — the model returned successfully with neither
#: reasoning nor a tool call — before a run stops. Distinct from a think failure (the
#: call raised) and from a text-only turn (reasoning, but no call, which the nudge
#: handles): this is a call that succeeded and said nothing at all. One ProgramBench
#: instance did exactly this for all 100 of its steps — empty reasoning, empty tool
#: calls, `step += 1` each time — and burned the whole budget to a zero score with
#: nothing logged as wrong. A handful rides out a transient upstream hiccup; beyond that
#: the model is not going to start answering, and the budget is better not spent finding
#: that out one empty step at a time.
_EMPTY_TURNS_BEFORE_GIVING_UP = 5

#: Hard runaway guard, not the ordinary cadence. Early compaction keeps a long run near a
#: bounded working set, so a 400-step benchmark legitimately folds more than three times;
#: the retained exact tail and one rolling checkpoint keep every fold structurally safe.
_COMPACTIONS_PER_RUN = 32

#: How much of a parent's conversation a forked child is shown. A history has no natural
#: size, and this rides in the child's prompt on every step, so it is bounded here rather
#: than left to the pressure guard — which may only shrink tool results and would have
#: nothing to take.
_INHERITED_CONTEXT_MAX = 12_000

#: Steps reserved at the end of a bounded run for landing the work. `max_step` is a hard
#: force-stop: the turn that reaches it is abandoned mid-action and the run is marked "not
#: completed", so a run that was still writing when it ran out ships whatever it last
#: persisted — which cost a stopped ProgramBench run its uncommitted refinements. During
#: the reserved window the prompt carries an unmissable "persist and finish now" directive,
#: on top of the softer budget tiers, so the agent commits/saves and calls done_tool before
#: the hard stop rather than being cut off. Taken from `max_step`, not added to it, so the
#: total budget is unchanged; scaled down for tiny budgets so it never swallows the run.
_LANDING_RESERVE_STEPS = 3

#: The value `max_step` is stored as when a run is configured unbounded (`max_step <= 0`);
#: mirrors the sentinel used where the budget is normalised. A run at this ceiling has no
#: end to land before, so the landing window and the cost denominator both suppress it.
_UNBOUNDED_MAX_STEP = int(1e8)


def _in_landing_window(step: int, max_step: int) -> bool:
    """True when a run at ``step`` (0-based, about to take step+1) is within the reserved
    landing window before the hard ``max_step`` stop. False for an effectively-unbounded
    budget (``max_step == _UNBOUNDED_MAX_STEP``), which has no end to land before. The
    reserve is scaled down for a tiny budget so it never swallows the whole run."""
    if max_step >= _UNBOUNDED_MAX_STEP:
        return False
    reserve = max(1, min(_LANDING_RESERVE_STEPS, max_step // 4))
    return step >= max_step - reserve


def _delegation_summary(data: Optional[Dict[str, Any]]) -> str:
    """A compact, deterministic completion envelope appended to a sub-agent's result when
    it is handed back to the orchestrator.

    An orchestrator otherwise sees only the child's own final text (`resp.message`), which
    cannot tell it whether the child *finished* or was cut off with partial work — a worker
    that hit its step ceiling returns its last words the same way one that called done_tool
    returns its summary. The deterministic facts are already on `resp.data`; this surfaces
    the two that decide how much to trust the result — the outcome and the cost — as one
    tagged line, so a partial result is never mistaken for a finished one. No transcript,
    no extra model call; just what the run already recorded about how it ended."""
    if not data:
        return ""
    done = data.get("done")
    step = data.get("step")
    max_step = data.get("max_step")
    constrained = data.get("stopped_by_constraint")
    if done and not constrained:
        outcome = "finished"
    elif constrained:
        outcome = "stopped by a resource limit — result is PARTIAL"
    elif step is not None and max_step and step >= max_step:
        outcome = "cut off at the step ceiling — result is PARTIAL, it did not signal completion"
    else:
        outcome = "stopped without finishing — result may be PARTIAL"
    # Suppress the denominator for an unbounded child — "used 5/100000000 steps" is noise.
    if step is not None and max_step and max_step < _UNBOUNDED_MAX_STEP:
        cost = f"; used {step}/{max_step} steps"
    elif step is not None:
        cost = f"; used {step} steps"
    else:
        cost = ""
    return f"\n\n[dispatch status: {outcome}{cost}]"

#: Capability types the dispatch handles with one path: everything except ``tool``,
#: which carries an execution record and the ``done_tool`` terminal, and ``agent``,
#: which can be backgrounded. Derived from the table so a new type joins the plain
#: path by existing rather than by someone remembering this line.
#: From `MOUNTED_TYPES`, not `CAPABILITY_TYPES`: `environment` dispatches through exactly
#: this path — a manager, a name and an action — and is not a capability. Narrowed to the
#: six that were, it would have fallen through to nothing.
_PLAIN_CAPABILITY_TYPES = frozenset(
    entry.type for entry in MOUNTED_TYPES if entry.type not in {"tool", "agent"})

#: Consecutive turns that change nothing before the agent is told so in its own context.
#: Low, because the remedy is cheap — make the edit you were about to justify — and the
#: failure it heads off is expensive: three separate runs spent 65, 300 and 650 turns
#: measuring a difference they had already located.
_IDLE_TURNS_BEFORE_WARNING = 5

#: Consecutive turns that change nothing before an observation-only proposal is blocked
#: outright. The existing guard catches a *repeated* action; this catches the other shape,
#: which is what actually happened three times: many different measurements, none of them
#: followed by a change. Blocking costs the agent one turn and returns a correction, the
#: same as the repeat guard — it does not end the run.
_IDLE_TURNS_BEFORE_BLOCKING = 12

@lru_cache(maxsize=1)
def _runtime_facts() -> Dict[str, str]:
    """Describe the interpreter shell commands will actually run under.

    ``bash_tool`` prepends this interpreter's ``bin`` directory to PATH, so
    ``python``/``pip`` in a command resolve here.  Telling the agent up front
    saves it from spending steps probing the environment (``conda env list``,
    ``which python``, import checks) on every run.  Constant per process.
    """
    import platform
    import sys

    prefix = sys.prefix
    env_name = os.environ.get("CONDA_DEFAULT_ENV") or os.path.basename(prefix)
    return {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "python_env": env_name,
        "platform": f"{platform.system()} {platform.machine()}",
    }


class AgentContext(BaseContext):
    """Context passed into agent manager and individual agent instances."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier for this agent invocation.")
    name: Optional[str] = Field(default=None, description="Human-readable label for this agent invocation.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload passed to the agent.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this agent context.")
    parent_session_id: Optional[str] = Field(default=None, description="Name of the parent MetaAgent, used by trace and escalation hooks.")
    subtask_id: Optional[str] = Field(default=None, description="ID of the subtask record in the parent MetaAgent's plan.")

class InputArgs(BaseModel):
    task: str = Field(description="The task to complete.")
    files: Optional[List[str]] = Field(default=None, description="The files to attach to the task.")

class AgentType(str, Enum):
    """Execution contract used by an agent."""

    TOOL_CALLING = "tool_calling"
    PROCEDURAL = "procedural"

    @classmethod
    def _missing_(cls, value):
        """Map legacy agent-type strings to a valid member (Enum lookup fallback).

        Preserves backward compatibility for configs written under the old informal
        ``"workflow"`` name, now folded into ``PROCEDURAL``. Returns ``None`` for any
        other unknown value so the Enum raises the usual ``ValueError``.
        """
        # Backward compatibility for configs created under the old informal name.
        if value == "workflow":
            return cls.PROCEDURAL
        return None


class AgentConfig(BaseModel):
    """Agent configuration for registration, similar to `ToolConfig`."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="The name of the agent")
    description: str = Field(description="The description of the agent")
    version: str = Field(default="1.0.0", description="Version of the agent")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    enable_evolving: bool = Field(default=False, description="Whether the agent may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Permission mode: read_only / workspace_write / danger_full_access")
    agent_type: AgentType = Field(default=AgentType.TOOL_CALLING, description="Agent execution contract")

    cls: Optional[Any] = None
    config: Optional[Dict[str, Any]] = Field(default_factory=dict,description="The initialization configuration of the agent",)
    instance: Optional[Any] = None
    
    code: Optional[str] = Field(default=None, description="Source code for dynamically generated agent classes (used when cls cannot be imported from a module)")

    function_calling: Optional[Dict[str, Any]] = Field(
        default=None, description="Default function calling representation"
    )
    text: Optional[str] = Field(
        default=None, description="Default text representation of the agent"
    )
    args_schema: Optional[Type[BaseModel]] = Field(
        default=None, description="Default args schema (BaseModel type)"
    )

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """Dump the model to a dictionary, recursively serializing nested Pydantic models."""
        
        result = {
            "name": self.name,
            "description": self.description,
            "metadata": self.metadata,
            "version": self.version,
            "enable_evolving": self.enable_evolving,
            
            "permission_mode": self.permission_mode,
            "agent_type": self.agent_type.value,

            "cls": dynamic_manager.get_class_string(self.cls) if self.cls else None,
            "config": self.config,
            "instance": None,
            "code": self.code,

            "function_calling": self.function_calling,
            "text": self.text,
            "args_schema": dynamic_manager.serialize_args_schema(self.args_schema) if self.args_schema else None,
        }

        return result
    
    @classmethod
    def model_validate(cls, data: Dict[str, Any]) -> 'AgentConfig':
        """Validate the model from a dictionary."""
        name = data.get("name")
        description = data.get("description")
        metadata = data.get("metadata", {})
        version = data.get("version")
        enable_evolving = data.get("enable_evolving", False)
        permission_mode = data.get("permission_mode", "workspace_write")
        agent_type = AgentType(data.get("agent_type", AgentType.TOOL_CALLING))

        cls_ = None
        code = data.get("code")
        if code:
            class_name = dynamic_manager.extract_class_name_from_code(code)
            if class_name:
                try:
                    cls_ = dynamic_manager.load_class(
                        code, 
                        class_name=class_name,
                        base_class=Agent,
                        context="agent"
                    )
                except Exception:
                    cls_ = None
            else:
                cls_ = None
        else:
            cls_ = None
            
        config = data.get("config", {})
        instance = data.get("instance", None)

        function_calling = data.get("function_calling")
        text = data.get("text")
        _raw_schema = data.get("args_schema")
        args_schema = dynamic_manager.deserialize_args_schema(_raw_schema) if _raw_schema is not None else None
        
        return cls(
            name=name,
            description=description,
            metadata=metadata,
            version=version,
            enable_evolving=enable_evolving,
            permission_mode=permission_mode,
            agent_type=agent_type,
            cls=cls_,
            config=config,
            instance=instance,
            function_calling=function_calling,
            text=text,
            args_schema=args_schema,
        )

    def __str__(self) -> str:
        return (
            f"AgentConfig(name={self.name}, "
            f"description={self.description}, "
            f"enable_evolving={self.enable_evolving})"
        )

    def __repr__(self) -> str:
        return self.__str__()


# ---------------------------------------------------------------------------
# Event-driven run: one unified loop for every agent (leaf actors AND orchestrators)
# ---------------------------------------------------------------------------
# The runtime pump drives every agent the same way: on_start kicks the first turn and
# returns None; each turn (_advance) runs _think then dispatches the batch as background
# tasks that post _ActionDone back to THIS agent's own inbox; on_event collects them and,
# when the round drains, advances to the next turn or concludes. round == turn.
#
# An orchestrator (MetaAgent) is not special: it just has ``agent`` capabilities in its
# roster, so some of its dispatched actions are sub-agents. A sub-agent that blocks
# escalates to its parent via the escalation channel → the parent's inbox → on_event — which
# works precisely because every agent (parent included) runs this same event-driven loop.

from agentevolver.runtime.types import BaseMessage as _BaseMessage
from agentevolver.protocol.types import ControlMessage as _ControlMessage, QueryMessage as _QueryMessage


class _ActionDone(_BaseMessage):
    """One dispatched action finished — posted back to the agent's OWN inbox so the
    event-driven round loop can collect it. The agent is both dispatcher and receiver;
    its pump drains these exactly like any other message."""

    call_id: str = ""
    name: str = ""
    output: Optional[str] = None   # the action's observable output (a sub-agent's message,
                                   # a tool's message …) — what an orchestrator shows/inspects
    result: Optional[str] = None   # the completion result (only meaningful when is_done)
    error: Optional[str] = None
    is_done: bool = False          # this call was done_tool (the completion signal)
    reasoning: Optional[str] = None


class _AgentRun:
    """Mutable per-run state for the event-driven loop (one per active runtime ref)."""

    def __init__(self, task, files, ctx, ref, task_id, extra_kwargs):
        self.task = task
        self.files = files
        self.ctx = ctx
        self.ref = ref
        self.task_id = task_id
        self.extra_kwargs = extra_kwargs or {}
        self.step = 0
        #: True once the run enters the reserved landing window (the last few steps before
        #: the hard max_step stop). While set, the prompt carries a forceful "persist and
        #: finish now" directive so the agent lands its work instead of being cut off
        #: mid-action with nothing committed. See `_LANDING_RESERVE_STEPS`.
        self.landing = False
        self.action_errors: List[str] = []
        # the round currently in flight (this turn's batch)
        self.round_step = 0
        self.decision: Optional[Dict[str, Any]] = None
        self.messages: Any = None
        self.outstanding: set = set()
        self.round_tasks: Dict[str, asyncio.Task] = {}
        self.step_plan: List[Dict[str, Any]] = []
        self.round_errors: List[str] = []
        # Every finished action of the current round: {name, result, error, is_done}.
        # Leaf agents ignore this (observations flow through memory); orchestrators read
        # it to build their "what changed" prompt and to inspect sub-agent verdicts.
        self.round_outcomes: List[Dict[str, Any]] = []
        self.round_done = False
        self.round_result: Optional[str] = None
        self.round_reasoning: Optional[str] = None
        # final outcome
        self.done = False
        self.result: Optional[str] = None
        self.reasoning: Optional[str] = None
        self.stopped_by_constraint = False
        self.paused = False   # control channel: when True, don't start the next turn
        # MetaAgent uses these fields to detect an unchanged action batch.  They live on
        # the run (not the Agent singleton) so concurrent sessions never share state.
        self.previous_action_signature: Optional[str] = None
        self.repeated_action_rounds = 0
        self.no_progress_rounds = 0
        #: The run of consecutive identical calls the repeat reminder is tracking:
        #: ``{"signature", "count", "name"}``. Held here rather than in the hook so
        #: concurrent runs cannot trip one another's reminder; the hook is handed this
        #: and hands back its successor.
        self.repeat_chain: Optional[Dict[str, Any]] = None
        #: Whether this run has ever changed its workspace. Until it has, the
        #: no-progress guard widens its strike budget rather than terminating — see
        #: Agent._prepare_round.
        self.produced_change = False
        self.baseline_fingerprint: Optional[str] = None
        #: Set when a turn should be retried immediately, so ``_advance`` loops instead
        #: of a handler calling back into it. The recursion this replaces cost a run: a
        #: 1000-step budget meant up to 1000 nested frames, which is Python's default
        #: recursion limit, and the failure surfaced as "maximum recursion depth
        #: exceeded" from inside a template renderer — nothing to do with the actual
        #: cause.
        self.retry_now = False
        #: How many times this run has folded history to fit a request. Bounded because a
        #: memory that cannot shrink further would otherwise be asked once per step for
        #: the rest of the budget, each time producing the same request and the same
        #: refusal — the shape of runaway this loop has produced before.
        self.rooms_made = 0
        #: Consecutive turns whose model call raised. A run whose model is unreachable or
        #: misnamed produces no tool calls, retries instantly, and would otherwise spend
        #: its whole budget doing that: 958 steps in 44 seconds, reported as a stack
        #: overflow rather than as the model error logged on the first one.
        self.think_failures = 0
        self.empty_turns = 0
        #: Action mix, so an agent can see about itself what it otherwise cannot: that it
        #: has been measuring rather than changing anything. Measuring always succeeds and
        #: never breaks the build, so an unsure agent keeps doing it and its history reads
        #: as activity. One run spent 65 turns on a one-line fix it had already located —
        #: 134 shell commands, 14 reads, 4 edits — and could see every output but not the
        #: ratio.
        self.observations = 0
        self.mutations = 0
        #: Consecutive turns that changed nothing: no mutating tool, no change in
        #: observable state. Reset by either.
        self.idle_turns = 0
        self.last_fingerprint: Optional[str] = None


class Agent(BaseModel):
    """Base class for all agents, mirroring the design of `Tool`."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    #: When the accumulated capability changes reach this fraction of the frozen
    #: catalog *and* :data:`_REFREEZE_MIN_CHARS` in absolute size, the catalog is
    #: re-taken instead of patched further. See :meth:`_freeze_capabilities`.
    #: ClassVars so they stay constants of the class rather than becoming pydantic
    #: fields on every agent instance.
    _REFREEZE_RATIO: ClassVar[float] = 0.25
    #: Both conditions must hold, because re-freezing costs a cache write of the whole
    #: catalog. Against a small catalog the ratio alone fires on the first change — the
    #: per-line prefixes ("now available: ") outweigh a two-line catalog — and paying
    #: tens of thousands of characters to retire a few hundred is never the trade.
    _REFREEZE_MIN_CHARS: ClassVar[int] = 2_000

    name: str = Field(description="The name of the agent.")
    description: str = Field(description="The description of the agent.")
    metadata: Dict[str, Any] = Field(description="The metadata of the agent.")
    version: str = Field(default="1.0.0", description="Version of the agent")
    enable_evolving: bool = Field(default=False, description="Whether the agent may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Permission mode: read_only / workspace_write / danger_full_access")
    agent_type: AgentType = Field(default=AgentType.TOOL_CALLING, description="Agent execution contract")

    def __init__(
        self,
        base_dir: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        prompt_name: Optional[str] = None,
        memory_name: Optional[str] = None,
        max_actions: int = 10,
        max_step: int = 20,
        max_token: Optional[int] = None,
        timeout: Optional[float] = None,
        review_steps: int = 5,
        enable_evolving: bool = False,
        use_memory: bool = True,
        derive_context: bool = True,
        fold_at_pressure: float = 0.85,
        retain_recent_steps: int = 4,
        compact_after_steps: int = 24,
        compact_body_tokens: int = 100_000,
        prefer_native_programmatic_tools: bool = True,
        prefer_native_multi_agent: bool = False,
        native_max_concurrent_subagents: int = 3,
        defer_capabilities_after: int = 40,
        constraints: Optional[List[Constraint]] = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)

        # Set default values
        self.name = name or self.name
        self.description = description or self.description
        self.metadata = metadata or self.metadata
        self.enable_evolving = enable_evolving

        # Set working directory
        self.base_dir = base_dir

        # Set prompt name and modules
        self.prompt_name = prompt_name
        self.memory_name = memory_name
        self.use_memory = use_memory
        #: Take the model's history from the session log instead of rebuilding a prose
        #: transcript. This preserves native assistant/tool turns. See `trace/derive.py`.
        self.derive_context = derive_context
        #: Fold history before a step whose last request was at least this fraction of
        #: the model's input capacity. See :meth:`_fold_ahead`.
        self.fold_at_pressure = float(fold_at_pressure or 0.0)
        self.retain_recent_steps = max(1, int(retain_recent_steps or 1))
        self.compact_after_steps = max(0, int(compact_after_steps or 0))
        #: Primary Codex-style trigger: growth since the carried checkpoint prefix.
        #: Unlike total request size, this resets after every successful compaction.
        self.compact_body_tokens = max(0, int(compact_body_tokens or 0))
        # Provider-neutral preferences. ModelManager resolves each one against the exact
        # route and records the native/fallback choice in the request snapshot.
        self.prefer_native_programmatic_tools = bool(prefer_native_programmatic_tools)
        self.prefer_native_multi_agent = bool(prefer_native_multi_agent)
        self.native_max_concurrent_subagents = max(
            1, int(native_max_concurrent_subagents or 3)
        )
        self.defer_capabilities_after = max(0, int(defer_capabilities_after or 0))
        self.model_name = model_name

        # Setup steps
        self.max_step = max_step if max_step > 0 else int(1e8)
        self.max_actions = max_actions

        self.review_steps = review_steps

        # Resource budgets — fed into constraint checks as per-call overrides,
        # so agent-level limits take precedence over constraint defaults.
        self.max_token = max_token
        self.timeout = timeout

        # Runtime constraints — accept Constraint instances or mmengine-style dicts
        # e.g. {"type": "StepConstraint", "max_step": 20}
        # Registration with the constraint manager happens in `initialize` (async).
        self.constraints: List[Constraint] = self._build_constraints(constraints)

        # Auto-attach constraints for explicitly requested budgets
        # StepConstraint only DISPLAYS the step budget: the loop (`while step < max_step`)
        # stops at the same value first, so it never blocks early — no off-by-one kill.
        if max_step and max_step > 0 and not any(isinstance(c, StepConstraint) for c in self.constraints):
            self.constraints.append(StepConstraint(max_step=max_step))
        if max_token is not None and not any(isinstance(c, TokenConstraint) for c in self.constraints):
            self.constraints.append(TokenConstraint(max_token=max_token))
        if timeout is not None and not any(isinstance(c, WallTimeConstraint) for c in self.constraints):
            self.constraints.append(WallTimeConstraint(max_second=timeout))
        # Tokens consumed by the previous step, fed into the next constraint check (keyed by task_id)
        self._pending_step_tokens: Dict[str, int] = {}
        # Per-run event-driven state, keyed by runtime ref name (one entry per active run).
        self._runs: Dict[str, "_AgentRun"] = {}

    @staticmethod
    def _build_constraints(raw: Optional[List]) -> List[Constraint]:
        """Normalize a mixed constraint spec into concrete ``Constraint`` instances.

        Accepts already-built ``Constraint`` objects (kept as-is) and dict specs (built
        via the ``CONSTRAINT`` registry), so an agent can be configured with either form.

        Raises:
            TypeError: If an item is neither a ``Constraint`` nor a dict.
        """
        if not raw:
            return []
        result = []
        for item in raw:
            if isinstance(item, Constraint):
                result.append(item)
            elif isinstance(item, dict):
                result.append(CONSTRAINT.build(item))
            else:
                raise TypeError(f"Unsupported constraint type: {type(item)}")
        return result

    async def initialize(self) -> None:
        """Initialize the agent."""
        logger.info(f"| 📁 Agent working directory: {self.base_dir}")

        # Register runtime constraints with the global constraint manager
        for c in self.constraints:
            await constraint_manager.register(c)

    def __str__(self) -> str:
        return f"Agent(name={self.name}, model={self.model_name}, prompt_name={self.prompt_name})"

    def __repr__(self) -> str:
        return self.__str__()

    async def _get_agent_context(self,
                                 task: str,
                                 step_number: int = 0,
                                 ctx: Optional[AgentContext] = None,
                                 **kwargs) -> Dict[str, Any]:
        """Get the agent context."""
        time_str = datetime.now().isoformat()
        step_info_body = (
            f"Step {step_number + 1} of {self.max_step} max possible steps\n"
            f"Current date and time: {time_str}"
        )
        # The agent's own action mix. Included because it is the one thing about its
        # behaviour it cannot see: its history shows every command and every output, but
        # not the ratio between looking and doing, and that ratio is what tells it whether
        # it is working or circling.
        run = kwargs.get("_run")
        mutations = getattr(run, "mutations", 0)
        observations = getattr(run, "observations", 0)
        if run is not None and (mutations or observations):
            step_info_body += (
                f"\nTurns that changed something: {run.mutations} | "
                f"turns that only looked: {run.observations}"
            )
            if getattr(run, "idle_turns", 0) >= _IDLE_TURNS_BEFORE_WARNING:
                step_info_body += (
                    f"\n⚠️  The last {getattr(run, 'idle_turns', 0)} turns changed nothing — no file "
                    f"written, no state moved. If you already know what to change, change "
                    f"it now; another measurement will not tell you more than the last one "
                    f"did. If you do not know, you are looking in the wrong place: pick a "
                    f"different item and come back."
                )

        # Clean per-section bodies (no "### " prefix) — each is rendered as its own
        # agent_context sub-module (see code_agent.html and the agent prompts).
        # Four distinct states, said apart. They used to collapse into one line —
        # "[Memory is disabled.]" was also what an agent saw when memory was enabled and
        # simply failed, and the failure was swallowed with a bare `except: pass`. An
        # agent with no history re-derives its situation every step: one run wrote both
        # its deliverables at step 0 and then spent 29 steps re-reading them, because
        # every prompt told it there was nothing to remember and nothing said why.
        memory_body = "[Memory is disabled.]"
        # Working Memory (append-only, byte-stable between compactions) is fetched
        # apart from Recent Steps (the sliding window that changes every step) so the
        # template can place it in the cached prefix — see agent_context.html. Backends
        # that predate the `section` split fall back to a single combined fetch.
        working_memory_body = ""
        if self.use_memory and self.memory_name:
            memory_body = "[Memory is unavailable — proceed from the task and the workspace.]"
            try:
                memory_info = await memory_manager.get_info(self.memory_name)
                if memory_info is None or memory_info.instance is None:
                    logger.warning(
                        f"| ⚠️ [{self.name}] Memory '{self.memory_name}' is configured but not "
                        f"registered; this agent runs without history"
                    )
                else:
                    session_id = ctx.id if ctx else ""
                    inst = memory_info.instance
                    if isinstance(inst, TieredMemory):
                        mem_text = await inst.get(
                            session_id=session_id,
                            short_term_n=self.review_steps,
                            section="volatile",
                        )
                        working_memory_body = await inst.get(
                            session_id=session_id, section="stable"
                        ) or ""
                    else:
                        mem_text = await inst.get(
                            session_id=session_id,
                            short_term_n=self.review_steps,
                        )
                    memory_body = mem_text if mem_text else "[No memory recorded yet.]"
            except Exception as error:  # noqa: BLE001 — a step must still run without memory
                logger.warning(
                    f"| ⚠️ [{self.name}] Could not read memory '{self.memory_name}' "
                    f"(session={getattr(ctx, 'id', None)}): {type(error).__name__}: {error}"
                )

        # Resource budgets collected from the previous step's constraint checks
        constraint_status = kwargs.get("constraint_status") or []
        constraint_text = render_status_text(constraint_status) if constraint_status else "[No active budget.]"

        # Landing window (see `_LANDING_RESERVE_STEPS`): the last steps before the hard
        # max_step stop. This is the final, unmissable escalation on top of the budget
        # tiers — a run cut off mid-action ships only what it already persisted, so spend
        # these steps landing, not starting. Kept general: "persist" is save/commit/write,
        # whatever this task's deliverable is; the agent knows what that means for its work.
        if run is not None and getattr(run, "landing", False):
            constraint_text += (
                "\n\n⚠️ LANDING WINDOW — only the last few steps remain before a hard stop "
                "that discards any unfinished action and marks the task incomplete. Do NOT "
                "start anything new or open another line of investigation. Spend what is "
                "left making what you already have final and durable: persist it (save, "
                "commit, or write out your files and results), record anything unfinished, "
                "then call `done_tool` with your best result. Work you do not persist now "
                "is lost.")

        # Errors from the previous step (shown only when the last step failed) — a
        # universal agent-context sub-module, provided here so subclasses don't each
        # re-derive it. Same for the live workspace snapshot below.
        action_errors = kwargs.get("action_errors") or []
        errors_body = "\n".join(f"- {e}" for e in action_errors) if action_errors else ""

        # The plan, read off disk every step. A file rather than tool-held state: the
        # agent revises it with the file tools it already has, the person watching opens
        # the same path, and it survives the run. Read rather than cached because the
        # agent may have just rewritten it — a cached copy would show the previous plan
        # on the step that acted on the new one.
        plan_body = ""
        if ctx is not None:
            try:
                from agentevolver.plan import (
                    AUTO_MODE_NOTICE,
                    PlanMode,
                    plan_manager,
                    read_plan,
                )

                session_id = str(getattr(ctx, "id", "") or "")
                # No session id passed: this is the run being rendered, and naming it
                # takes the parameterised path, which no override answers.
                plan_body = read_plan()
                if not plan_body and plan_manager.mode(session_id) is PlanMode.AUTO:
                    # An empty slot would render as no slot at all — `{% if plan %}` —
                    # so in `auto` the agent would never be told the document is its to
                    # write. The obligation goes where the plan goes: the one place it
                    # already reads for the plan, on the step it would have to act on it.
                    plan_body = f"(no plan yet)\n\n{AUTO_MODE_NOTICE}"
            except Exception as error:                              # noqa: BLE001
                logger.warning(f"| ⚠️ [{self.name}] could not read plan.md: {error}")

        return {
            "step_info": step_info_body,
            "memory_context": memory_body,
            "working_memory": working_memory_body,
            "constraint_text": constraint_text,
            "workspace": self._workspace_snapshot(ctx),
            "errors": errors_body,
            "plan": plan_body,
        }

    def _capability_allowlist(self, capability_type: str, ctx: "AgentContext"):
        """This run's allowlist for one capability type, or ``None`` for the default.

        ``None`` = every loaded entity of that type, ``[]`` = none (the baseline an
        evaluation runs against), ``[names]`` = only those. The one exception is
        ``plugin``, whose manager reads ``None`` as none — the registry holds far
        more than any run wants resident.
        """
        extra = getattr(ctx, "extra", None) if ctx is not None else None
        return (extra or {}).get(f"{capability_type}_allowlist")

    async def _get_capability_context(self, capability_type: str, ctx: "AgentContext",
                                      types: Optional[List[str]] = None,
                                      **extra: Any) -> Dict[str, Any]:
        """One capability type's roster, as the prompt's slots for it.

        Four near-identical copies of this stood here, one per type, and the way
        that failed is on the record: environment context was assembled by a mixin
        only two agents inherited, so every other agent — including agents whose
        environments were in their own ``env_names`` — got none of it. Reading the
        manager through the capability table means a type is served by existing.

        Returns one slot, ``available_<mount_type>``, which is the one a template
        renders. A second, headed ``<type>_context`` was produced beside it and no
        template ever named it — the Code Mode calling convention was appended to that
        key, so an agent holding ``batch_call_tool`` was never told how to call anything.

        An empty roster renders as empty rather than as a notice: the shared capability
        module omits a block whose slot is blank, so a type this agent does not have
        costs it no prompt at all.
        """
        entry = mounted_type_entry(capability_type)
        content = entry.manager().get_instruction(
            allowlist=self._capability_allowlist(capability_type, ctx), types=types,
            level="brief", **extra)
        # `workflow_manager` answers synchronously while every other manager awaits.
        # Awaiting unconditionally raised `TypeError: object str can't be used in
        # 'await' expression` for any agent that reached the generic path for
        # `workflow` — which was every agent except the one that happened to override
        # the hook, so the whole prompt build died on the first step.
        if isawaitable(content):
            content = await content
        return {f"available_{entry.mount_type}": content or ""}

    async def _capability_slots(self, entry: Any, ctx: AgentContext) -> Dict[str, Any]:
        """One capability type's prompt slots.

        A type that needs something of its own defines ``_capability_<type>_slots``;
        everything else takes the generic roster. The hook is named for the capability
        rather than after ``_get_<type>_context`` because ``agent`` would then collide
        with ``_get_agent_context``, which is the orchestration state and takes a
        different signature — a resolution that would have failed only at run time, and
        only for the one type that had no roster to begin with.
        """
        hook = getattr(self, f"_capability_{entry.type}_slots", None)
        if hook is not None:
            return await hook(ctx)
        return await self._get_capability_context(entry.type, ctx)

    async def _capability_skill_slots(self, ctx: AgentContext) -> Dict[str, Any]:
        """Skills, filtered to the types this agent may see.

        The filter is the hard guardrail keeping worker SOPs out of the MetaAgent and
        orchestration recipes out of workers, so it is applied here rather than left to
        whatever allowlist a run happens to carry.
        """
        return await self._get_capability_context("skill", ctx, types=self._allowed_skill_types())

    async def _capability_agent_slots(self, ctx: AgentContext) -> Dict[str, Any]:
        """The sub-agent roster, with this agent left out of its own."""
        return await self._get_capability_context("agent", ctx, exclude=self.name)

    async def _capability_environment_slots(self, ctx: AgentContext) -> Dict[str, Any]:
        """The environment path, reached through the capability walk that visits every type.

        `MOUNTED_TYPES` is what prompt assembly iterates, and `environment` is a row in it
        — so this is where the walk arrives for that row, and it hands straight off to
        :meth:`_get_environment_context`. The name stays `_capability_*` because that is
        the hook `_capability_slots` looks for; what it returns is not a capability roster.
        """
        return await self._get_environment_context(ctx)

    async def _get_environment_context(self, ctx: AgentContext) -> Dict[str, Any]:
        """The environments this agent works in: the roster, and what each one looks like now.

        The third of the three context methods, and the one that did not exist. An agent
        *uses* a capability, is *in* an environment and *has* a memory — the type table says
        so — but only two of those had a method. Environments borrowed
        :meth:`_get_capability_context`, which asks a manager for its roster and nothing
        else, so the base class could name an environment and never see inside it. The live
        state existed only in `BrowserAgent` and `SSHAgent`, each overriding
        `_get_agent_context` to fetch it — which meant "works in an environment" was a
        property of two agent classes rather than of holding an environment.

        Two slots:

        ``environment_context``
            The roster. A catalog that does not change between steps, so it renders beside
            the capabilities, before the state — everything after the state is re-read at
            full price every step.

        ``environment_state``
            What each mounted environment holds right now, refreshed every step. This is
            the half that makes "in" mean something: a roster tells an agent a browser
            exists, a state tells it which page is open.

        Every mounted environment is asked, not only an active one. An agent given two is
        in both, and deciding which one is "current" from the outside is guessing —
        `SSHAgent` reads a host and a workspace, `BrowserAgent` a page; neither is the
        other's context. An agent with none gets two empty strings, and the templates omit
        both blocks, so this costs nothing where nothing is mounted.
        """
        roster = (await self._get_capability_context("environment", ctx))["available_environments"]
        slots = {
            "environment_context": f"### Available Environments\n{roster}" if roster else "",
            "environment_state": await self._environment_state(ctx),
        }
        return slots

    async def _environment_state(self, ctx: AgentContext) -> str:
        """Each mounted environment's live state, headed by name when there is more than one.

        A failure to read one is reported in place rather than raised: an environment that
        cannot be reached is a fact the agent needs — it is about to try to act in it — and
        losing the whole prompt build over it would turn a broken browser into a broken run.
        """
        from agentevolver.capability import ENVIRONMENT_TYPE

        names = await self._mounted_environment_names(ctx)
        if not names:
            return ""
        manager = ENVIRONMENT_TYPE.manager()
        blocks: List[str] = []
        for name in names:
            try:
                state = await manager.get_state(name, ctx=ctx)
            except Exception as error:                       # noqa: BLE001
                logger.warning(f"| ⚠️ [{self.name}] could not read {name} state: {error}")
                blocks.append(f"[{name}: unavailable — {error}]")
                continue
            body = state.get("state") if isinstance(state, dict) else state
            if not body:
                continue
            blocks.append(str(body) if len(names) == 1 else f"#### {name}\n{body}")
        return "\n\n".join(blocks)

    async def _mounted_environment_names(self, ctx: AgentContext) -> List[str]:
        """Which environments this agent holds.

        The allowlist when the run set one, the whole registry otherwise — the same rule
        every other capability type follows, so an agent is never shown a roster it cannot
        act on nor asked for the state of something it does not hold.
        """
        from agentevolver.capability import ENVIRONMENT_TYPE

        allowed = self._capability_allowlist("environment", ctx)
        if allowed is not None:
            return list(allowed)
        listing = ENVIRONMENT_TYPE.manager().list()
        names = await listing if isawaitable(listing) else listing
        return list(names or [])

    async def _capability_tool_slots(self, ctx: AgentContext) -> Dict[str, Any]:
        """Tools, plus the Code Mode calling convention when this agent can run a program."""
        return await self._get_tool_context(ctx=ctx)

    async def _get_tool_context(self, ctx: AgentContext, **kwargs) -> Dict[str, Any]:
        """The tool roster, plus the Code Mode calling convention when it applies."""
        slots = await self._get_capability_context("tool", ctx)
        sdk = await self._code_mode_section(self._capability_allowlist("tool", ctx))
        if sdk:
            slots["available_tools"] = f"{slots['available_tools']}\n\n{sdk}"
        return slots

    async def _get_inherited_context(self, ctx: AgentContext, **kwargs) -> Dict[str, Any]:
        """What a forked child is told its parent had already done.

        A dispatched child starts with a fresh session and sees none of the conversation
        behind its task, so everything it needs to know has to survive the trip inside one
        `task` string. That is the same defect attachments already fixed on the file side:
        without them a child got only the orchestrator's paraphrase of a document the
        orchestrator was handed in full. This is the conversation half — the parent read
        five files and ruled out three approaches, and re-typing that accurately into a
        brief is work the parent will do imperfectly every time.

        Read-only, and rendered rather than replayed. The parent's turns are *not* written
        into the child's log: they did not happen in this session, and a child whose trace
        claims otherwise would export training samples for turns another agent took.

        Bounded from the tail. A parent's history has no natural size, and the most recent
        turns are the ones its decision rested on.
        """
        extra = getattr(ctx, "extra", None) or {}
        from agentevolver.agent.project_context import load_project_context
        from agentevolver.paths import path_manager

        roots = path_manager.session_roots()
        workspace = str(
            roots["workspace"] if roots
            else getattr(config, "workspace_root", "") or ""
        )
        project_context = load_project_context(workspace)
        parent_session = str(extra.get("forked_from") or "")
        if not parent_session:
            return ({"inherited_context": project_context} if project_context else {})
        from agentevolver.trace import trace_manager
        from agentevolver.trace.derive import derive_messages
        from agentevolver.trace.surface import SurfaceError

        try:
            events = trace_manager.events(parent_session)
            derived = derive_messages(events) if events else []
        except SurfaceError as error:
            logger.warning(
                f"| ⚠️ [{self.name}] cannot project the parent log {parent_session} "
                f"({error}); the fork starts without it"
            )
            return {}
        if not derived:
            return ({"inherited_context": project_context} if project_context else {})

        lines = [f"{getattr(m, 'role', '?')}: {getattr(m, 'text', '') or ''}".strip()
                 for m in derived]
        body = "\n\n".join(line for line in lines if line.strip())
        if len(body) > _INHERITED_CONTEXT_MAX:
            kept = body[-_INHERITED_CONTEXT_MAX:]
            body = (f"[earlier turns omitted: {len(body) - _INHERITED_CONTEXT_MAX:,} "
                    f"characters]\n\n{kept}")
        inherited = (
            "This is what the agent that dispatched you had already done, for context. "
            "It is not your own history and you did not take these actions.\n\n" + body
        )
        if project_context:
            inherited = f"{project_context}\n\n### Parent execution context\n{inherited}"
        return {"inherited_context": inherited}


    async def _code_mode_section(self, allowlist: Optional[List[str]]) -> str:
        """Declare the visible tools as functions, for agents that hold `batch_call_tool`.

        Rendered from the same roster the tool cards above it come from, and only when the
        transport is actually in that roster — an agent that cannot run a program has no
        use for a calling convention, and the block would just be prompt it pays for every
        step.
        """
        from agentevolver.tool.default.code_mode.sdk import code_mode_section, sdk_for

        names = list(allowlist) if allowlist is not None else await tool_manager.list()
        if BATCH_CALL_TOOL not in names:
            return ""
        return code_mode_section(await sdk_for(names, tool_manager))

    def _allowed_skill_types(self) -> List[str]:
        """Which skill types this agent may see. Workers see 'worker' skills;
        the MetaAgent overrides this to ['orchestrator']. This is the hard
        guardrail that keeps the two skill audiences separate regardless of which
        skills a run happens to load."""
        return ["worker"]





    async def _resolve_workspace_root(self, ctx: AgentContext, **kwargs) -> str:
        """Resolve the workspace_root surfaced in the prompt's `{{ workspace_root }}` slot.

        The run's bound workspace, falling back to this agent's own ``base_dir`` outside a
        session. Every agent in one run therefore shares a directory, which is what a
        MetaAgent dispatching sub-agents needs — the sub-agent has to be able to open what
        the parent wrote. The agent runs inside the container its tools run in, so this
        path is already the working directory those tools see.

        The docstring used to say it preferred ``ctx.workspace_root``; it never read the
        context at all, and the fact that nothing broke is the evidence that the roots
        travelling through contexts were not the ones being used.
        """
        return assemble_workspace_path(config.workspace_root or self.base_dir)

    def _workspace_snapshot(self, ctx: Optional[AgentContext]) -> str:
        """A live listing of the working directory's files, refreshed each step.

        Lets an agent see what's currently in its scratch directory without
        spending a tool call. Opt-in: agents that do file work expose this as a
        `workspace` sub-module from their `_get_agent_context` override.
        """
        workspace_root = os.path.abspath(config.workspace_root or self.base_dir)
        try:
            entries = sorted(os.listdir(workspace_root))
            lines = [
                f"  {name}{'/' if os.path.isdir(os.path.join(workspace_root, name)) else ''}"
                for name in entries
            ]
            snapshot = "\n".join(lines) if lines else "  (empty)"
        except Exception:
            snapshot = "  (unavailable)"
        return f"{workspace_root}\n{snapshot}"

    def _task_with_input_files(self, task: str, **kwargs) -> str:
        """Append the input files the user attached to the task body.

        Input files are part of the assignment (static), so they live inside the
        `task` module rather than a separate block. Only existing paths are shown.
        """
        files = kwargs.get("files") or []
        existing = [f for f in files if os.path.exists(f)]
        if not existing:
            return task
        listing = "\n".join(f"- {f}" for f in existing)
        return f"{task}\n\n**Input files:**\n{listing}"

    async def _evolution_enabled(self, ctx: Optional[AgentContext] = None) -> bool:
        """Whether this run actually has self-evolution capabilities available.

        Derived from the live roster rather than declared by a config flag, so the
        prompt cannot contradict what the agent can do. A declared flag is a second
        source of truth: set it wrong, or change the roster without it, and the
        prompt starts teaching the agent to invoke `self_evolving_skill` and roll
        back with `evolution_tool` when neither is loaded — instructions for tools
        that are not there, which is how a lean run ends up reasoning about
        capabilities it cannot reach.

        Gates the `<self-evolution-rules>` block and
        the generator/optimizer/evaluator half of the sub-agent taxonomy.
        """
        from agentevolver.skill.server import skill_manager
        from agentevolver.tool.server import tool_manager
        from agentevolver.agent.server import agent_manager

        # Any one of these is enough: the roster is assembled per run, and a
        # partially-wired evolution setup should still get the rules rather than
        # silently lose them.
        try:
            if _EVOLUTION_TOOL in set(await tool_manager.list()):
                return True
            if _EVOLUTION_SKILL in set(await skill_manager.list()):
                return True
            return bool(_EVOLUTION_AGENTS & {str(n) for n in await agent_manager.list()})
        except Exception as e:  # noqa: BLE001 — never fail a render over introspection
            logger.warning(f"| ⚠️ [{self.name}] Could not resolve evolution roster: {e}")
            return False

    async def _get_messages(self,
                            task: str,
                            ctx: AgentContext,
                            **kwargs) -> List[Message]:
        """Build system+agent messages using prompt templates and context."""

        # Record the route this turn is being built for. A tool called during the turn
        # receives this exact context object, so this is how a tool learns which model
        # its result is about to be shown to — `read_image_tool` refuses on it. Written
        # here rather than read from config because an agent may carry its own model.
        if getattr(ctx, "extra", None) is not None and self.model_name:
            ctx.extra["model_name"] = self.model_name

        workspace_root = await self._resolve_workspace_root(ctx=ctx, **kwargs)
        # The roots the agent is told about come from the layout table for the session
        # this run is bound to. They used to be read out of `ctx.extra`, where they had
        # been copied — and where `extension_root` named the session's staging tree while
        # the identically-spelled `config.extension_root` named the shared library. An
        # agent told the wrong one writes where promotion will refuse to look.
        from agentevolver.paths import P, path_manager
        roots = path_manager.session_roots()
        extension_root = str(roots["extension"] if roots else get_extension_root())
        package_root = str(roots["package"] if roots else get_package_root())
        project_root = str(roots["project"] if roots else getattr(config, "project_root", ""))
        log_root = str(roots["log"] if roots else getattr(config, "log_root", ""))

        system_modules = dict(
            max_actions=self.max_actions,
            extension_root=extension_root,
            package_root=package_root,
            project_root=project_root,
            workspace_root=workspace_root,
            log_root=log_root,
            evolution_enabled=await self._evolution_enabled(ctx=ctx),
            # What this run was dispatched to work on. The evolution agents build whichever
            # component type they are told to, so the type is an input to the run — and a
            # generate run cannot look its target up, because the target does not exist yet.
            # Empty for every other agent, whose prompts do not mention either.
            target_name=str(roots.get("target_name") or ""),
            target_type=str(roots.get("target_type") or ""),
            **_runtime_facts(),
        )
        agent_message_modules = dict(task=self._task_with_input_files(task, **kwargs))

        agent_message_modules.update(await self._get_agent_context(task, ctx=ctx, **kwargs))
        agent_message_modules.update(await self._get_inherited_context(ctx=ctx))
        # Every type in the table, rather than a list of them here. Written out, this was
        # one more register to remember: `agent` was missing from it, which is why a
        # prompt told the model to "dispatch a sub-agent from Available Sub-Agents" while
        # nothing produced that roster.
        for entry in MOUNTED_TYPES:
            agent_message_modules.update(await self._capability_slots(entry, ctx))
        
        response = await prompt_manager(
            name=self.prompt_name,
            input={
                "system_modules": system_modules,
                "agent_modules": agent_message_modules,
            },
        )
        if not response.success:
            raise ValueError(response.message)

        messages = response.data["messages"]
        if self.derive_context:
            messages = self._derived_messages(messages, ctx)
        else:
            messages = self._frozen_rendered(messages, ctx)
        return self._with_attachments(messages, ctx)

    @staticmethod
    def _with_attachments(messages: List[Message], ctx: AgentContext) -> List[Message]:
        """Append the images this run has read as a trailing user turn.

        Every step rebuilds the whole prompt from memory, so an image appended once is
        absent from the next request. Re-attaching each step is what makes "look at this
        screenshot" hold for more than one turn.

        A new message rather than parts appended to the last one: the last message is not
        reliably a user turn — the derived path can end with the frozen capability delta —
        and its content is sometimes a plain string. Appending after everything else also
        keeps the images behind the cache breakpoint, where content that changes between
        steps belongs.
        """
        from agentevolver.attachment import attachment_manager

        live = attachment_manager.live(str(getattr(ctx, "id", "") or ""))
        if not live:
            return messages

        parts: List[Any] = [ContentPartText(text="Images you read, in the order you read them:")]
        for attachment in live:
            parts.append(ContentPartText(text=f"\n[{attachment.source_path}]"))
            parts.append(attachment_manager.content_part(attachment))
        return list(messages) + [HumanMessage(content=parts)]

    def _frozen_rendered(self, rendered: List[Message], ctx: AgentContext) -> List[Message]:
        """Hold the catalog's bytes still on the default path as well.

        Freezing reached only the projection, which is the switch nobody has turned on.
        The default path re-rendered the catalog live every step, so the first component
        this framework generated rewrote it — and the catalog sits at the front of the
        turn, ahead of the cache breakpoint, so rewriting it invalidates the prefix for
        the rest of the session. The one thing the system exists to do would quietly
        cancel the caching it just gained.

        Same trade as the projection, minus the restructuring: the catalog goes out
        exactly as first rendered, and the delta is appended to the *end* of the same
        turn — past the whole agent-context (task, plan, working-memory, and the live
        state). The cache breakpoint is at ``<constraints>`` (see the serializer's
        ``_cache_split``), not at ``</capability-context>``: the frozen catalog **and**
        the stable blocks between it and ``<constraints>`` — task, inherited-context,
        plan — all ride in the cached prefix, while the delta, sitting after every one of
        them at the very end, stays outside it and costs nothing to change when evolution
        registers or withdraws a component. (working-memory and the rest of the live state
        sit past ``<constraints>`` too — see agent_context.html for the tier rules.) The
        consequence for anyone editing the layout: the span from ``</capability-context>``
        to ``<constraints>`` is now cached, not live, so nothing that changes between steps
        may be inserted there — put per-step content past ``<constraints>``.
        """
        import re as _re
        from agentevolver.agent.context_builder import strip_rendered_comments

        rendered = strip_rendered_comments(rendered)

        extra = getattr(ctx, "extra", None)
        if extra is None:
            return rendered

        for index, message in enumerate(rendered):
            text = getattr(message, "text", "") or ""
            match = _re.search(r"<capability-context>.*?</capability-context>", text, _re.S)
            if match is None:
                continue

            frozen, addition = self._freeze_capabilities(
                [HumanMessage(content=match.group(0))], ctx)
            if not addition:
                # Either the first step, or nothing has changed since it. Either way the
                # bytes already match the snapshot; rebuilding would only risk differing.
                return rendered

            rebuilt = (text[:match.start()] + frozen[0].text + text[match.end():]
                       + "\n\n" + addition[0].text)
            return rendered[:index] + [HumanMessage(content=rebuilt)] + rendered[index + 1:]

        return rendered

    def _derived_messages(self, rendered: List[Message], ctx: AgentContext) -> List[Message]:
        """Replace the rendered transcript with the log's own projection.

        The rendered path describes history in prose inside one user turn, re-built from
        memory every step. The projection replays it as the turns that actually
        happened — assistant messages carrying their tool calls, tool messages carrying
        the results — which is the shape the model was trained on, and which appends
        rather than being rewritten, so the prompt prefix can be cached.

        The rendered turn carries more than history, though: the budget, the step
        guidance, the plan, the workspace snapshot, and the previous step's errors —
        which is where the repeat reminder rides. Replacing it wholesale silently turned
        those off, so they are re-attached as a trailing turn instead. That is also where
        they belong for caching: they change every step, and volatile content after the
        last stable byte is what keeps the prefix reusable, whereas the rendered path
        mixes them *into* the history and so can never settle.

        Falls back to the rendered messages, loudly, whenever the log cannot support the
        projection. A short history is worse than a described one: the model would act on
        a conversation that silently lost its earlier turns.
        """
        from agentevolver.agent.context_builder import context_builder
        from agentevolver.trace import trace_manager
        from agentevolver.trace.surface import SurfaceError

        session_id = str(getattr(ctx, "id", "") or "")
        events = trace_manager.events(session_id)
        if not events:
            logger.warning(
                f"| ⚠️ [{self.name}] No retained log for session {session_id}; "
                f"using the rendered history"
            )
            return context_builder.build(rendered, [], ctx)
        try:
            return context_builder.build(rendered, events, ctx)
        except SurfaceError as error:
            logger.warning(
                f"| ⚠️ [{self.name}] Log for session {session_id} cannot be projected "
                f"({error}); using the rendered history"
            )
            return context_builder.build(rendered, [], ctx)

    @staticmethod
    def _freeze_capabilities(
        stable: List[Message], ctx: AgentContext
    ) -> Tuple[List[Message], List[Message]]:
        """Hold the capability catalog's bytes still, and announce changes after it.

        The catalog is only stable until this framework does the thing it exists for.
        Evolution registers a component mid-session and every agent's next step reads the
        managers live, so the catalog is rebuilt — and it is not merely appended to:
        measured on a real registry, removing one skill of eighty-four leaves a common
        prefix of **four characters**. Rewritten in place at the front of the request,
        one generated skill invalidates the entire conversation behind it.

        So the first render's bytes are kept verbatim for the rest of the session, and
        what changed since is stated as a later turn instead. The frozen catalog stays
        cacheable; the announcement rides where invalidation costs nothing. This is the
        same move compaction makes with `replace`: never rewrite what has already been
        said, add something that supersedes it.

        The announcement reuses the *same block types* — a generated skill is announced
        inside a `<skill-context>`, a generated tool inside a `<tool-context>`. A new
        capability is not a new kind of thing, and asking the model to merge a catalog
        with a separate change log to answer "what skills do I have" adds a vocabulary
        the prompt never defined. The block repeats; the concept does not.

        Freezing is not free forever. Each change lengthens the announcement while the
        frozen catalog grows staler, and a long evolving session would end up carrying a
        change log rivalling the catalog it patches — paying for both, and asking the
        model to reconcile them on every step. Past :data:`_REFREEZE_RATIO` the trade
        inverts: re-freezing costs one cache write and the announcement goes back to
        empty, so that is what happens. Rare by construction, since it takes a great many
        changes to get there.

        Returns:
            ``(frozen, addition)`` — the catalog to send in the stable slot, and zero or
            one message describing what changed since it was taken.
        """
        import difflib

        extra = getattr(ctx, "extra", None)
        if extra is None or not stable:
            return stable, []

        current = stable[0].text
        snapshot = extra.get("_capability_snapshot")
        if snapshot is None:
            extra["_capability_snapshot"] = current
            return stable, []
        if snapshot == current:
            return stable, []

        import re as _re

        def _blocks(text: str) -> Dict[str, str]:
            # Leaf blocks only, and excluded *while matching* rather than after. The
            # container's own tag matches this pattern too, and being outermost it wins:
            # one lazy match swallows the whole catalog, so discarding it afterwards
            # discards every leaf inside it and the diff comes back empty.
            return {m.group(1): m.group(2)
                    for m in _re.finditer(
                        r"<((?!capability-context)[a-z-]+-context)>(.*?)</\1>", text, _re.S)}

        old_blocks, new_blocks = _blocks(snapshot), _blocks(current)
        sections: List[str] = []
        # Per leaf block, so an added skill is announced inside a `<skill-context>` and
        # an added tool inside a `<tool-context>` — the same vocabulary the catalog
        # uses. A new capability is not a new *kind* of thing.
        for capability_type in sorted(set(old_blocks) | set(new_blocks)):
            before = old_blocks.get(capability_type, "").splitlines()
            after = new_blocks.get(capability_type, "").splitlines()
            diff = list(difflib.unified_diff(before, after, n=0, lineterm=""))
            added = [l[1:].strip() for l in diff if l.startswith("+") and not l.startswith("+++")]
            removed = [l[1:].strip() for l in diff if l.startswith("-") and not l.startswith("---")]
            added = [l for l in added if l]
            removed = [l for l in removed if l]
            if not added and not removed:
                continue
            body = [f"  <{capability_type}>"]
            body += [f"    now available: {l}" for l in added]
            body += [f"    no longer available, do not call: {l}" for l in removed]
            body.append(f"  </{capability_type}>")
            sections.append("\n".join(body))

        if not sections:
            return [HumanMessage(content=snapshot)], []

        # Measured on the change content, not on the wrapped message: the container and
        # its explanatory line are a fixed ~190 characters that do not grow with the
        # number of changes, and counting them made every small catalog re-freeze on its
        # first change — the opposite of what the ratio is for.
        delta = sum(len(s) for s in sections)
        if (delta > Agent._REFREEZE_RATIO * len(snapshot)
                and delta > Agent._REFREEZE_MIN_CHARS):
            extra["_capability_snapshot"] = current
            return stable, []
        # Wrapped in a container that mirrors `<capability-context>`, so the leaf blocks
        # inside it read as an update to that catalog rather than as a second, competing
        # one. The name is derived from a container the prompt already defines — before
        # the catalogs were merged there was nothing for it to refer to, and a
        # free-standing change log had to introduce a concept of its own.
        addition = "\n".join([
            "<capability-context-changes>",
            "Changes to the capability-context above, which was taken when this "
            "conversation started. It still stands except as stated here:",
            *sections,
            "</capability-context-changes>",
        ])

        # The frozen snapshot goes out unchanged; only this addition is new.
        return [HumanMessage(content=snapshot)], [HumanMessage(content=addition)]

    @staticmethod
    def _split_rendered_turn(rendered: List[Message]) -> Tuple[List[Message], List[Message]]:
        """Partition the rendered turn by what changes between steps, not by origin.

        The rendered turn is one message carrying several things at once, and they do
        not belong in the same place once history is a real conversation:

        - The capability catalogs (tools, skills, connectors, workflows) are **identical
          every step**. They are stable content and belong ahead of the history, where a
          cache can keep them.
        - `task` and `memory` are already the projection — the opening turn and the
          conversation itself — so repeating them would state each twice.
        - What is left (`agent-context`: budget, step guidance, plan, workspace, errors)
          genuinely changes every step, and belongs after the last stable byte.

        Getting this split wrong is expensive and quiet. Sending the catalogs *after* the
        history put 61,000 unchanging characters beyond the last reusable byte and cut
        prefix reuse to 20% — no better than not projecting at all. "It came from the
        per-step render" is not the same question as "does it change per step".

        Returns:
            ``(stable, volatile)`` — each a list of zero or one message.
        """
        import re as _re

        turn = next((m for m in reversed(rendered) if getattr(m, "role", "") != "system"), None)
        if turn is None:
            return [], []
        text = getattr(turn, "text", "") or ""

        # One container, so the split follows the template rather than a list of tag
        # names kept in sync with it by hand. The bare blocks are still matched, for
        # prompts written before the container existed.
        stable_parts = []
        for block in ("capability-context", "tool-context", "skill-context",
                      "connector-context", "workflow-context"):
            for match in _re.finditer(rf"<{block}>.*?</{block}>", text, _re.S):
                stable_parts.append(match.group(0))
            text = _re.sub(rf"<{block}>.*?</{block}>", "", text, flags=_re.S)

        # Already carried by the projection.
        for block in ("task", "memory"):
            text = _re.sub(rf"<{block}>.*?</{block}>", "", text, flags=_re.S)

        def _turn(body: str) -> List[Message]:
            # Tags alone are not content: a wrapper left holding nothing adds a turn
            # that says nothing. Unrecognised prose *is* content and is kept — a prompt
            # template this code has not seen must not lose what it says.
            return [HumanMessage(content=body.strip())] if _re.sub(r"<[^>]+>", "", body).strip() else []

        return _turn("\n".join(stable_parts)), _turn(text)


    # ------------------------------------------------------------------
    # Shared execution loop — all tool-calling agents use this
    # ------------------------------------------------------------------

    async def _constraint_check(self, task_id: str, ctx: "AgentContext"):
        """Run the per-step resource-budget check via the constraint hook, exactly once.

        The agent owns its token accounting: it pops the previous step's tokens
        and passes the per-call budget in; the hook runs the checks, blocks on a
        violation, and returns the budget snapshot.

        Returns ``(violation_reason, status_list)``:
          - ``violation_reason``: a string when a budget is exhausted (the caller
            should stop the task), else ``None``;
          - ``status_list``: the budget snapshot to render into the prompt.

        Stateful (step/token counters increment per call), so every agent calls it
        exactly once per step — at the top of the loop, before ``_get_messages`` —
        and never inside ``_think_and_act``. See the canonical loop in
        ``_think_and_act``'s docstring.
        """
        if not self.constraints:
            return None, []
        from agentevolver.hook.server import hook_manager
        from agentevolver.hook.types import HookDecision, HookEvent
        result = await hook_manager(
            name="constraint_hook",
            input={
                "event": HookEvent.PRE_STEP,
                "agent_name": self.name,
                "task_id": task_id,
                "constraint_names": [c.name for c in self.constraints],
                "check_input": {
                    "token": self._pending_step_tokens.pop(task_id, 0),
                    "max_step": self.max_step,
                    "max_token": self.max_token,
                    "max_second": self.timeout,
                },
            },
            ctx=ctx,
        )
        if result.decision == HookDecision.BLOCK:
            for c in self.constraints:
                # Freed by the key the constraint actually counts under (ctx.id).
                # This passed task_id — a per-invocation uuid the constraint never
                # sees — so nothing was ever released and a session's budget only
                # ever went up.
                c._cleanup(ctx.id)
            self._pending_step_tokens.pop(task_id, None)
            return result.reason, []
        return None, result.constraint_status or []

    async def _think_and_act(
        self,
        messages: List[Message],
        task_id: str,
        step_number: int,
        ctx: "AgentContext",
        **kwargs,
    ) -> Dict[str, Any]:
        """One step of the think-and-act loop (native tool use): the model sees the
        agent's capabilities as native tools, emits tool_calls, and this dispatches
        each back to its owning manager. Returns done/result/reasoning/action_errors.

        Reasoning is the model's thinking/text; completion is an explicit ``done``
        tool call (never inferred from plain text — a text-only turn is nudged to
        act or call done). Tool args arrive as structured objects validated by each
        tool's schema (no JSON-string double-encoding).

        The per-step resource-budget check is the CALLER's responsibility — every
        agent runs ``_constraint_check`` BEFORE building ``messages`` (so the prompt
        reflects the current budget) and stops on a violation. The check is stateful
        (counts a step), so it must run exactly once per step and is NOT repeated
        here. Canonical loop every agent follows::

            step = 0
            action_errors = []
            while step < self.max_step:
                reason, status = await self._constraint_check(task_id, ctx)
                if reason is not None:
                    response = {"done": True, "result": reason,
                                "stopped_by_constraint": True}
                    break
                messages = await self._get_messages(
                    task, ctx=ctx, step_number=step,
                    action_errors=action_errors, constraint_status=status)
                response = await self._think_and_act(messages, task_id, step, ctx=ctx)
                step += 1
                action_errors = response.get("action_errors") or []
                if response["done"]:
                    break
        """
        # THINK: one LLM turn → a batch of tool_calls (+ routing). Pure decision.
        decision = await self._think(messages, task_id, step_number, ctx)

        # DISPATCH: run this turn's batch concurrently, each call routed to its manager.
        outcome = await self._dispatch(decision, task_id, step_number, ctx)

        # Per-step lifecycle: POST_STEP + trajectory capture.
        await self._post_step(task_id, step_number, ctx, messages,
                              reasoning=decision["reasoning"],
                              assistant_text=decision.get("assistant_text", ""),
                              provider_state=decision.get("provider_state", {}),
                              plan=outcome["plan"],
                              step_tokens=decision["step_tokens"], step_usage=decision.get("step_usage"),
                              done=outcome["done"])

        return {"done": outcome["done"], "result": outcome["result"], "reasoning": outcome["reasoning"],
                "action_errors": outcome["action_errors"], "constraint_status": [], "stopped_by_constraint": False}

    async def _post_step(self, task_id, step_number, ctx, messages, *, reasoning,
                         assistant_text="", provider_state=None, plan, step_tokens, done,
                         step_usage=None):
        """Fire the per-step POST_STEP lifecycle (memory / trace / trajectory)
        and carry token usage forward. Shared by the blocking ``_think_and_act`` path
        (BrowserAgent) and the event-driven round loop, so a step is recorded identically
        however it was driven.
        """
        from agentevolver.hook.server import hook_manager
        from agentevolver.hook.types import HookEvent
        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.POST_STEP, "agent_name": self.name,
                   "step_number": step_number, "task_id": task_id,
                   "reasoning": reasoning, "assistant_text": assistant_text,
                   "provider_state": provider_state or {},
                   "step_usage": step_usage,
                   "use_memory": self.use_memory, "memory_name": self.memory_name},
            ctx=ctx,
        )
        await hook_manager(
            name="trajectory_hook",
            input={"event": HookEvent.POST_STEP, "agent_name": self.name, "step_number": step_number,
                   "task_id": task_id, "messages": messages, "reasoning": reasoning,
                   "plan": plan, "step_tokens": step_tokens, "step_usage": step_usage},
            ctx=ctx,
        )
        from agentevolver.trace.checkpoint import (
            TraceCheckpointBoundary,
            checkpoint_trace,
        )
        await checkpoint_trace(
            str(ctx.id),
            TraceCheckpointBoundary.STEP_END,
            ctx=ctx,
            metadata={
                "task_id": task_id,
                "agent_name": self.name,
                "step_number": step_number,
            },
        )
        self._pending_step_tokens[task_id] = step_tokens
        if done and self.constraints:
            for c in self.constraints:
                c._cleanup(ctx.id)  # the key it counts under; see _constraint_check
            self._pending_step_tokens.pop(task_id, None)

    # ------------------------------------------------------------------
    # The unified loop's two verbs: _think (decide) + _dispatch (act).
    # Shared verbatim by every agent — leaf actors AND the MetaAgent. The only
    # thing an orchestrator adds is a richer roster (include_agents / extra_tools)
    # and a different batch executor; the decision + per-call dispatch are identical.
    # ------------------------------------------------------------------

    async def _think(
        self,
        messages: List[Message],
        task_id: str,
        step_number: int,
        ctx: "AgentContext",
        *,
        include_agents: bool = False,
        extra_tools: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """One LLM turn (native tool use): project the agent's capabilities into a flat
        tool list + routing table, stream, and accumulate into a batch of tool_calls.

        Pure decision — no dispatch, no state mutation — so both the leaf-agent loop and
        MetaAgent call this same method. ``include_agents`` projects registered sub-agents
        into the roster; ``extra_tools`` appends any extra schema-only tools. Returns
        ``{tool_calls, routing, reasoning, step_tokens, error}``.
        """
        from agentevolver.hook.server import hook_manager
        from agentevolver.hook.types import HookEvent
        from agentevolver.model import model_manager
        from agentevolver.model.types import accumulate_stream
        from agentevolver.agent.native_tools import assemble_native_tools

        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.PRE_STEP, "agent_name": self.name, "step_number": step_number, "task_id": task_id},
            ctx=ctx,
        )

        tools, routing = await assemble_native_tools(self, ctx, include_agents=include_agents)
        if extra_tools:
            tools = tools + list(extra_tools)

        reasoning = ""
        assistant_text = ""
        provider_state = {}
        tool_calls: List[Any] = []
        step_tokens = 0
        step_usage: Optional[Dict[str, Any]] = None
        think_error: Optional[str] = None
        overflowed = False
        try:
            acc = await accumulate_stream(
                model_manager.stream(
                    name=self.model_name,
                    input={
                        "messages": messages,
                        "tools": tools,
                        # ModelContext knows the session, but not which agent step owns
                        # the call. These coordinates let the request snapshot become a
                        # precise trace fact that trajectory can cite instead of guessing
                        # from timestamps or call order.
                        "trace_context": {
                            "task_id": task_id,
                            "agent_name": self.name,
                            "step_number": step_number,
                        },
                        "compaction_policy": {
                            "retain_recent_steps": getattr(self, "retain_recent_steps", 4),
                            "compact_after_steps": getattr(self, "compact_after_steps", 24),
                            "compact_body_tokens": getattr(
                                self, "compact_body_tokens", 100_000,
                            ),
                            "fold_at_pressure": getattr(self, "fold_at_pressure", 0.85),
                        },
                        "runtime_features": {
                            "programmatic_tool_calling": getattr(
                                self, "prefer_native_programmatic_tools", True,
                            ),
                            # Native multi-agent is an orchestration backend, never a way
                            # to silently turn a leaf actor into an orchestrator.
                            "multi_agent": getattr(
                                self, "prefer_native_multi_agent", False,
                            ) and include_agents,
                            "max_concurrent_subagents": getattr(
                                self, "native_max_concurrent_subagents", 3,
                            ),
                        },
                        **({
                            "trace_integrity_profile": ctx.extra["trace_integrity_profile"],
                        } if (ctx.extra or {}).get("trace_integrity_profile") else {}),
                    },
                    ctx=ctx,
                )
            )
            # The whole usage record, not just the completion count. Every provider
            # already reports input, cache_write and cache_read alongside it; taking
            # `output_tokens` alone discarded them here, at the first hop, which is why
            # no durable record downstream could say whether a prompt was ever cached.
            # `step_tokens` keeps its old meaning — it feeds the trajectory's reward —
            # and the full record travels beside it.
            from agentevolver.model.types import TokenUsage
            usage = TokenUsage.from_raw(acc.get("usage"))
            if usage is not None:
                step_usage = usage.model_dump()
                step_tokens = usage.output_tokens
            assistant_text = acc.get("text") or ""
            provider_state = acc.get("provider_state") or {}
            reasoning = acc.get("thinking") or assistant_text
            tool_calls = acc.get("tool_calls") or []
        except Exception as e:
            from agentevolver.model.pressure import ContextOverflowError
            from agentevolver.trace.checkpoint import TraceIntegrityError
            if isinstance(e, TraceIntegrityError):
                raise
            think_error = str(e)
            # Separated from an ordinary model error because the answer is different.
            # Another attempt at the same prompt cannot succeed, and counting it toward
            # the give-up threshold spends the run's remaining patience on a request that
            # was decided before it was sent.
            overflowed = isinstance(e, ContextOverflowError)
            logger.error(f"| ❌ [{self.name}] Error in _think: {e}")

        tool_calls = self._cap_actions(tool_calls)

        logger.info(f"| 💭 [{self.name}] Reasoning: {reasoning[:200]}")
        logger.info(f"| 🔧 [{self.name}] Tool calls: {[c.name for c in tool_calls]}")

        # The error travels with the decision. Swallowed, it is indistinguishable from a
        # model that simply chose to say something without calling a tool — so the turn
        # retries, and a broken model spends the entire budget looking like an agent
        # thinking out loud.
        return {"tool_calls": tool_calls, "routing": routing, "reasoning": reasoning,
                "assistant_text": assistant_text,
                "provider_state": provider_state,
                "step_tokens": step_tokens, "step_usage": step_usage, "error": think_error,
                "overflowed": overflowed}

    def _cap_actions(self, tool_calls: List[Any]) -> List[Any]:
        """Hold a turn's batch to ``max_actions``, keeping the earliest calls.

        ``max_actions`` used to be advisory — it was rendered into the prompt and
        nothing enforced it, so a model that decided to fan out could return any
        number of calls. Observed on ProgramBench once the batching guidance landed:
        71 turns produced 943 tool calls, one turn asking for hundreds of
        ``readelf | grep <symbol>`` probes, and a quarter of the run's budget went
        into static analysis the task forbids.

        Truncating beats accepting. The tail of an oversized batch is speculative by
        construction — it was planned without seeing any of the earlier results — and
        running it crowds out the turns that would have used those observations. The
        earliest calls are kept for the same reason.
        """
        if len(tool_calls) <= self.max_actions:
            return tool_calls
        dropped = len(tool_calls) - self.max_actions
        logger.warning(
            f"| ✂️ [{self.name}] {len(tool_calls)} tool calls exceeds "
            f"max_actions={self.max_actions}; keeping the first {self.max_actions}, "
            f"dropping {dropped}"
        )
        return tool_calls[: self.max_actions]

    async def _dispatch(
        self,
        decision: Dict[str, Any],
        task_id: str,
        step_number: int,
        ctx: "AgentContext",
    ) -> Dict[str, Any]:
        """Run this turn's batch of tool_calls CONCURRENTLY, each routed to its manager.

        A single turn's batch is parallel-safe by the function-calling contract — the
        model only puts independent calls in one batch; dependent work is emitted across
        turns — so we gather the whole batch. Returns
        ``{done, result, reasoning, action_errors, plan}``.
        """
        import json as _json

        tool_calls = decision["tool_calls"]
        routing = decision["routing"]
        reasoning = decision["reasoning"]
        action_errors: List[str] = []
        step_plan = [
            {"id": c.id, "description": "", "type": (routing.get(c.name) or ("tool",))[0],
             "name": c.name, "args": _json.dumps(c.input, ensure_ascii=False)}
            for c in tool_calls
        ]

        # A text-only turn is NOT completion — completion is an explicit `done` tool call,
        # never inferred from plain text. Nudge the model to act or call `done`.
        if not tool_calls:
            action_errors.append(
                "You produced text but called no tool. Take the next action by calling a tool, "
                "or if the task is COMPLETE call `done` with the result now. Do not answer in plain text."
            )
            logger.warning(f"| ⚠️ [{self.name}] No tool call — nudging to act or call done.")
            return {"done": False, "result": None, "reasoning": reasoning, "action_errors": action_errors, "plan": step_plan}

        if await self._batch_requires_serial(tool_calls, routing):
            outcomes = []
            for i, call in enumerate(tool_calls):
                outcomes.append(await self._run_one(
                    call, i, routing, task_id, step_number, ctx,
                ))
        else:
            outcomes = await asyncio.gather(*[
                self._run_one(call, i, routing, task_id, step_number, ctx)
                for i, call in enumerate(tool_calls)
            ])

        done = False
        result = None
        for o in outcomes:
            if o.get("error"):
                action_errors.append(f"Action '{o['name']}' failed: {o['error']}")
            if o.get("done"):
                done = True
                result = o.get("result")
                if o.get("reasoning"):
                    reasoning = o["reasoning"]
        return {"done": done, "result": result, "reasoning": reasoning, "action_errors": action_errors, "plan": step_plan}

    async def _run_one(
        self,
        call: Any,
        index: int,
        routing: Dict[str, Any],
        task_id: str,
        step_number: int,
        ctx: "AgentContext",
        parent_ref: Any = None,
        parent_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatch ONE tool_call, wrapped in its PRE_ACTION → invoke → POST_ACTION hooks.

        This is the atomic unit of action; the batch executor runs one per tool_call,
        concurrently. Keeping a call's hook pair inside one coroutine means the pairs
        stay correct even when the batch runs in parallel. ``parent_ref`` is this agent's
        own runtime ref, threaded through so an ``agent`` call can spawn its child with a
        parent to escalate to. Returns ``{name, done, result, reasoning, error}``.
        """
        import json as _json
        from agentevolver.hook.server import hook_manager
        from agentevolver.hook.types import HookDecision, HookEvent

        route = routing.get(call.name)
        capability_type = route[0] if route else "tool"
        args_str = _json.dumps(call.input, ensure_ascii=False)
        logger.info(f"| 📝 [{self.name}] [{capability_type}] {call.name}: {call.input}")

        action_dict = {
            "index": index, "id": call.id, "description": "", "type": capability_type,
            "name": call.name, "args": args_str, "args_parsed": call.input,
            # Code Mode sub-calls execute through this same method, but they were not
            # emitted by the model. The parent id lets a training projector keep their
            # observations while excluding them from the assistant target's tool_calls.
            "parent_call_id": parent_call_id,
            "caller": getattr(call, "caller", None),
        }

        done, result, reasoning, error, action_result = False, None, None, None, None
        execution_meta: Optional[Dict[str, Any]] = None

        pre_result = await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.PRE_ACTION, "agent_name": self.name,
                   "step_number": step_number, "action": action_dict,
                   "task_id": task_id, "use_memory": self.use_memory,
                   "memory_name": self.memory_name},
            ctx=ctx,
        )
        if pre_result.decision == HookDecision.BLOCK:
            logger.warning(f"| 🚫 [{self.name}] Action blocked by hook: {pre_result.reason}")
            return {"name": call.name, "done": False, "result": None, "reasoning": None, "error": None}

        # The plan-mode gate. Dispatched by name like every other hook, and separate
        # from the trace hook above because its refusal has to reach the *model*: the
        # reason names `exit_plan_mode` as the way out, and a block returned with
        # `error: None` would leave the agent re-issuing the same call against a wall
        # it cannot see. Allows everything when no run is in plan mode.
        plan_gate = await hook_manager(
            name="plan_mode_hook",
            input={"event": HookEvent.PRE_ACTION, "agent_name": self.name, "step_number": step_number, "action": action_dict, "task_id": task_id},
            ctx=ctx,
        )
        guard_denials: List[str] = []
        if plan_gate.decision == HookDecision.BLOCK:
            logger.warning(f"| 🚫 [{self.name}] {plan_gate.reason}")
            # Tool refusals enter the same execution pipeline as allowed calls. That
            # produces a classified, correlated result without invoking the body. Other
            # capability kinds do not use ToolExecution and retain their existing gate.
            if capability_type == "tool" and route is not None:
                guard_denials.append(str(plan_gate.reason or "Blocked by plan mode."))
            else:
                return {"name": call.name, "done": False, "result": None,
                        "reasoning": None, "error": plan_gate.reason}

        if (route is not None and self.permission_mode == "read_only" and capability_type == "tool"
                and route[1] in _READ_ONLY_DENIED_TOOLS
                and not self._allow_read_only_tool_call(route[1], call.input or {})):
            guard_denials.append(
                f"read_only agent '{self.name}' may not invoke framework-mutating "
                f"tool '{route[1]}'. Report findings instead of modifying anything."
            )

        try:
            if route is None:
                raise ValueError(f"Unknown tool '{call.name}' (not in the assembled tool set)")
            # `batch_call_tool` carries a program whose tool calls come back through THIS
            # method. It is the only dispatch handed a way to dispatch, and it is handed
            # one bound to this turn — so a call from inside a program is checked against
            # the same roster, gated by the same plan mode, and recorded by the same
            # hooks as the call the model could have made itself.
            bridge = None
            if capability_type == "tool" and route[1] == BATCH_CALL_TOOL:
                bridge = self._guarded_dispatch(routing, task_id, step_number, ctx,
                                                parent_ref, call.id)
            tool_execution_context = {
                "call_id": str(call.id or ""),
                "root_call_id": str(parent_call_id or call.id or ""),
                "parent_call_id": parent_call_id,
                "session_id": str(ctx.id or ""),
                "task_id": task_id,
                "agent_name": self.name,
                "step_number": step_number,
                "action_index": index,
                "guard_denials": guard_denials,
            }
            (action_result, done, result, reasoning, capability_error,
             execution_meta) = await self._invoke_capability(
                route, call, ctx, parent_ref, bridge,
                tool_execution_context=tool_execution_context,
            )
            if capability_error:
                error = capability_error
        except Exception as e:
            from agentevolver.trace.checkpoint import TraceIntegrityError
            if isinstance(e, TraceIntegrityError):
                # Tool Manager performs this after every guard and any approval, directly
                # before the body. Strict integrity failures are run-level failures, not
                # ordinary Tool observations that the model may retry around.
                raise
            error = str(e)
            logger.error(f"| ❌ [{self.name}] Action '{call.name}' failed: {e}")

        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.POST_ACTION, "agent_name": self.name,
                   "step_number": step_number, "action": action_dict,
                   "action_result": action_result, "task_id": task_id, "error": error,
                   "execution_meta": execution_meta, "use_memory": self.use_memory,
                   "memory_name": self.memory_name},
            ctx=ctx,
        )
        await hook_manager(
            name="trajectory_hook",
            input={"event": HookEvent.POST_ACTION, "agent_name": self.name, "step_number": step_number, "action": action_dict, "action_result": action_result, "task_id": task_id, "error": error, "execution_meta": execution_meta},
            ctx=ctx,
        )
        return {"name": call.name, "done": done, "result": result, "reasoning": reasoning,
                "error": error, "output": action_result, "execution": execution_meta}

    def _guarded_dispatch(
        self,
        routing: Dict[str, Any],
        task_id: str,
        step_number: int,
        ctx: "AgentContext",
        parent_ref: Any,
        parent_call_id: str,
    ) -> GuardedDispatch:
        """A callable that runs ONE tool call through this agent's own action path.

        Handed to `batch_call_tool` so a program's `await tools.x(...)` is dispatched by
        `_run_one`, not by a private line to the tool manager. That distinction is the
        whole safety argument for code mode: everything a call is checked by lives in
        `_run_one` and in the tool itself, so a second dispatcher — however careful — would
        be a second copy of the plan-mode gate, the read-only refusal, the permission
        check and four hook firings, silently drifting from this one.

        Only ``tool`` routes are bound. Skills, connectors and sub-agents dispatch through
        the same method and would work, but the model is told what it may call by a
        declaration block generated from the tool roster, and binding names it was never
        shown buys nothing.
        """
        from agentevolver.model.types import ToolCall
        from agentevolver.tool.default.code_mode.sdk import callable_names

        names = tuple(callable_names(
            [name for name, route in routing.items() if (route or ("tool",))[0] == "tool"]))
        served = itertools.count(1)

        async def dispatch(name: str, args: Dict[str, Any]) -> str:
            if name not in names:
                raise LookupError(
                    f"'{name}' is not callable from a program here. Callable: "
                    f"{', '.join(names) or 'none'}.")
            # A distinct id per sub-call, derived from the program's own call id, so the
            # trace shows which program each action came out of instead of a flat run of
            # unattributed calls.
            sub_call = ToolCall(id=f"{parent_call_id}#{next(served)}", name=name, input=args or {})
            outcome = await self._run_one(sub_call, 0, routing, task_id, step_number, ctx,
                                          parent_ref=parent_ref, parent_call_id=parent_call_id)
            if outcome.get("error"):
                raise RuntimeError(str(outcome["error"]))
            output = outcome.get("output")
            if output is None:
                # `_run_one` returns this shape when a hook blocked the action before it
                # ran. Returning it as an empty success would tell the program the tool
                # did its work and had nothing to say.
                raise RuntimeError(f"'{name}' was blocked before it ran.")
            return str(output)

        return GuardedDispatch(names=names, call=dispatch)

    async def _invoke_capability(
        self,
        route: Any,
        call: Any,
        ctx: "AgentContext",
        parent_ref: Any = None,
        bridge: Optional[GuardedDispatch] = None,
        tool_execution_context: Optional[Dict[str, Any]] = None,
    ):
        """Route ONE call to the manager that owns it — the single dispatch table that
        knows how each capability kind executes. Returns
        ``(action_result, done, result, reasoning, error, execution_metadata)``.

        ``bridge`` is set only for `batch_call_tool`, and is that tool's only way to reach
        another capability.

        ``agent`` is a capability like any other: dispatching one runs a sub-agent to
        completion via the runtime, with this agent as its parent (so the child can
        escalate back up). This is what lets an orchestrator use the very same loop as a
        leaf actor — a sub-agent is just another tool it can call.

        ``run_in_background`` is the one branch: the dispatch returns a job id as soon as
        the child holds its brief, instead of the child's result. Everything else about
        the delegation — what the child inherits, what it must not, who it escalates to
        — is identical, because the only thing backgrounding changes is whether this
        agent spends its own steps waiting.
        """
        capability_type = route[0]
        if capability_type == "capability_search":
            from agentevolver.agent.capability_index import catalog, search

            output = search(
                catalog(ctx, self.name),
                ctx=ctx,
                agent_name=self.name,
                query=str((call.input or {}).get("query") or ""),
                limit=int((call.input or {}).get("limit") or 6),
            )
            return output, False, None, None, None, None
        if capability_type == "agent":
            from agentevolver.agent.server import agent_manager
            from agentevolver.runtime import runtime_manager
            child = await agent_manager.get(route[1])
            if child is None:
                raise ValueError(f"No registered agent named {route[1]!r}")
            inp = call.input or {}
            # Attachments carry across a delegation unless the dispatch names its own.
            # The child is working on part of the same task, and the source material it
            # needs is whatever came with that task; without this it gets only the
            # orchestrator's paraphrase of a document the orchestrator was itself given
            # in full. Any specifics belong in the dispatched `task` text, not in
            # withholding the material.
            ambient_files = (getattr(ctx, "extra", None) or {}).get("task_files")
            brief = dict(
                files=inp.get("files") or ambient_files, target_name=inp.get("target_name"),
                target_type=inp.get("target_type"),
                allowlists={
                    k: inp.get(k) for k in (
                        "tool_allowlist", "skill_allowlist", "connector_allowlist",
                        "environment_allowlist", "workflow_allowlist",
                    )
                },
                parent_ref=parent_ref,
                # The child executes wherever this agent executes — most importantly
                # inside the same peer sandbox, which both bash_tool and the prompt's
                # workspace slot read off the context.
                parent_ctx=ctx,
                fork=bool(inp.get("fork")),
            )
            if inp.get("run_in_background"):
                started = await runtime_manager.delegate_background(
                    child, inp.get("task", ""), continuable=bool(inp.get("continuable")), **brief)
                logger.info(f"| 🚀 [{self.name}] Sub-agent '{route[1]}' backgrounded as "
                            f"{(started.data or {}).get('job_id')}")
                return started.message, False, None, None, None, None
            resp = await runtime_manager.delegate(child, inp.get("task", ""), **brief)
            # Hand back the child's result plus a deterministic completion envelope, so the
            # orchestrator can tell a finished result from a cut-off one — whether or not the
            # child called done_tool, and without an exception discarding the work.
            output = (resp.message or "") + _delegation_summary(resp.data)
            if resp.success:
                logger.info(f"| ✅ [{self.name}] Sub-agent '{route[1]}' completed (success=True)")
                return output, False, None, None, None, None
            # A not-finished dispatch splits two ways. A worker that hit its step ceiling or a
            # resource limit has usually produced real work (a tight worker budget makes this
            # routine) — that is a partial result to build on, not an error, so its work and
            # envelope go back with no error flag. But success=False also covers genuine
            # failures — model-not-found, a run of empty turns, repeated think failures — and
            # those must still surface as a failed action rather than be mistaken for partial
            # progress; they keep the error flag (while still handing back whatever text and
            # envelope exist, which is never worse than the bare exception this replaced).
            data = resp.data or {}
            step, max_step = data.get("step"), data.get("max_step")
            hit_ceiling = step is not None and max_step and step >= max_step
            partial_stop = hit_ceiling or data.get("stopped_by_constraint")
            if partial_stop:
                logger.warning(f"| ⚠️ [{self.name}] Sub-agent '{route[1]}' did not finish "
                               f"(step {step}/{max_step}) — handing back its partial result")
                return output, False, None, None, None, None
            logger.warning(f"| ❌ [{self.name}] Sub-agent '{route[1]}' failed: {(resp.message or '')[:120]}")
            return output, False, None, None, (resp.message or f"Sub-agent {route[1]!r} failed"), None
        if capability_type in _PLAIN_CAPABILITY_TYPES:
            # Skill, connector, environment, workflow and plugin differ only in which
            # manager answers and whether the route names a member. Four copies of
            # these five lines is where "the fifth one behaves slightly differently"
            # came from, so they run one path.
            entry = mounted_type_entry(capability_type)
            member = route[2] if len(route) > 2 else ""
            label = f"{capability_type.capitalize()} {route[1]!r}" + (f" action {member!r}" if member else "")
            manager = entry.manager()
            response = (await manager(name=route[1], action=member, input=call.input or {}, ctx=ctx)
                        if entry.container
                        else await manager(name=route[1], input=call.input or {}, ctx=ctx))
            if not response.success:
                raise RuntimeError(response.message or f"{label} failed")
            logger.info(f"| ✅ [{self.name}] {label} completed")
            return response.message, False, None, None, None, None
        if capability_type == "tool":
            passthrough = {"sub_dispatch": bridge} if bridge is not None else {}
            tool_response = await tool_manager(
                name=route[1], input=call.input, ctx=ctx,
                execution_context=tool_execution_context, **passthrough,
            )
            execution_meta = dict((tool_response.extra or {}).get("execution") or {})
            if not tool_response.success:
                message = tool_response.message or f"Tool {route[1]!r} failed"
                return message, False, None, None, message, execution_meta
            logger.info(f"| ✅ [{self.name}] Tool '{route[1]}' completed")
            if route[1] == "done_tool":
                reasoning = (tool_response.data or {}).get("reasoning") if hasattr(tool_response, "data") else None
                return (tool_response.message, True, tool_response.message, reasoning,
                        None, execution_meta)
            return tool_response.message, False, None, None, None, execution_meta
        raise ValueError(f"Unknown route type {capability_type!r}")

    # ------------------------------------------------------------------
    # Path 1: Direct call
    # ------------------------------------------------------------------

    async def __call__(self,
                       task: Optional[str] = None,
                       files: Optional[List[str]] = None,
                       ctx: Optional[AgentContext] = None,
                       **kwargs: Any,
                       ) -> "Response":
        """Synchronous entry point: run this agent to completion and return its Response.

        Delegates to the runtime — spawn a pump, deliver the task, await the result — so
        the SAME event-driven loop (on_start → rounds → _conclude) runs whether the agent
        is called directly here or dispatched as a sub-agent by an orchestrator. Post-run
        work that used to live in a ``__call__`` override (generate/optimize registration)
        now hangs off ``_finalize_run``, so it fires on every path.
        """
        from agentevolver.runtime import runtime_manager
        return await runtime_manager.invoke(self, task=task, files=files, ctx=ctx, **kwargs)

    # ------------------------------------------------------------------
    # Path 2: Event-driven (runtime / mailbox)
    # ------------------------------------------------------------------

    async def on_start(self,
                       task: str,
                       files: Optional[List[str]],
                       ctx: Optional[AgentContext],
                       ref: Any,
                       **kwargs: Any,
                       ) -> Optional["Response"]:
        """Runtime pump entry: initialise the run, emit ON_START lifecycle hooks, and
        kick the first turn. Always returns ``None`` — the result is delivered later by
        ``_conclude`` (which resolves the caller's reply). This is THE loop every agent
        uses; orchestrators only widen the roster and override a couple of seams."""
        from agentevolver.hook.server import hook_manager
        from agentevolver.hook.types import HookEvent
        from agentevolver.utils.name_utils import make_id

        logger.info(f"| 🚀 Starting {self.name}: {task}")
        if ctx is None:
            ctx = AgentContext()
        if not config.workspace_root:
            config.workspace_root = self.base_dir
        if files:
            logger.info(f"| 📂 Attached files: {files}")
            # Recorded on the context so a delegation can pass them on without the
            # orchestrator having to remember to. A sub-agent doing part of this task
            # needs the same source material, and the alternative is that it works from
            # the orchestrator's summary of a document it could have read itself.
            if getattr(ctx, "extra", None) is not None:
                ctx.extra.setdefault("task_files", list(files))

        # Gateways already own a public task id.  Reuse it when supplied so task events,
        # trace events, memory, and cancellation all describe one execution identity.
        task_id = str(kwargs.pop("task_id", "") or make_id())
        run = _AgentRun(task, files, ctx, ref, task_id, kwargs)
        self._runs[ref.name] = run

        for hook_name in ("trace_hook", "trajectory_hook"):
            await hook_manager(
                name=hook_name,
                input={"event": HookEvent.ON_START, "task": task, **self._lifecycle_input(run)},
                ctx=ctx,
            )

        await self._advance(run)
        return None

    async def on_event(self, msg: Any, ref: Any) -> None:
        """Runtime pump: collect a finished action (round bookkeeping) and, when the
        round drains, advance to the next turn or conclude. Non-action messages
        (escalations, progress) go to ``_handle_extra_event`` for orchestrators."""
        run = self._runs.get(ref.name)
        if run is None:
            return
        if isinstance(msg, _ActionDone):
            if msg.call_id in run.outstanding:
                run.outstanding.discard(msg.call_id)
                run.round_tasks.pop(msg.call_id, None)
                run.round_outcomes.append({"id": msg.call_id, "name": msg.name, "output": msg.output, "result": msg.result, "error": msg.error, "is_done": msg.is_done})
                if msg.error:
                    run.round_errors.append(f"Action '{msg.name}' failed: {msg.error}")
                if msg.is_done:
                    run.round_done = True
                    run.round_result = msg.result
                    run.round_reasoning = msg.reasoning
            if not run.outstanding:
                await self._on_round_complete(run)
        elif isinstance(msg, _ControlMessage):
            await self._handle_control(run, msg)
        elif isinstance(msg, _QueryMessage):
            if msg.reply_future is not None and not msg.reply_future.done():
                msg.reply_future.set_result(self._snapshot(run))
        else:
            await self._handle_extra_event(run, msg)

    async def _handle_control(self, run: "_AgentRun", msg: "_ControlMessage") -> None:
        """Control channel: cancel concludes gracefully; pause/resume gate advancing."""
        if msg.action == "cancel":
            logger.info(f"| ✋ [{self.name}] cancel requested: {msg.reason or '(no reason)'}")
            run.done = False
            run.result = f"Cancelled: {msg.reason}" if msg.reason else "Cancelled by parent."
            run.stopped_by_constraint = True  # treated as a non-success stop
            await self._conclude(run)
        elif msg.action == "pause":
            run.paused = True
            logger.info(f"| ⏸️ [{self.name}] paused")
        elif msg.action == "resume":
            run.paused = False
            logger.info(f"| ▶️ [{self.name}] resumed")
            if not run.outstanding and not run.done:
                await self._advance(run)

    def _snapshot(self, run: "_AgentRun") -> Dict[str, Any]:
        """Query channel: a small live status snapshot of this run."""
        return {
            "agent": self.name, "task_id": run.task_id, "step": run.step,
            "running_actions": len(run.outstanding), "paused": run.paused,
            "done": run.done, "result": run.result,
        }

    # ------------------------------------------------------------------
    # The event-driven loop body (shared by every agent)
    # ------------------------------------------------------------------

    def _announce_plan_mode(self, run: "_AgentRun") -> None:
        """Say that plan mode is on, while it is on.

        Otherwise the model learns it from the first refusal — it proposes an action, the
        gate turns it away, and it has to infer a mode nobody mentioned from a single
        error. That costs a step and reads, from inside, like a malfunction.

        Not in the system prompt, which is where a mode notice would normally go. Plan
        mode is toggled mid-session; text in the system message sits ahead of the cache
        breakpoint, so switching it on would rewrite the prefix and throw away the whole
        session's cached tokens to deliver one paragraph. It rides in the volatile
        section instead, past the breakpoint, where changing it costs nothing.

        Repeated every step rather than announced once. A mode that constrains every
        action has to be legible at the moment of every action, and by the tenth step the
        opening turn is a long way back.
        """
        try:
            from agentevolver.plan import PLAN_MODE_NOTICE, plan_manager

            session_id = str(getattr(run.ctx, "id", "") or "")
            if not session_id:
                return
            if plan_manager.active(session_id):
                run.action_errors = [*(run.action_errors or []), PLAN_MODE_NOTICE]
            # `auto` says nothing here. Its notice is a standing obligation, and
            # `<errors>` means "the previous step failed" — the agent has learned that
            # block as a signal something went wrong, so an instruction placed there is
            # read against its own grain. It rides in the `<plan>` slot instead, which is
            # where the agent already looks for the plan.
        except Exception as error:                                  # noqa: BLE001
            logger.warning(f"| ⚠️ [{self.name}] could not read plan mode: {error}")


    def _deliver_due_reminders(self, run: "_AgentRun") -> None:
        """Hand the model any reminder whose time has come.

        A reminder the agent has to remember to look for is not a reminder. `schedule`
        wrote them and `job__list` could show them, but nothing pushed — so the agent
        saw a due reminder only if it happened to list its jobs, which is precisely the
        thing it set the reminder in order not to have to do.

        Delivered on the same channel as the repeat advice, and for the same reason: it
        rides beside the batch that is about to run rather than replacing it. A reminder
        is context, not an interruption, and stopping the turn to announce one would cost
        a step for something the model can simply read.

        `claim_due` consumes, so a reminder is announced exactly once. Reading it twice
        would be worse than not at all — the model would take the repetition as a new
        event and act on the same thing again.

        Never fatal: a reminder that cannot be collected must not end a run that is
        otherwise going fine.
        """
        try:
            from agentevolver.job import job_manager

            session_id = str(getattr(run.ctx, "id", "") or "")
            if not session_id:
                return
            due = job_manager.claim_due(session_id)
            if not due:
                return
            lines = [f"Reminder you set earlier is now due: {job.label}" for job in due]
            run.action_errors = [*(run.action_errors or []), *lines]
            logger.info(f"| ⏰ [{self.name}] delivered {len(due)} due reminder(s)")
        except Exception as error:                                  # noqa: BLE001
            logger.warning(f"| ⚠️ [{self.name}] could not collect due reminders: {error}")


    def _release_session_resources(self, run: "_AgentRun") -> None:
        """Reap what the run left running: sub-agents, jobs, terminals, language servers.

        Each registry had a `forget` and nothing called it. A backgrounded command, a
        PTY shell and an indexing language server outlive the step that started them by
        design — that is the whole
        point — but nothing outlived the *run* on purpose, and the only reaper was
        `atexit`. In a long-lived host that never fires, so every finished session left
        its processes behind and the machine leaked until the gateway was restarted.

        Best-effort and never fatal. A run is already over by the time this is called;
        raising here would turn a completed task into a failed one over cleanup, which
        is the worst possible trade — the work is done and the caller would never learn
        it. A registry that cannot reap says so in the log and the process still exits.
        """
        session_id = str(getattr(run.ctx, "id", "") or "")
        if not session_id:
            return
        # Sub-agents first. A background child is stopped through the manager that knows
        # how to stop one — cancel its driver, which stops its pump — and only then does
        # the job registry drop the record. The other order would forget the record while
        # the child was still calling a model, with nothing left that could name it.
        for label, forget in (("delegated children", self._forget_delegated),
                              ("jobs", self._forget_jobs),
                              ("terminals", self._forget_terminals),
                              ("attachments", self._forget_attachments)):
            try:
                forget(session_id)
            except Exception as error:                              # noqa: BLE001
                logger.warning(f"| ⚠️ [{self.name}] could not release {label} for "
                               f"{session_id}: {error}")
        try:
            from agentevolver.agent.capability_index import forget
            forget(run.ctx, self.name)
        except Exception as error:                                  # noqa: BLE001
            logger.warning(
                f"| ⚠️ [{self.name}] could not release capability catalog for "
                f"{session_id}: {error}"
            )

    @staticmethod
    def _forget_attachments(session_id: str) -> None:
        """Drop the run's live images. The committed bytes on disk are left alone."""
        from agentevolver.attachment import attachment_manager
        attachment_manager.release(session_id)

    @staticmethod
    def _forget_delegated(session_id: str) -> None:
        from agentevolver.runtime import runtime_manager
        runtime_manager.forget(session_id)

    @staticmethod
    def _forget_jobs(session_id: str) -> None:
        from agentevolver.job import job_manager
        job_manager.forget(session_id)

    @staticmethod
    def _forget_terminals(session_id: str) -> None:
        from agentevolver.terminal import terminal_manager
        terminal_manager.forget(session_id)


    def _lifecycle_input(self, run: "_AgentRun") -> Dict[str, Any]:
        """Assemble the common identity payload shared by ON_START/ON_STOP hook calls.

        Bundles the agent name, task id, memory settings, and the parent-session /
        subtask ids from the run context, so memory, trace, and trajectory hooks all
        receive a consistent lifecycle envelope.
        """
        return {
            "agent_name": self.name, "task_id": run.task_id,
            "memory_name": self.memory_name, "use_memory": self.use_memory,
            "parent_session_id": getattr(run.ctx, "parent_session_id", None),
            "subtask_id": getattr(run.ctx, "subtask_id", None),
        }

    async def _advance(self, run: "_AgentRun") -> None:
        """Turns, until one dispatches work or the run concludes.

        A loop, not recursion. Turns that continue immediately — a text-only reply, a
        proposal the no-progress guard blocked — used to recurse into this method, one
        frame per step. With a 1000-step budget that is 1000 nested frames against
        Python's default recursion limit of the same number, and the failure arrives as
        "maximum recursion depth exceeded" raised from wherever the stack happened to run
        out: in one run, from inside a Jinja template parser, naming nothing that had
        anything to do with the cause.
        """
        while True:
            concluded = await self._advance_once(run)
            if not concluded:
                return

    async def _advance_once(self, run: "_AgentRun") -> bool:
        """One turn. Returns True when the caller should immediately take another."""
        if run.step >= self.max_step:
            logger.warning(f"| 🛑 [{self.name}] Reached max steps ({self.max_step})")
            run.done, run.result = False, "The task has not been completed."
            run.reasoning = "Reached the maximum number of steps."
            await self._conclude(run)
            return False

        reason, cstatus = await self._constraint_check(run.task_id, run.ctx)
        if reason is not None:
            logger.warning(f"| 🛑 {self.name} constraint violated: {reason}")
            run.done, run.result, run.stopped_by_constraint = True, reason, True
            await self._conclude(run)
            return False

        # Landing window: the last few steps before the hard max_step stop (see
        # `_in_landing_window` / `_LANDING_RESERVE_STEPS`).
        run.landing = _in_landing_window(run.step, self.max_step)

        logger.info(f"| 🔄 [{self.name}] Step {run.step + 1}/{self.max_step}"
                    + ("  🛬 LANDING" if run.landing else ""))
        # Before the prompt is built, because `_get_messages` rebuilds it from memory and
        # trace — a fold recorded now is already shadowed out of what it renders.
        await self._fold_ahead(run)
        messages = await self._get_messages(
            run.task, ctx=run.ctx, files=run.files, step_number=run.step,
            action_errors=run.action_errors, constraint_status=cstatus, _run=run, **run.extra_kwargs)
        decision = await self._think(
            messages, run.task_id, run.step, run.ctx,
            include_agents=self._include_agents(), extra_tools=self._extra_tools(run))
        run.decision = decision
        run.messages = messages

        # A model that cannot be called produces no tool calls, so the turn looks like
        # thinking out loud and retries — for as long as the budget lasts. One run spent
        # 958 steps in 44 seconds this way and reported a stack overflow, while the
        # actual cause ("Model ... not found. Available: [...]") had been logged on the
        # very first step. Stop on the third consecutive one and report *that*.
        if decision.get("overflowed") and await self._make_room(run):
            # Not a failed step: the request was never sent, and the next rebuild is
            # smaller. Deliberately not counted toward `think_failures` — this is the one
            # model error the run can do something about.
            return True

        if decision.get("error"):
            run.think_failures += 1
            if run.think_failures >= _THINK_FAILURES_BEFORE_GIVING_UP:
                logger.error(
                    f"| 🛑 [{self.name}] giving up after {run.think_failures} consecutive "
                    f"model errors: {decision['error']}"
                )
                run.done = False
                run.result = (
                    f"The model could not be called: {decision['error']}"
                )
                run.reasoning = "Consecutive model errors; the run cannot make progress."
                await self._conclude(run)
                return False
        else:
            run.think_failures = 0

        calls = await self._prepare_round(run, decision)
        if calls is None:
            # A seam (e.g. MetaAgent) handled this turn. It may also have asked for an
            # immediate retry — the no-progress guard does — which is a loop iteration
            # here rather than a call back into this method.
            if run.retry_now:
                run.retry_now = False
                return True
            return False
        if not calls:
            # text-only turn: record the empty step, nudge, and try again next turn
            run.round_step = run.step
            run.step_plan = []
            await self._post_step(run.task_id, run.step, run.ctx, messages,
                                  reasoning=decision["reasoning"],
                                  assistant_text=decision.get("assistant_text", ""),
                                  provider_state=decision.get("provider_state", {}),
                                  plan=[], step_tokens=decision["step_tokens"],
                                  step_usage=decision.get("step_usage"), done=False)

            # A turn with neither reasoning nor a call is degenerate — a successful model
            # response that said nothing — not a model thinking out loud. The nudge below
            # assumes there was text to nudge; when there was not, repeating it every step
            # is how a run spends its whole budget on empty turns. Counted separately from
            # think_failures because the call did not fail, and stopped before the budget
            # is gone rather than after.
            if not (decision.get("reasoning") or "").strip():
                run.empty_turns += 1
                if run.empty_turns >= _EMPTY_TURNS_BEFORE_GIVING_UP:
                    logger.error(
                        f"| 🛑 [{self.name}] giving up after {run.empty_turns} consecutive "
                        f"empty turns: the model is returning neither reasoning nor a tool call")
                    run.done = False
                    run.result = (
                        f"The model returned {run.empty_turns} empty responses in a row "
                        f"(no reasoning, no tool call); the run cannot make progress.")
                    run.reasoning = "Consecutive empty model responses."
                    await self._conclude(run)
                    return False
            else:
                run.empty_turns = 0

            run.action_errors = [
                "You produced text but called no tool. Take the next action by calling a tool, "
                "or if the task is COMPLETE call `done_tool` with the result now."]
            run.step += 1
            return True
        decision["serialize_batch"] = await self._batch_requires_serial(
            calls, decision["routing"]
        )
        self._dispatch_round(run, calls, decision["routing"])
        return False

    def _dispatch_round(self, run: "_AgentRun", calls: List[Any], routing: Dict[str, Any]) -> None:
        """Launch this turn's batch as concurrent background tasks, each posting its
        result to this agent's own inbox. This turn's batch == one round."""
        import json as _json
        run.round_step = run.step
        run.outstanding = set()
        run.round_tasks = {}
        run.round_errors = []
        run.round_done = False
        run.round_result = None
        run.round_reasoning = None
        run.round_outcomes = []
        run.step_plan = [
            {"id": c.id, "description": "", "type": (routing.get(c.name) or ("tool",))[0],
             "name": c.name, "args": _json.dumps(c.input, ensure_ascii=False)} for c in calls]
        previous = None
        for i, call in enumerate(calls):
            run.outstanding.add(call.id)
            task = asyncio.create_task(
                self._run_one_bg_ordered(run, call, i, routing, previous),
                name=f"action-{call.id}",
            )
            run.round_tasks[call.id] = task
            if (run.decision or {}).get("serialize_batch"):
                previous = task

    async def _run_one_bg_ordered(
        self, run: "_AgentRun", call: Any, index: int,
        routing: Dict[str, Any], previous: Optional[asyncio.Task],
    ) -> None:
        if previous is not None:
            try:
                await previous
            except Exception:
                pass
        await self._run_one_bg(run, call, index, routing)

    async def _run_one_bg(self, run: "_AgentRun", call: Any, index: int, routing: Dict[str, Any]) -> None:
        """Run one action, then post an _ActionDone back to this agent's inbox."""
        try:
            outcome = await self._run_one(call, index, routing, run.task_id, run.round_step, run.ctx, parent_ref=run.ref)
        except asyncio.CancelledError:
            return
        except Exception as e:  # pragma: no cover - defensive
            outcome = {"name": call.name, "done": False, "result": None, "reasoning": None, "error": str(e)}
        try:
            await run.ref._inbox.put(_ActionDone(
                call_id=call.id, name=call.name, output=outcome.get("output"), result=outcome.get("result"),
                error=outcome.get("error"), is_done=outcome.get("done", False), reasoning=outcome.get("reasoning")))
        except Exception:
            pass

    async def _on_round_complete(self, run: "_AgentRun") -> None:
        """A round's whole batch has drained: record the step, then advance or conclude."""
        decision = run.decision
        await self._record_round_outcome(run)
        await self._post_step(run.task_id, run.round_step, run.ctx, run.messages,
                              reasoning=decision["reasoning"],
                              assistant_text=decision.get("assistant_text", ""),
                              provider_state=decision.get("provider_state", {}),
                              plan=getattr(run, "step_plan", []),
                              step_tokens=decision["step_tokens"], step_usage=decision.get("step_usage"),
                              done=run.round_done)
        run.action_errors = list(run.round_errors)
        run.step = run.round_step + 1
        if run.round_done:
            run.done = True
            run.result = run.round_result
            if run.round_reasoning:
                run.reasoning = run.round_reasoning
            await self._conclude(run)
            return
        if run.paused:
            return  # control channel: hold here until a resume advances us
        await self._advance(run)

    async def _conclude(self, run: "_AgentRun") -> None:
        """Finish a run: cancel stragglers, emit ON_STOP hooks, build the Response, run
        the post-run finalize hook, resolve the caller's reply, then on_end."""
        from agentevolver.hook.server import hook_manager
        from agentevolver.hook.types import HookEvent
        from agentevolver.response import ResponseType

        for t in list(run.round_tasks.values()):
            if not t.done():
                t.cancel()
        if run.round_tasks:
            await asyncio.gather(*run.round_tasks.values(), return_exceptions=True)
        run.round_tasks.clear()
        run.outstanding.clear()

        success = run.done and not run.stopped_by_constraint
        for hook_name in ("trace_hook", "trajectory_hook"):
            await hook_manager(
                name=hook_name,
                input={"event": HookEvent.ON_STOP, "result": run.result, "success": success, **self._lifecycle_input(run)},
                ctx=run.ctx,
            )
        self._release_session_resources(run)

        # "✅ completed" for a force-stop reads as success in the logs and hides the
        # very runs worth looking at — a no-progress termination and a finished task
        # looked identical while the former had written no source at all.
        if success:
            logger.info(f"| ✅ {self.name} completed after {run.step}/{self.max_step} steps")
        else:
            why = "constraint" if run.stopped_by_constraint else "stopped without finishing"
            logger.warning(
                f"| ❌ {self.name} ended after {run.step}/{self.max_step} steps ({why}): "
                f"{(run.result or '')[:200]}"
            )

        # `step`/`max_step` travel with the response so a caller can report what the
        # run actually cost without scraping the log — a benchmark harness needs it to
        # say whether it stayed inside the budget it claims to be running under.
        data = {"done": run.done, "result": run.result, "reasoning": run.reasoning,
                "stopped_by_constraint": run.stopped_by_constraint, "task_id": run.task_id,
                "step": run.step, "max_step": self.max_step}
        response = Response(type=ResponseType.AGENT, success=success, message=run.result or "", data=data)
        response = await self._finalize_run(response, run.ctx)

        ref = run.ref
        if ref is not None and ref._pending_reply is not None and not ref._pending_reply.done():
            ref._pending_reply.set_result(response)
            ref._pending_reply = None
        self._runs.pop(run.ref.name, None)
        await self.on_end(response, run.ctx, run)

    # ------------------------------------------------------------------
    # Seams — leaf actors use the defaults; orchestrators (MetaAgent) override
    # ------------------------------------------------------------------

    def _include_agents(self) -> bool:
        """Whether to project registered sub-agents into this agent's roster (agent__*).
        False for leaf actors; MetaAgent overrides to True."""
        return False

    def _include_workflows(self) -> bool:
        """Whether this Agent may invoke registered Workflows directly."""
        return False

    def _allow_read_only_tool_call(self, name: str, input: Dict[str, Any]) -> bool:
        """Narrow opt-in for non-mutating actions exposed by a mixed-action Tool."""
        return False

    def _target_capability_allowlists(self, target_name: Optional[str],
                                      target_type: Optional[str] = None) -> Dict[str, Any]:
        """Optional least-privilege allowlists derived from an evolution target.

        ``target_type`` says which kind the target is, so an implementation can name the
        right allowlist key. It was implicit while each type had its own agent class.
        """
        return {}

    def _extra_tools(self, run: "_AgentRun") -> Optional[List[Any]]:
        """Extra schema-only tools to append beyond the projected capabilities. Default:
        none (orchestration control like reply is an ordinary registered tool now)."""
        return None

    async def _prepare_round(self, run: "_AgentRun", decision: Dict[str, Any]) -> Optional[List[Any]]:
        """Advise on repetition, and stop a run that has stopped changing anything.

        Defined on the base ``Agent`` and called on the single round path every agent's
        loop flows through, so the guard applies to all agents uniformly; subclasses that
        override this (only ``MetaAgent``) must chain to ``super()`` to keep it.

        Repetition is handled by advice, not veto: a stateless hook
        (``repeat_tool_reminder_hook``) counts consecutive identical calls and returns a
        reminder, which rides to the model as context while the call proceeds. The model
        decides what to do about it.

        Blocking is reserved for the one shape advice has demonstrably failed to move: a
        run whose turns keep changing nothing. That backstop is below, and it is judged on
        the workspace rather than on any classification of the tools involved.
        """
        calls = decision["tool_calls"]
        if not calls:
            return calls

        from agentevolver.hook.server import hook_manager
        from agentevolver.hook.types import HookEvent

        routing = decision.get("routing") or {}
        actions = []
        for call in calls:
            route = routing.get(call.name) or ("tool", call.name)
            signature = self._action_signature(route[0], call.name, call.input or {})
            actions.append({
                "name": call.name,
                "type": route[0],
                "signature": signature,
            })
        fingerprint = await self._workspace_fingerprint(run.ctx)
        if run.baseline_fingerprint is None:
            run.baseline_fingerprint = fingerprint
        elif fingerprint != run.baseline_fingerprint:
            run.produced_change = True

        advice = await hook_manager(
            name="repeat_tool_reminder_hook",
            input={
                "event": HookEvent.PRE_ACTION,
                "agent_name": self.name,
                "task_id": run.task_id,
                "actions": actions,
                "repeat_chain": run.repeat_chain,
            },
            ctx=run.ctx,
        )
        if advice.repeat_chain is not None:
            run.repeat_chain = advice.repeat_chain
        if advice.additional_context:
            # Appended, not returned in place of the calls: the batch still runs. The
            # model reads this alongside the result it was about to fetch again.
            run.action_errors = [*(run.action_errors or []), advice.additional_context]

        self._announce_plan_mode(run)
        self._deliver_due_reminders(run)

        # The idle backstop. Not "this action repeats" — many *different* measurements
        # with nothing changed between them is the shape that actually consumed runs of
        # 65, 300 and 650 turns, and no repeat detector can see it because every command
        # differs. Judged on observable state, so a turn that changes something is never
        # caught here however much it repeats.
        idle_turns = getattr(run, "idle_turns", 0)
        if idle_turns < _IDLE_TURNS_BEFORE_BLOCKING or await self._turn_will_change(run, decision):
            run.no_progress_rounds = 0
            return calls

        run.no_progress_rounds += 1
        reason = (
            f"No-progress guard: {idle_turns} turns in a row have changed nothing and this "
            f"turn proposes only more inspection."
        )

        # Terminating is the right call for an agent circling work it has already
        # done, and the wrong one for an agent that has not produced anything yet:
        # the deliverable is then empty by construction, which is strictly worse
        # than spending a few more of its steps. Observed on ProgramBench — three
        # repeated recon reads ended a 200-step run at step 8 with no source file
        # written at all. So the strike budget below is widened until the run has
        # changed something at least once.
        scaled = int(self.max_step * _NO_PROGRESS_STRIKE_BUDGET_FRACTION)
        strikes_allowed = max(_NO_PROGRESS_STRIKES_MIN, min(scaled, _NO_PROGRESS_STRIKES_MAX))
        if not run.produced_change:
            strikes_allowed = max(strikes_allowed, _NO_PROGRESS_STRIKES_BEFORE_ANY_CHANGE)
        if run.no_progress_rounds >= strikes_allowed:
            run.done = False
            run.result = (
                f"Stopped after {run.no_progress_rounds} no-progress action proposals. "
                "Existing successful evidence is preserved in Memory, but the agent did "
                "not finish or choose a materially different action."
            )
            run.reasoning = reason
            await self._conclude(run)
            return None

        run.round_step = run.step
        await self._post_step(
            run.task_id, run.step, run.ctx, run.messages,
            reasoning=decision["reasoning"],
            assistant_text=decision.get("assistant_text", ""),
            provider_state=decision.get("provider_state", {}), plan=[],
            step_tokens=decision["step_tokens"], step_usage=decision.get("step_usage"), done=False,
        )
        blocked = (
            f"{reason} You have measured enough to act. Write the change you believe is "
            f"right — an edit, a file, a build — even if you are not certain; a wrong edit "
            f"is visible in one turn and reversible, while another measurement only tells "
            f"you what the last one did. If you genuinely cannot name a change to make, "
            f"this is not the thing to be working on: record it and move to the next item."
        )
        if run.no_progress_rounds >= strikes_allowed - 1:
            blocked += (
                " This is the last such turn that will be allowed: one more that changes "
                "nothing terminates this agent."
            )
        # Appended: a repetition reminder raised earlier in this same call is still
        # true, and the model should read both.
        run.action_errors = [*(run.action_errors or []), blocked]
        run.step += 1
        run.retry_now = True
        return None

    async def _fold_ahead(self, run) -> None:
        """Keep exact history bounded before cost or capacity becomes a problem."""
        if run.rooms_made >= _COMPACTIONS_PER_RUN:
            return
        metrics = self._context_history_metrics(run)
        reasons: list[str] = []
        logical_steps = metrics["logical_steps"]
        body_tokens = metrics.get("body_after_prefix_tokens", 0)
        ratio = metrics["pressure_ratio"]
        if self.compact_after_steps and logical_steps >= self.compact_after_steps:
            reasons.append(f"history={logical_steps} steps")
        body_limit = getattr(self, "compact_body_tokens", 0)
        if body_limit and body_tokens >= body_limit:
            reasons.append(f"body-after-prefix≈{body_tokens:,} tokens")
        if self.fold_at_pressure and ratio >= self.fold_at_pressure:
            reasons.append(f"capacity={ratio:.0%}")
        if not reasons:
            return
        logger.info(
            f"| 🗜️ [{self.name}] compacting before this step: {', '.join(reasons)}; "
            f"retaining {self.retain_recent_steps} complete steps"
        )
        await self._make_room(run)

    def _context_history_metrics(self, run) -> Dict[str, Any]:
        """Read early-compaction signals from the retained Trace."""
        from agentevolver.trace import trace_manager
        from agentevolver.trace.surface import fold_surface
        from agentevolver.trace.types import TraceEventType

        empty = {
            "logical_steps": 0,
            "body_after_prefix_tokens": 0,
            "pressure_ratio": 0.0,
        }
        try:
            events = trace_manager.events(str(getattr(run.ctx, "id", "") or "")) or []
            surface = set(fold_surface(events)["nodes"])
        except Exception as error:                                  # noqa: BLE001
            logger.debug(f"| [{self.name}] cannot read context history: {error}")
            return empty

        logical_steps = sum(
            event.seq_no in surface and event.event_type is TraceEventType.AGENT_CALL
            and (not event.agent_name or event.agent_name == self.name)
            for event in events
        )
        request = next((
            event for event in reversed(events)
            if event.event_type is TraceEventType.MODEL_REQUEST
            and (not event.agent_name or event.agent_name == self.name)
            and ((event.input or {}).get("parameters") or {}).get("operation")
            != "compact"
        ), None)
        pressure = ((request.input or {}).get("pressure") or {}) if request else {}
        try:
            pressure_ratio = float(pressure.get("pressure_ratio_after") or 0.0)
        except (TypeError, ValueError):
            pressure_ratio = 0.0

        # The active body is everything after the carried checkpoint.  Before the
        # first checkpoint the task is the stable anchor, so it is excluded as well.
        body_tokens = 0
        try:
            from agentevolver.message.types import CompactionMessage
            from agentevolver.model.pressure import estimate_tokens
            from agentevolver.trace.derive import derive_messages

            projected = derive_messages(events)
            boundary = max((
                index for index, message in enumerate(projected)
                if isinstance(message, CompactionMessage)
            ), default=0)
            body = projected[boundary + 1:]
            body_tokens = estimate_tokens(body) if body else 0
        except Exception as error:  # noqa: BLE001 - pressure fallback remains valid
            logger.debug(f"| [{self.name}] cannot measure compaction body: {error}")

        return {
            "logical_steps": logical_steps,
            "body_after_prefix_tokens": body_tokens,
            "pressure_ratio": pressure_ratio,
        }

    async def _last_pressure_ratio(self, run) -> float:
        """How full the previous request was, read from the log rather than remembered.

        ``0`` when there is no request yet, or when the log cannot be read — a step that
        cannot measure pressure must not fold on a guess.
        """
        from agentevolver.trace import trace_manager
        from agentevolver.trace.types import TraceEventType

        try:
            events = trace_manager.events(str(getattr(run.ctx, "id", "") or "")) or []
        except Exception as error:                                  # noqa: BLE001
            logger.debug(f"| [{self.name}] cannot read pressure from the log: {error}")
            return 0.0
        for event in reversed(events):
            if (
                event.event_type is TraceEventType.MODEL_REQUEST
                and ((event.input or {}).get("parameters") or {}).get("operation")
                != "compact"
            ):
                pressure = (event.input or {}).get("pressure") or {}
                try:
                    return float(pressure.get("pressure_ratio_after") or 0.0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    async def _make_room(self, run) -> bool:
        """Fold the oldest history so the next rebuild fits. Returns whether it worked.

        Answering an oversized request by compacting is the only recovery there is: the
        prompt is rebuilt from memory and trace every step, so a fold recorded now is
        shadowed out of the derived history before the next render. Nothing else the run
        can do makes the same request smaller.

        ``False`` means the run should stop trying — either there is no memory to fold,
        or there is nothing left in it that folding would remove. Then the ordinary error
        path reports the overflow, which is the honest outcome: a single turn whose
        instructions and reasoning alone exceed the window cannot be made to fit here.
        """
        if not (self.use_memory and self.memory_name):
            return False
        if run.rooms_made >= _COMPACTIONS_PER_RUN:
            logger.error(
                f"| 🛑 [{self.name}] context still does not fit after "
                f"{run.rooms_made} compaction(s); not folding further"
            )
            return False
        from agentevolver.memory import memory_manager
        try:
            folded = await memory_manager.compact(
                self.memory_name,
                str(run.ctx.id or ""),
                keep_steps=self.retain_recent_steps,
            )
        except Exception as error:                                  # noqa: BLE001
            logger.warning(f"| ⚠️ [{self.name}] could not compact to make room: {error}")
            return False
        if not folded:
            logger.warning(
                f"| 🗜️ [{self.name}] nothing left to fold; the request cannot be made to fit"
            )
            return False
        run.rooms_made += 1
        logger.info(
            f"| 🗜️ [{self.name}] folded history to fit the next request "
            f"(compaction {run.rooms_made}/{_COMPACTIONS_PER_RUN})"
        )
        return True

    @staticmethod
    def _action_signature(type: str, name: str, args: Dict[str, Any]) -> str:
        """Return a deterministic signature for one capability invocation."""
        payload = {"type": type, "name": name, "args": args}
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    async def _workspace_fingerprint(ctx: Optional["AgentContext"]) -> str:
        """Fingerprint observable workspace state without reading file contents.

        Paths, sizes, and nanosecond mtimes detect ordinary edits cheaply. Large cache
        trees are skipped and traversal is bounded to keep the guard lightweight.

        The guard blocks a repeated action only while this is unchanged, so a
        fingerprint that cannot move makes every repeat look unproductive: when it once
        scanned a directory the agent was not working in, a 200-step run ended at step 8
        before a single source file existed. It has to watch the directory the tools
        actually write to.
        """
        root_value = config.workspace_root
        if not root_value:
            return ""
        root = os.path.abspath(root_value)
        digest = hashlib.sha256()
        seen = 0
        skipped = {".git", "__pycache__", "node_modules", ".venv"}
        try:
            for current, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d not in skipped)
                # Directory mtimes catch creation/removal of empty directories, which
                # matters for list/inspection actions even when no file exists yet.
                for name in dirs:
                    directory = os.path.join(current, name)
                    try:
                        stat = os.stat(directory, follow_symlinks=False)
                    except OSError:
                        continue
                    relative = os.path.relpath(directory, root)
                    digest.update(f"{relative}/\0{stat.st_mtime_ns}\n".encode())
                for name in sorted(files):
                    path = os.path.join(current, name)
                    try:
                        stat = os.stat(path, follow_symlinks=False)
                    except OSError:
                        continue
                    relative = os.path.relpath(path, root)
                    digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
                    seen += 1
                    if seen >= 4096:
                        digest.update(b"<truncated>")
                        return digest.hexdigest()
        except OSError:
            return ""
        return digest.hexdigest()

    async def _turn_will_change(self, run: "_AgentRun", decision: Dict[str, Any]) -> bool:
        """Whether the *proposed* turn intends to change something.

        Judged before it runs, so a turn of pure inspection can be refused while an idle
        streak is open. A shell command is treated as intending to change something —
        it may well — so this only ever blocks a turn made entirely of tools that declare
        themselves read-only.
        """
        from agentevolver.tool import tool_manager

        calls = decision.get("tool_calls") or []
        if not calls:
            return True  # a text-only turn is handled elsewhere
        for call in calls:
            route = (decision.get("routing") or {}).get(call.name) or ("tool",)
            if str(route[0]) != "tool":
                return True
            try:
                info = await tool_manager.get_info(call.name)
            except Exception:  # noqa: BLE001
                return True
            if info is None or info.instance is None:
                return True
            predicted = info.instance.will_mutate(call.input or {})
            if predicted is not False:
                return True
        return False

    async def _batch_requires_serial(
        self, calls: List[Any], routing: Dict[str, Any],
    ) -> bool:
        """Parallelize only a batch whose every effect is explicitly read-only."""
        if len(calls) < 2:
            return False
        from agentevolver.tool import tool_manager
        from agentevolver.connector import connector_manager

        for call in calls:
            route = routing.get(call.name) or ("tool", call.name)
            if route[0] == "capability_search":
                continue
            try:
                if route[0] == "tool":
                    info = await tool_manager.get_info(route[1])
                    if info is None or info.instance is None:
                        return True
                    if info.instance.will_mutate(call.input or {}) is not False:
                        return True
                    continue
                if route[0] == "connector" and len(route) > 2:
                    info = await connector_manager.get_info(route[1])
                    annotations = (
                        (getattr(info, "action_annotations", None) or {}).get(route[2])
                        if info is not None else None
                    ) or {}
                    if annotations.get("readOnlyHint") is True:
                        continue
                return True
            except Exception as error:  # registry/effect uncertainty => serialize
                logger.warning(
                    f"| ⚠️ [{self.name}] could not classify {call.name} for "
                    f"parallel execution ({error}); serializing the batch"
                )
                return True
        return False

    async def _turn_changed_something(self, run: "_AgentRun", fingerprint: str) -> bool:
        """Whether this turn changed anything, by declaration or by effect.

        Two ways to count, because neither alone is enough. A tool can say it mutates —
        `write_file` does, `read_file` does not — but a shell command depends entirely on
        its arguments: `cat x` reports, `sed -i` changes. So a shell command is judged by
        whether observable state actually moved.
        """
        from agentevolver.tool import tool_manager

        for item in run.step_plan:
            if str(item.get("type") or "tool") != "tool":
                # A dispatched sub-agent or workflow does work of its own.
                return True
            try:
                info = await tool_manager.get_info(str(item.get("name") or ""))
            except Exception:  # noqa: BLE001 — accounting must never break a run
                info = None
            if info is not None and getattr(info, "mutates", None) is True:
                return True
        return bool(run.last_fingerprint is not None and fingerprint != run.last_fingerprint)

    async def _record_round_outcome(self, run: "_AgentRun") -> None:
        """Update the action mix and the idle streak from the drained round.

        The per-signature evidence this used to accumulate fed the classification guard
        that blocked repeats. Repetition is now advised on from the proposed batch, so
        nothing read the evidence — and it retained every successful tool output for the
        life of the run to answer a question no longer asked.
        """
        fingerprint = await self._workspace_fingerprint(run.ctx)

        # Action mix and the idle streak, updated once per drained round.
        changed = await self._turn_changed_something(run, fingerprint)
        if changed:
            run.mutations += 1
            run.idle_turns = 0
        else:
            run.observations += 1
            run.idle_turns += 1
        run.last_fingerprint = fingerprint

    async def _handle_extra_event(self, run: "_AgentRun", msg: Any) -> None:
        """Handle a non-action inbox message (escalation, progress). Leaf agents receive
        none; orchestrators override. Default: ignore."""
        return

    async def _finalize_run(self, response: "Response", ctx: Optional[AgentContext]) -> "Response":
        """Post-run hook, called by ``_conclude`` BEFORE the caller's reply is resolved so
        it can still adjust the Response. Default: passthrough. generate/optimize agents
        override to register the produced artifact (and fail the response on error)."""
        return response

    async def on_end(self,
                     result: "Response",
                     ctx: Optional[AgentContext],
                     run: Optional["_AgentRun"] = None,
                     ) -> None:
        """Third of the lifecycle triad (``on_start`` / ``on_event`` / ``on_end``):
        called once the run resolves — cleanup / teardown hook.

        The framework's ``_conclude`` calls this (with the finished ``run``) after it
        has resolved the caller's reply. ``run`` is ``None`` only on the synchronous
        ``handle`` path (an agent whose ``on_start`` returned a Response directly, e.g.
        BrowserAgent). Distinct from ``HookEvent.ON_STOP`` (a hook event fired around
        completion) — this is the overridable Python method.

        Default behaviour: no-op.  Override to emit extra teardown / trace / reset state.
        """

    # ------------------------------------------------------------------
    # Framework dispatcher — do NOT override in subclasses
    # ------------------------------------------------------------------

    async def handle(self, msg: Any, ref: Any) -> None:
        """Runtime pump dispatcher.

        Routes each inbox message to the appropriate lifecycle method:
          * TaskMessage          → on_start → [on_end if resolved synchronously]
          * Everything else      → on_event

        This method is part of the framework layer.  Subclasses should
        implement ``on_start``, ``on_event``, and ``on_end`` instead of
        overriding ``handle``.
        """
        from agentevolver.runtime.types import TaskMessage
        if isinstance(msg, TaskMessage):
            ctx = msg.kwargs.get("ctx")
            ref._pending_reply = msg.reply_future      # hand ownership to ref
            try:
                extra_kwargs = {k: v for k, v in msg.kwargs.items() if k not in ("ctx", "files")}
                result = await self.on_start(
                    task=msg.task or "",
                    files=msg.kwargs.get("files"),
                    ctx=ctx,
                    ref=ref,
                    **extra_kwargs,
                )
                if result is not None:
                    if ref._pending_reply is not None and not ref._pending_reply.done():
                        ref._pending_reply.set_result(result)
                        ref._pending_reply = None
                    await self.on_end(result, ctx)
            except asyncio.CancelledError:
                if ref._pending_reply is not None and not ref._pending_reply.done():
                    ref._pending_reply.cancel()
                raise
            except Exception as exc:
                logger.error(f"| ❌ {self.name} task failed: {exc}", exc_info=True)
                if ref._pending_reply is not None and not ref._pending_reply.done():
                    ref._pending_reply.set_exception(exc)
        else:
            await self.on_event(msg, ref)


class ProceduralAgent(Agent):
    """Deterministic Agent subtype driven by code instead of the LLM loop.

    Subclasses implement :meth:`run_procedure`. The inherited ``__call__`` remains
    the only public entry point, so direct calls and delegated runtime calls follow
    the same mailbox lifecycle.
    """

    agent_type: AgentType = Field(default=AgentType.PROCEDURAL)

    def __init__(self, *args: Any, use_memory: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, use_memory=use_memory, **kwargs)

    async def run_procedure(
        self,
        task: str,
        files: Optional[List[str]],
        ctx: AgentContext,
        **kwargs: Any,
    ) -> "Response":
        """Run this procedural agent's deterministic logic once and return its Response.

        The single extension point for ``ProceduralAgent`` subclasses: ``on_start`` wraps
        it in the standard lifecycle hooks and reply resolution, so subclasses implement
        only the procedure itself.

        Raises:
            NotImplementedError: If a subclass does not override this method.
        """
        raise NotImplementedError(f"{type(self).__name__}.run_procedure is not implemented")

    async def on_start(
        self,
        task: str,
        files: Optional[List[str]],
        ctx: Optional[AgentContext],
        ref: Any,
        **kwargs: Any,
    ) -> Optional["Response"]:
        """Execute the deterministic workflow once and resolve synchronously."""
        from agentevolver.hook.server import hook_manager
        from agentevolver.hook.types import HookEvent
        from agentevolver.response import ResponseType

        ctx = ctx or AgentContext()
        if not config.workspace_root:
            config.workspace_root = self.base_dir
        task_id = str(uuid.uuid4())
        lifecycle = {
            "task_id": task_id,
            "agent_name": self.name,
            "agent_type": self.agent_type.value,
            "memory_name": self.memory_name,
            "use_memory": self.use_memory,
            "parent_session_id": ctx.parent_session_id,
            "subtask_id": ctx.subtask_id,
        }
        for hook_name in ("trace_hook", "trajectory_hook"):
            await hook_manager(
                name=hook_name,
                input={"event": HookEvent.ON_START, "task": task, **lifecycle},
                ctx=ctx,
            )

        try:
            response = await self.run_procedure(task, files, ctx, **kwargs)
            if not isinstance(response, Response):
                response = Response(
                    type=ResponseType.AGENT,
                    success=True,
                    message=str(response),
                    data={"result": response},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"| ❌ [{self.name}] Workflow failed: {exc}", exc_info=True)
            response = Response(type=ResponseType.AGENT, success=False, message=str(exc))

        response = await self._finalize_run(response, ctx)
        for hook_name in ("trace_hook", "trajectory_hook"):
            await hook_manager(
                name=hook_name,
                input={
                    "event": HookEvent.ON_STOP,
                    "result": response.message,
                    "success": response.success,
                    **lifecycle,
                },
                ctx=ctx,
            )
        return response


__all__ = [
    "InputArgs",
    "AgentConfig",
    "AgentType",
    "Agent",
    "ProceduralAgent",
    "AgentContext",
]
