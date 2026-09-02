"""Dedicated website product engineer with MetaAgent orchestration mechanics."""

import json
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.registry import AGENT

_MANIFEST_MARKER = "## runtime-input-manifest"
_PRIVATE_ATTACHMENT_ROLES = {"user_context"}


def _bound_runtime_input_manifest(
    task: str, files: Optional[List[str]],
) -> Optional[tuple[str, str, Dict[str, Any]]]:
    """Parse and privately bind a role manifest to the staged attachment paths."""
    attachments = [str(path) for path in (files or [])]
    before, marker, after = str(task).partition(_MANIFEST_MARKER)
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
            raise ValueError(
                f"runtime-input-manifest attachment {index} requires id and role"
            )
        bound = dict(entry)
        bound.pop("source_path", None)
        bound["path"] = staged_path
        bound["staged"] = True
        rebound.append(bound)
    manifest["attachments"] = rebound
    manifest["paths_staged"] = True
    return before.rstrip(), after[:start].strip(), manifest


def _render_runtime_input_manifest(
    before: str, explanation: str, manifest: Dict[str, Any],
) -> str:
    return (
        f"{before}\n\n{_MANIFEST_MARKER}\n"
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
    bound = _bound_runtime_input_manifest(task, files)
    if bound is None:
        return str(task)
    before, explanation, manifest = bound
    visible = []
    for entry in manifest["attachments"]:
        bound = dict(entry)
        if bound.get("role") in _PRIVATE_ATTACHMENT_ROLES:
            bound.pop("path", None)
            bound.pop("staged", None)
            bound["routing"] = "runtime_private"
        visible.append(bound)
    manifest["attachments"] = visible
    return _render_runtime_input_manifest(before, explanation, manifest)


@AGENT.register_module(force=True)
class WebsiteBuilderAgent(MetaAgent):
    """Direct website builder with MetaAgent's reusable event lifecycle.

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
        base_dir: str,
        prompt_name: Optional[str] = None,
        max_step: int = 180,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            base_dir=base_dir,
            prompt_name=prompt_name or "website_builder_agent",
            max_step=max_step,
            **kwargs,
        )

    @staticmethod
    def _bind_runtime_environment(ctx: Any) -> None:
        """Mount only the coordinator environment on the Builder itself."""
        # BrowserEnvironment is registered globally because Website User children need
        # it. The Builder itself coordinates those children and only works in JobEnvironment;
        # mounting the browser here creates an unused page and pushes critical schemas into
        # deferred discovery. Keep the runtime boundary explicit before the first prompt.
        if ctx is not None and getattr(ctx, "extra", None) is not None:
            ctx.extra["environment_allowlist"] = ["job"]

    @staticmethod
    async def _subscriber(
        name: str,
        *,
        model: str,
        task: str,
        files: Optional[List[str]] = None,
        ctx: Any,
        ref: Any,
    ) -> str:
        """Start one private, continuable subscriber from a registered template."""
        from agentevolver.agent.server import agent_manager
        from agentevolver.agent.types import Agent
        from agentevolver.runtime import runtime_manager

        template = await agent_manager.get(name)
        if not isinstance(template, Agent):
            raise RuntimeError(f"subscriber Agent {name!r} is not registered")
        child = template.clone_for_run(model_name=model)
        response = await runtime_manager.delegate_background(
            child,
            task,
            continuable=True,
            subscription_topics=["deployment.ready"],
            files=files or None,
            parent_ref=ref,
            parent_ctx=ctx,
        )
        job_id = str((response.data or {}).get("job_id") or "")
        if not response.success or not job_id:
            raise RuntimeError(f"could not start {name!r} subscriber: {response.message}")
        return job_id

    @staticmethod
    def _document_text(paths: List[str], *, label: str) -> str:
        """Render routed documents for an Agent that intentionally has no file tool."""
        from agentevolver.task.context import load_task_document

        documents = []
        for path in paths:
            document = load_task_document(path)
            if not document.content.strip():
                raise ValueError(f"{label} attachment is empty: {path}")
            documents.append(document.content.strip())
        return "\n\n".join(documents)

    async def _bootstrap_subscribers(
        self, manifest: Dict[str, Any], ctx: Any, ref: Any,
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
            attachment_id = str(
                participant.get("user_context_attachment") or ""
            ).strip()
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
            private_context = self._document_text(
                [str(attachment["path"])], label=f"participant {participant_id}",
            )
            job_id = await self._subscriber(
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
                ref=ref,
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
        acceptance_requirements = self._document_text(
            requirement_paths, label="release acceptance requirements",
        )
        acceptance_job = await self._subscriber(
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
            ref=ref,
        )

        cycles = max(0, int(manifest.get("optimization_cycles") or 0))
        contract = {
            "topic": "deployment.ready",
            "required_releases": cycles + 1,
            "participants": jobs,
            "acceptance_job_id": acceptance_job,
            "subscriber_job_ids": [
                *(item["job_id"] for item in jobs), acceptance_job,
            ],
            # Mutated in place by JobEnvironment when the Builder reads a completed
            # subscriber turn. Keeping this inside the contract makes the feedback
            # handshake survive context conversion without a second registry.
            "collected_turns": {},
        }
        extra["website_runtime_contract"] = contract
        extra["deployment_release_history"] = []
        return contract

    @staticmethod
    def _public_manifest(
        before: str,
        explanation: str,
        manifest: Dict[str, Any],
        contract: Dict[str, Any],
    ) -> str:
        public = json.loads(json.dumps(manifest))
        for entry in public.get("attachments") or []:
            entry.pop("source_path", None)
            if entry.get("role") in _PRIVATE_ATTACHMENT_ROLES:
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
        }
        return _render_runtime_input_manifest(before, explanation, public)

    async def _completion_blocker(self, ctx: Any) -> Optional[str]:
        """Require every contracted release and subscriber turn before completion."""
        base = await super()._completion_blocker(ctx)
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
            str(item.get("source_revision") or "") for item in history
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

        from agentevolver.runtime import runtime_manager

        release_turn = len(history)
        pending = []
        failed = []
        for job_id in contract.get("subscriber_job_ids") or []:
            ref = runtime_manager.child(job_id)
            if (
                ref is None
                or not ref.alive
                or ref.busy
                or not ref._tasks.empty()
                or ref.turns < release_turn
            ):
                pending.append(str(job_id))
            elif ref.last_turn_success is not True:
                failed.append(str(job_id))
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
            acceptance_ref = runtime_manager.child(acceptance_id)
            acceptance_result = (
                acceptance_ref.turn_results.get(release_turn, "")
                if acceptance_ref is not None else ""
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
            str(job_id) for job_id in contract.get("subscriber_job_ids") or []
            if int(collected.get(str(job_id)) or 0) < len(history)
        ]
        if uncollected:
            return (
                f"release {len(history)} feedback has not been collected from: "
                + ", ".join(uncollected)
            )
        return None

    async def on_start(self, task, files, ctx, ref, **kwargs):
        """Privately route user inputs, then expose only public work to the Builder."""
        self._bind_runtime_environment(ctx)
        bound = _bound_runtime_input_manifest(task, files)
        if bound is None:
            return await super().on_start(task, files, ctx, ref, **kwargs)
        before, explanation, manifest = bound
        contract = await self._bootstrap_subscribers(manifest, ctx, ref)
        task = self._public_manifest(before, explanation, manifest, contract)
        public_files = [
            str(item["path"])
            for item in manifest["attachments"]
            if item.get("role") not in _PRIVATE_ATTACHMENT_ROLES and item.get("path")
        ]
        if getattr(ctx, "extra", None) is not None:
            ctx.extra["task_files"] = list(public_files)
        return await super().on_start(task, public_files, ctx, ref, **kwargs)


__all__ = ["WebsiteBuilderAgent", "bind_runtime_input_manifest"]
