"""WebsiteBuilderAgent — an orchestrator the runtime drives in subscription mode.

Same loop, same declaration shape, same base class as every other agent. What makes it
different is not its code but how the runtime drives it: it registers *resident* children
subscribed to ``deployment.ready``, so every release it publishes becomes one turn for a
panel of website users and an independent acceptance worker. Registering a subscriber is
an ordinary sub-agent dispatch that names ``subscription_topics`` — the mechanism is the
kernel's, not this file's.

Four overrides, each a policy this domain has and other agents do not: what counts as
evolution work worth recording, how long one product iteration may spend observing, when
the run may call itself finished, and which half of its own brief it may read.

The module-level half of the file is what must be settled *before* the agent starts, and
therefore cannot be a tool call it makes:

``bind_runtime_input_manifest``   what the task is, and which attachments stay private
``bootstrap_subscribers``         the rubric each release is judged by

An agent that could read the acceptance brief could write to the rubric instead of to the
product, and one that could build its own review panel could build one that passes it.
The ordering is the guarantee.
"""

import json
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.registry import AGENT

#: The three agents whose runs the contract records. Not a permission list: this agent
#: may call anything mounted, and these are simply the calls that leave a component
#: behind worth citing.
EVOLUTION_WORKERS = frozenset({"generate_agent", "optimize_agent", "evaluate_agent"})


# ---------------------------------------------------------------------------
# What the task is, and which half of it stays private
# ---------------------------------------------------------------------------

MANIFEST_MARKER = "## runtime-input-manifest"
PRIVATE_ATTACHMENT_ROLES = {"user_context"}


def bound_runtime_input_manifest(
    task: str,
    files: Optional[List[str]],
) -> Optional[tuple[str, str, Dict[str, Any]]]:
    """Parse and privately bind a role manifest to the staged attachment paths."""
    attachments = [str(path) for path in (files or [])]
    before, marker, after = str(task).partition(MANIFEST_MARKER)
    if not marker:
        return None
    start = after.find("{")
    if start < 0:
        raise ValueError("runtime-input-manifest has no JSON object")
    try:
        manifest = json.loads(after[start:])
    except (TypeError, ValueError) as error:
        raise ValueError(f"runtime-input-manifest is invalid JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise ValueError("runtime-input-manifest must be a JSON object")
    declared = manifest.get("attachments")
    if not isinstance(declared, list):
        raise ValueError("runtime-input-manifest must contain an attachments list")
    if len(declared) != len(attachments):
        raise ValueError(
            "runtime-input-manifest attachment count does not match staged files: "
            f"declared={len(declared)}, staged={len(attachments)}"
        )
    rebound = []
    for index, (entry, staged_path) in enumerate(zip(declared, attachments)):
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("role"):
            raise ValueError(f"runtime-input-manifest attachment {index} requires id and role")
        bound = dict(entry)
        bound.pop("source_path", None)
        bound["path"] = staged_path
        bound["staged"] = True
        rebound.append(bound)
    manifest["attachments"] = rebound
    manifest["paths_staged"] = True
    return before.rstrip(), after[:start].strip(), manifest


def render_runtime_input_manifest(
    before: str,
    explanation: str,
    manifest: Dict[str, Any],
) -> str:
    return (
        f"{before}\n\n{MANIFEST_MARKER}\n"
        f"{explanation}\n"
        f"{json.dumps(manifest, ensure_ascii=False, indent=2)}"
    )


def bind_runtime_input_manifest(task: str, files: Optional[List[str]]) -> str:
    """Return the Builder-visible manifest without private participant paths.

    Ordinary website tasks need no manifest. Launchers that require privacy-preserving
    role routing may provide an ``attachments`` list of any length; attachment staging
    happens after the launcher builds the task. User-context paths remain available to
    Runtime for subscriber creation but never enter the Builder's model message.
    """
    bound = bound_runtime_input_manifest(task, files)
    if bound is None:
        return str(task)
    before, explanation, manifest = bound
    visible = []
    for entry in manifest["attachments"]:
        bound = dict(entry)
        if bound.get("role") in PRIVATE_ATTACHMENT_ROLES:
            bound.pop("path", None)
            bound.pop("staged", None)
            bound["routing"] = "runtime_private"
        visible.append(bound)
    manifest["attachments"] = visible
    return render_runtime_input_manifest(before, explanation, manifest)


# ---------------------------------------------------------------------------
# What judges each release
# ---------------------------------------------------------------------------

def bind_runtime_environment(ctx: Any) -> None:
    """Mount only the coordinator environment on the Builder itagent."""
    # BrowserEnvironment is registered globally because Website User children need
    # it. The Builder itself coordinates those children and only works in JobEnvironment;
    # mounting the browser here creates an unused page and pushes critical schemas into
    # deferred discovery. Keep the runtime boundary explicit before the first prompt.
    if ctx is not None and getattr(ctx, "extra", None) is not None:
        ctx.extra["environment_allowlist"] = ["job"]

async def start_subscriber(
    name: str,
    *,
    model: str,
    task: str,
    files: Optional[List[str]] = None,
    ctx: Any,
    proc: Any,
) -> str:
    """Start one private subscriber from a registered template.

    A subscriber is a resident process with a topic edge, so ``task`` becomes its
    standing brief and it registers idle — no turn is spent waiting for a release
    that has not happened yet. Its pid is what identifies it afterwards.
    """
    from agentevolver.agent.server import agent_manager
    from agentevolver.agent.types import AgentContext
    from agentevolver.runtime import kernel
    from agentevolver.runtime.modes import InteractionMode

    template = await agent_manager.get(name)
    if template is None:
        raise RuntimeError(f"subscriber Agent {name!r} is not registered")
    # The registry holds the program; each subscriber is its own process.
    child = template.fresh()
    child.model_name = model
    # Its own session, exactly as a dispatched child gets one. Passing the parent's
    # context gave all four subscribers the builder's session id, and a session id is
    # what `BrowserEnvironment` keys a browser TAB on — so three participants and the
    # acceptance worker, woken by the same event, drove one page at once. Each read a
    # state another had just navigated away from, and reported "browser parked at
    # about:blank" and "no browser navigation capability was available", neither of
    # which was true: they had eleven browser actions each.
    #
    # The topic scope is read from the parent, so scoped events still reach them.
    inherited = dict(getattr(ctx, "extra", None) or {})
    child_ctx = AgentContext(
        name=name,
        parent_session_id=str(getattr(ctx, "id", "") or ""),
        extra={
            # A subscriber is a browser user; the builder's job-only scope is not its.
            key: value for key, value in inherited.items()
            if key in ("root_session_id", "source_workspace", "trace_integrity_profile")
        },
    )
    child_ctx.extra.setdefault(
        "root_session_id", str(getattr(ctx, "id", "") or "")
    )
    subscriber = await kernel.spawn(
        child,
        task,
        mode=InteractionMode.SUBSCRIBER,
        files=list(files or ()),
        ctx=child_ctx,
        parent=proc,
        topics=["deployment.ready"],
    )
    return subscriber.pid

def document_text(paths: List[str], *, label: str) -> str:
    """Render routed documents for an Agent that intentionally has no file tool."""
    from agentevolver.task.context import load_task_document

    documents = []
    for path in paths:
        document = load_task_document(path)
        if not document.content.strip():
            raise ValueError(f"{label} attachment is empty: {path}")
        documents.append(document.content.strip())
    return "\n\n".join(documents)

async def bootstrap_subscribers(
    agent: Any,
    manifest: Dict[str, Any],
    ctx: Any,
    proc: Any,
) -> Dict[str, Any]:
    """Create the private user panel and independent release acceptance worker."""
    extra = getattr(ctx, "extra", None)
    if extra is None:
        raise RuntimeError("website subscriptions require an Agent context")
    existing = extra.get("website_runtime_contract")
    if isinstance(existing, dict):
        return existing

    attachments = {
        str(item.get("id")): item
        for item in manifest.get("attachments") or []
        if isinstance(item, dict) and item.get("id")
    }
    participants = manifest.get("participants") or []
    if not isinstance(participants, list):
        raise ValueError("runtime-input-manifest participants must be a list")

    jobs = []
    for participant in participants:
        participant_id = str(participant.get("id") or "").strip()
        attachment_id = str(participant.get("user_context_attachment") or "").strip()
        attachment = attachments.get(attachment_id)
        if (
            not participant_id
            or attachment is None
            or attachment.get("role") != "user_context"
            or not attachment.get("path")
        ):
            raise ValueError(
                f"participant {participant_id or '(missing id)'} has no private "
                "user_context attachment"
            )
        private_context = document_text(
            [str(attachment["path"])],
            label=f"participant {participant_id}",
        )
        job_id = await start_subscriber(
            "website_user_agent",
            model=str(participant.get("model") or ""),
            task=(
                f"You are {participant_id}, a continuable website co-design participant. "
                "For every "
                "deployment.ready event, open only its exact URL, pursue your own "
                "personal goals through the visible UI, and return concise grounded "
                "needs, preferences, successful outcomes, and blockers.\n\n"
                "--- private user context (assigned only to you) ---\n"
                f"{private_context}"
            ),
            ctx=ctx,
            proc=proc,
        )
        jobs.append({"participant_id": participant_id, "job_id": job_id})

    acceptance = dict(manifest.get("release_acceptance") or {})
    requirement_paths = [
        str(item["path"])
        for item in attachments.values()
        if item.get("role") == "requirements" and item.get("path")
    ]
    if not requirement_paths:
        raise ValueError("release acceptance requires at least one requirements attachment")
    acceptance_requirements = document_text(
        requirement_paths,
        label="release acceptance requirements",
    )
    acceptance_job = await start_subscriber(
        str(acceptance.get("agent") or "browser_agent"),
        model=str(acceptance.get("model") or ""),
        task=(
            "Act as the independent release acceptance browser. For every "
            "deployment.ready event, test only the exact deployed URL against the "
            "release requirements below and event acceptance scope. Verify visible user "
            "journeys and native browser diagnostics. The first non-empty line of your "
            "final result must be exactly `VERDICT: PASS` only when every required journey "
            "passes, otherwise `VERDICT: FAIL`; follow it with grounded evidence.\n\n"
            "--- release requirements ---\n"
            f"{acceptance_requirements}"
        ),
        ctx=ctx,
        proc=proc,
    )

    cycles = max(0, int(manifest.get("optimization_cycles") or 0))
    contract = {
        "topic": "deployment.ready",
        "required_releases": cycles + 1,
        "participants": jobs,
        "acceptance_job_id": acceptance_job,
        "subscriber_job_ids": [
            *(item["job_id"] for item in jobs),
            acceptance_job,
        ],
        # Mutated in place by JobEnvironment when the Builder reads a completed
        # subscriber turn. Keeping this inside the contract makes the feedback
        # handshake survive context conversion without a second registry.
        "collected_turns": {},
        "evolution_runs": [],
        "evolution_decisions": [],
        "initial_step_budget": max(
            8,
            int(manifest.get("initial_step_budget") or agent.initial_step_budget),
        ),
        "iteration_step_budget": max(
            8,
            int(manifest.get("iteration_step_budget") or agent.iteration_step_budget),
        ),
    }
    extra["website_runtime_contract"] = contract
    extra["deployment_release_history"] = []
    return contract

def public_manifest(
    before: str,
    explanation: str,
    manifest: Dict[str, Any],
    contract: Dict[str, Any],
) -> str:
    public = json.loads(json.dumps(manifest))
    for entry in public.get("attachments") or []:
        entry.pop("source_path", None)
        if entry.get("role") in PRIVATE_ATTACHMENT_ROLES:
            entry.pop("path", None)
            entry.pop("staged", None)
            entry["routing"] = "runtime_private"
    public["participants"] = list(contract["participants"])
    acceptance = dict(public.get("release_acceptance") or {})
    acceptance.pop("model", None)
    acceptance["job_id"] = contract["acceptance_job_id"]
    acceptance["automatic_subscription"] = True
    public["release_acceptance"] = acceptance
    public["runtime_subscription"] = {
        "topic": contract["topic"],
        "automatic_deploy_publish": True,
        "required_releases": contract["required_releases"],
        "subscriber_job_ids": contract["subscriber_job_ids"],
        "collection": (
            "After each successful deploy, call job__wait for these ids with "
            "condition=idle_after_turn and min_turns equal to that release number; "
            "then call job__output with turn equal to that release number before "
            "choosing the next change."
        ),
        "initial_step_budget": contract["initial_step_budget"],
        "iteration_step_budget": contract["iteration_step_budget"],
    }
    return render_runtime_input_manifest(before, explanation, public)


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

@AGENT.register_module(force=True)
class WebsiteBuilderAgent(MetaAgent):
    """An orchestrator whose releases are consumed by resident subscribers.

    This is a distinct registered Agent rather than a renamed MetaAgent configuration:
    it has its own runtime identity, version history, prompt default, permissions, memory,
    traces, and evolution target. Product engineering stays in this agent; delegation is
    reserved for bounded specialist work, user research, and framework evolution.
    """

    name: str = Field(default="website_builder_agent")
    description: str = Field(
        default=(
            "An evolvable website product engineer that designs, implements, tests, "
            "deploys, and improves web products from task-defined requirements."
        )
    )
    metadata: Dict[str, Any] = Field(
        default_factory=lambda: {
            "role": "website_builder",
            "orchestrator": True,
            "product_engineering": True,
        }
    )
    enable_evolving: bool = Field(default=True)

    def __init__(
        self,
        base_dir: str = "",
        prompt_name: Optional[str] = None,
        max_step: int = 180,
        initial_step_budget: int = 36,
        iteration_step_budget: int = 30,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            base_dir=base_dir,
            prompt_name=prompt_name or "website_builder_agent",
            max_step=max_step,
            **kwargs,
        )
        self.initial_step_budget = max(8, int(initial_step_budget))
        self.iteration_step_budget = max(8, int(iteration_step_budget))
        self._release_count: Optional[int] = None
        self._iteration_start = 0
        # The orchestrator's guards, plus this agent's own iteration budget.
        self.middleware = [
            *self.middleware,
            lambda agent, step: agent.iteration_budget(step),
        ]

    async def act(self, decision):
        """Run the batch, then note any evolution work in the task contract.

        Records, never restricts. Every action in the batch has already run by the time
        this method has a result to look at, and this agent is deliberately unrestricted:
        its capability allowlist is empty — which means *everything* mounted, not nothing
        — it may dispatch any registered agent, and ``enable_evolving`` is on, so it can
        generate and optimize its own capabilities. Nothing here gates that.

        What it does is bookkeeping the contract needs and the model cannot be trusted
        for: which component was actually registered, and at what version. The agent's
        own account of a generate run is a claim; the extension manifest is the fact.
        """
        results = await super().act(decision)
        contract = (getattr(self.ctx, "extra", None) or {}).get(
            "website_runtime_contract"
        )
        if not isinstance(contract, dict):
            return results
        for result in results:
            entry = self._evolution_entry(result)
            if entry is not None:
                contract.setdefault("evolution_runs", []).append(entry)
        return results

    def _evolution_entry(self, result) -> Optional[Dict[str, Any]]:
        """One contract row for an evolution-worker result, or None for anything else.

        ``None`` is the ordinary case — most actions are not evolution — and says only
        "nothing to record about this one".
        """
        if result.call.name not in EVOLUTION_WORKERS:
            return None
        from agentevolver.extension import extension_manager

        target = dict(result.call.args or {})
        module = str(target.get("target_type") or "")
        name = str(target.get("target_name") or "")
        version = ""
        if module and name:
            component = extension_manager.read_manifest().find(module, name)
            if component is not None:
                version = component.version
        history = (getattr(self.ctx, "extra", None) or {}).get(
            "deployment_release_history"
        ) or []
        return {
            "agent": result.call.name,
            "module": module,
            "name": name,
            "version": version,
            "release_number": len(history),
            # A run that registered nothing did not evolve anything, whatever it reported.
            "success": result.ok and bool(version),
        }

    async def iteration_budget(self, step: int) -> str:
        """Keep each product iteration from spending itself on observation.

        Deliberately not a Workflow: the Builder stays free to choose its own
        implementation. And deliberately advice rather than a veto — the previous version
        discarded the proposed batch, which threw away a turn the model had already paid
        for; saying what to do instead redirects it at no cost, past the cache breakpoint.
        """
        extra = getattr(self.ctx, "extra", None) or {}
        contract = extra.get("website_runtime_contract")
        if not isinstance(contract, dict):
            return ""
        releases = len(extra.get("deployment_release_history") or [])
        if self._release_count != releases:
            self._release_count = releases
            self._iteration_start = step
        budget = int(contract.get(
            "initial_step_budget" if releases == 0 else "iteration_step_budget",
            self.initial_step_budget if releases == 0 else self.iteration_step_budget,
        ))
        if step - self._iteration_start < budget:
            return ""
        return (
            f"<iteration-budget>\nIteration budget reached ({budget} model turns since "
            f"release {releases}). Stop exploratory checks. Use deploy_tool preview on "
            "the current workspace, make at most one bounded root-cause patch if that "
            "evidence fails, then publish; or finish any capability evolution you "
            "already chose to start and report an honest blocker.\n</iteration-budget>"
        )

    async def completion_blocker(self, ctx: Any) -> Optional[str]:
        """Require every contracted release and subscriber turn before completion."""
        base = await super().completion_blocker(ctx)
        if base:
            return base
        extra = getattr(ctx, "extra", None) or {}
        contract = extra.get("website_runtime_contract")
        if not isinstance(contract, dict):
            return None
        history = list(extra.get("deployment_release_history") or [])
        required = int(contract.get("required_releases") or 0)
        if len(history) < required:
            return f"the task requires {required} releases; only {len(history)} succeeded"
        revisions = {
            str(item.get("source_revision") or "")
            for item in history
            if item.get("source_revision")
        }
        if len(revisions) < required:
            return (
                f"the task requires {required} materially distinct releases; only "
                f"{len(revisions)} unique source revisions were deployed"
            )
        expected_fanout = len(contract.get("subscriber_job_ids") or [])
        incomplete_fanout = [
            item for item in history if int(item.get("fanout") or 0) != expected_fanout
        ]
        if incomplete_fanout:
            return (
                f"{len(incomplete_fanout)} release event(s) did not reach all "
                f"{expected_fanout} subscribers"
            )

        # The same acceptance table the deploy gate reads, for the same reason: a
        # subscriber's turn number is not the release it was about, and binding them
        # made a first rejection permanent in BOTH gates at once.
        from agentevolver.runtime import kernel
        from agentevolver.tool.default.deployment.deploy import DeployTool

        release_turn = len(history)
        pending, failed = [], []
        for pid in contract.get("subscriber_job_ids") or []:
            state = DeployTool._acceptance_state(contract, release_turn, str(pid))
            if state in ("accepted", "absent"):
                continue
            (failed if state == "failed" else pending).append(str(pid))
        if pending:
            return (
                f"release {release_turn} still awaits subscriber turn completion: "
                f"{', '.join(pending)}"
            )
        if failed:
            return (
                "the latest co-design/acceptance turn did not finish successfully for: "
                + ", ".join(failed)
            )
        acceptance_id = str(contract.get("acceptance_job_id") or "")
        if acceptance_id:
            acceptance = kernel.get(acceptance_id)
            acceptance_result = (
                acceptance.turn_results.get(release_turn, "")
                if acceptance is not None
                else ""
            )
            first_line = next(
                (line.strip().upper() for line in acceptance_result.splitlines() if line.strip()),
                "",
            )
            if first_line != "VERDICT: PASS":
                return (
                    f"latest independent acceptance did not pass for release {release_turn}; "
                    f"received {first_line or '(no verdict)'}"
                )
        collected = dict(contract.get("collected_turns") or {})
        uncollected = [
            str(job_id)
            for job_id in contract.get("subscriber_job_ids") or []
            if int(collected.get(str(job_id)) or 0) < len(history)
        ]
        if uncollected:
            return f"release {len(history)} feedback has not been collected from: " + ", ".join(
                uncollected
            )
        # Runtime never decides whether this task should evolve. It only prevents an
        # evolution the Builder already started from being left as an unvalidated,
        # implicitly-active extension.
        runs = list(contract.get("evolution_runs") or [])
        decisions = list(contract.get("evolution_decisions") or [])
        changed = {
            (
                str(item.get("module") or ""),
                str(item.get("name") or ""),
                str(item.get("version") or ""),
            )
            for item in runs
            if item.get("success")
            and item.get("agent") in {"generate_agent", "optimize_agent"}
            and item.get("module")
            and item.get("name")
            and item.get("version")
        }
        for module, name, version in sorted(changed):
            evaluated = any(
                item.get("success")
                and item.get("agent") == "evaluate_agent"
                and item.get("module") == module
                and item.get("name") == name
                and item.get("version") == version
                for item in runs
            )
            decided = any(
                item.get("module") == module
                and item.get("name") == name
                and item.get("version") == version
                and item.get("decision") in {"keep", "rollback", "unload"}
                for item in decisions
            )
            if not evaluated or not decided:
                missing = (
                    "evaluation and decision"
                    if not evaluated and not decided
                    else ("evaluation" if not evaluated else "keep/rollback/unload decision")
                )
                return (
                    f"self-initiated evolution {module}:{name} v{version} is incomplete; "
                    f"missing {missing}"
                )
        return None

    async def prepare_task(self, task, files, ctx):
        """Privately route user inputs, then expose only public work to the Builder.

        The private half — the user personas, the acceptance brief — goes to subscribers
        that the Builder cannot read. What it gets is the public manifest, so it cannot
        write to a rubric it is being graded against.
        """
        bind_runtime_environment(ctx)
        bound = bound_runtime_input_manifest(task, files)
        if bound is None:
            return await super().prepare_task(task, files, ctx)
        before, explanation, manifest = bound
        contract = await bootstrap_subscribers(self, manifest, ctx, self.proc)
        task = public_manifest(before, explanation, manifest, contract)
        public_files = [
            str(item["path"])
            for item in manifest["attachments"]
            if item.get("role") not in PRIVATE_ATTACHMENT_ROLES and item.get("path")
        ]
        if getattr(ctx, "extra", None) is not None:
            ctx.extra["task_files"] = list(public_files)
        return task, public_files


__all__ = [
    "EVOLUTION_WORKERS",
    "PRIVATE_ATTACHMENT_ROLES",
    "WebsiteBuilderAgent",
    "bind_runtime_input_manifest",
    "bound_runtime_input_manifest",
]
