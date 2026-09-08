"""The loop's only view of the capability system.

Everything the loop needs to know about capabilities is two questions: what schemas go
in this request, and what happens when the model names one. Both are answered here, so
the agent never imports a manager and the loop can be exercised against a stub.

Routing is by the table each schema arrived with — ``name -> (type, entity, action?)``.
Tools go to the tool pipeline, which owns permission, approval and durability; other
capability kinds go to their own manager, which is callable by the same convention; a
sub-agent becomes a child process, because that is what dispatching one means.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agentevolver.agent.loop.decision import ActionCall, ActionResult
from agentevolver.logger import logger
from agentevolver.runtime.kernel import GRANTED_ALLOWLISTS, grant
from agentevolver.runtime.modes import InteractionMode

#: Capability types whose manager is callable as ``manager(name=, input=, ctx=)``.
_CALLABLE_MANAGERS = ("skill", "connector", "workflow", "plugin", "environment")

def _evolved_names(capability_type: str, *, exclude: Sequence[str] = ()) -> List[str]:
    """Registered extension components of one type, minus what is already listed.

    Read from the extension manifest, which is the record of what actually registered —
    an agent's account of a generate run is a claim, the manifest is the fact. Never
    raises: an unreadable manifest means an agent keeps the roster it declared, which is
    the safe direction for a list that bounds what a process may do.
    """
    try:
        from agentevolver.extension import extension_manager

        seen = set(exclude)
        return [
            component.name
            for component in extension_manager.read_manifest().components
            if component.module == capability_type and component.name not in seen
        ]
    except Exception as error:  # noqa: BLE001 - scope must not fail on a bad read
        logger.warning(f"| ⚠️ could not read evolved {capability_type}s: {error}")
        return []


#: The tool that ends a run. Recognised by name, as before: its Response carries the
#: result in ``data`` but no "I am finished" flag, so a data-key check silently never
#: fires and the loop runs on until the model stops calling tools by itself.
DONE_TOOL = "done_tool"

#: Framework-mutating tools a read_only agent may not call, whatever its roster says.
READ_ONLY_DENIED = frozenset({
    "write_file_tool", "edit_file_tool", "apply_patch_tool", "git_tool", "deploy_tool",
    "adoption_tool", "send_message_tool", "publish_event_tool",
})


class ToolRouter:
    """Base contract. Subclass to run the loop against something other than the registry."""

    async def schemas(self, agent: Any, ctx: Any) -> Tuple[List[Any], Dict[str, Any]]:
        """The tool list for this request, and the routing table for its names."""
        raise NotImplementedError

    async def invoke(
        self,
        call: ActionCall,
        *,
        agent: Any,
        ctx: Any,
        routing: Dict[str, Any],
        execution: Optional[Dict[str, Any]] = None,
        bridge: Any = None,
    ) -> ActionResult:
        """Run one action and return its result. Must not raise for an action failure.

        ``bridge`` is set only for the program transport, and is how a program's own tool
        calls are routed back through the executor rather than dispatched privately.
        """
        raise NotImplementedError

    def read_only(self, call: ActionCall, routing: Dict[str, Any]) -> Optional[bool]:
        """Whether this action is known to have no effects.

        ``None`` means unknown, and unknown is treated as effectful. Guessing the other
        way is how a batch reorders externally visible side effects.
        """
        return None

    def denial(self, call: ActionCall, routing: Dict[str, Any], agent: Any) -> str:
        """Why this action must not run, or "" to let it through.

        Part of the contract rather than one implementation's extra, because the executor
        asks every router for it before every action. A router with no policy of its own
        allows everything and says so here.
        """
        return ""


class CapabilityRouter(ToolRouter):
    """The real router: registered capabilities, dispatched to their owning manager."""

    def __init__(self, *, include_agents: bool = False, kernel: Any = None) -> None:
        #: Project registered sub-agents into the roster. This one flag is what makes an
        #: agent an orchestrator; nothing else about the loop changes.
        self.include_agents = include_agents
        self._kernel = kernel

    # -- schemas -------------------------------------------------------------

    async def schemas(self, agent: Any, ctx: Any) -> Tuple[List[Any], Dict[str, Any]]:
        from agentevolver.agent.context.capabilities import assemble_native_tools

        # The agent's declared scope, put where the capability managers look for it.
        #
        # A class field is a DEFAULT; a grant recorded in the context WINS. The two used
        # to be told apart by `setdefault`, which freezes on first use: the default was
        # copied into the context on step 0 and then occupied the key, so a grant made
        # later could not get in. Defaults are therefore refreshed every step and only
        # for keys nothing has granted, which is what lets an allowlist change during a
        # run — the point of granting a newly evolved component to an agent that needs
        # it, rather than only to whoever happened to be dispatched after it registered.
        extra = getattr(ctx, "extra", None)
        declared = getattr(agent, "capability_allowlists", None) or {}
        if isinstance(extra, dict):
            granted = set(extra.get(GRANTED_ALLOWLISTS) or ())
            accepts = {str(item) for item in (getattr(agent, "accepts_evolved", None) or ())}
            for capability_type, names in declared.items():
                key = (capability_type if capability_type.endswith("_allowlist")
                       else f"{capability_type}_allowlist")
                if key in granted:
                    continue
                scope = list(names)
                if capability_type in accepts:
                    scope += _evolved_names(capability_type, exclude=scope)
                extra[key] = scope

        tools, routing = await assemble_native_tools(
            agent, ctx, include_agents=self.include_agents
        )
        return list(tools), dict(routing)

    # -- effects -------------------------------------------------------------

    def read_only(self, call: ActionCall, routing: Dict[str, Any]) -> Optional[bool]:
        route = routing.get(call.name)
        if route and route[0] == "capability_search":
            return True
        if not route or route[0] != "tool":
            return None
        try:
            from agentevolver.tool import tool_manager

            info = tool_manager.peek(route[1])
            instance = getattr(info, "instance", None)
            mutates = instance.will_mutate(call.args) if instance is not None else None
        except Exception:  # noqa: BLE001 - unknown stays unknown
            return None
        return True if mutates is False else False if mutates is True else None

    # -- dispatch ------------------------------------------------------------

    async def invoke(
        self,
        call: ActionCall,
        *,
        agent: Any,
        ctx: Any,
        routing: Dict[str, Any],
        execution: Optional[Dict[str, Any]] = None,
        bridge: Any = None,
    ) -> ActionResult:
        route = routing.get(call.name)
        if route is None:
            return ActionResult(
                call=call,
                error=f"Unknown capability {call.name!r}; it is not in this run's roster.",
            )
        capability_type = route[0]
        try:
            if capability_type == "capability_search":
                from agentevolver.agent.context.capabilities import catalog, search

                # Refresh scope before searching: permissions may have narrowed
                # since the request exposing this callable was built.
                await self.schemas(agent, ctx)
                return ActionResult(call=call, output=search(
                    catalog(ctx, agent.name), ctx=ctx, agent_name=agent.name,
                    query=call.args.get("query", ""), limit=call.args.get("limit", 6),
                ))
            if capability_type == "tool":
                return await self._invoke_tool(call, route, ctx, execution, bridge)
            if capability_type == "agent":
                return await self._invoke_agent(call, route, agent, ctx)
            if capability_type in _CALLABLE_MANAGERS:
                return await self._invoke_manager(call, route, ctx)
            return ActionResult(
                call=call,
                error=f"Unsupported capability type {capability_type!r}",
            )
        except Exception as error:  # noqa: BLE001 - an action fault is an observation
            logger.warning(f"| ⚠️ action {call.name} raised: {error}")
            return ActionResult(call=call, error=f"{type(error).__name__}: {error}")

    async def _invoke_tool(
        self,
        call: ActionCall,
        route: Sequence[Any],
        ctx: Any,
        execution: Optional[Dict[str, Any]],
        bridge: Any = None,
    ) -> ActionResult:
        from agentevolver.tool import tool_manager

        # `sub_dispatch` is `batch_call_tool`'s only way to reach another tool, and it is
        # bound to this turn. A program's `await tools.x(...)` therefore goes back through
        # the executor — the same gates, the same hooks — instead of down a private line
        # to the tool manager, which would be a second copy of every check.
        passthrough = {"sub_dispatch": bridge} if bridge is not None else {}
        response = await tool_manager(
            name=route[1], input=call.args, ctx=ctx, execution_context=execution or {},
            **passthrough,
        )
        result = self._from_response(call, response)
        if route[1] == DONE_TOOL and result.ok:
            data = result.extra or {}
            return replace(
                result, final=True, output=str(data.get("result") or result.output)
            )
        return result

    async def _invoke_manager(
        self, call: ActionCall, route: Sequence[Any], ctx: Any
    ) -> ActionResult:
        from agentevolver.capability import MOUNTED_TYPES
        from agentevolver.permission import permission_manager

        if permission_manager.restrict() == "read_only":
            from agentevolver.plan.server import declaration_of

            declaration = await declaration_of(route[0], route[1])
            if not declaration or not (
                declaration.get("mutates") is False
                or declaration.get("permission_mode") == "read_only"
            ):
                return ActionResult(call=call, error=f"{route[1]} is not verified read-only")

        entry = next((item for item in MOUNTED_TYPES if item.type == route[0]), None)
        if entry is None:
            return ActionResult(call=call, error=f"No manager for {route[0]!r}")
        manager = entry.manager()
        # `action` is a parameter of the manager, not one of the action's own arguments.
        # Putting it inside `input` left `environment_manager.__call__` — where it is
        # required and positional — raising `missing 1 required positional argument:
        # 'action'` on every call. Every environment action was therefore dead:
        # `job__output`, `job__wait`, `job__kill`, and every browser action. A container
        # capability whose route carries no action addresses it by name alone, which is
        # what `skill` and `workflow` do, and neither accepts the keyword.
        extra = {"action": route[2]} if len(route) > 2 and route[2] else {}
        response = await manager(
            name=route[1], input=dict(call.args), ctx=ctx, **extra
        )
        return self._from_response(call, response)

    async def _invoke_agent(
        self, call: ActionCall, route: Sequence[Any], parent: Any, ctx: Any
    ) -> ActionResult:
        """Dispatch a sub-agent: ask the kernel for a child process, and collect it.

        What is left here once dispatching itself is a runtime primitive: validate what
        the model asked for, and turn a process into something the model can read.
        Blocking or not is the caller's choice at the call site, exactly as it is with a
        shell command. ``background: true`` returns the pid immediately and the child's
        final report arrives in the parent's mailbox when it exits.
        """
        from agentevolver.agent.server import validate_dispatch_input
        from agentevolver.runtime.modes import for_brief, topics_of

        kernel = self._kernel or self._default_kernel()
        try:
            brief = validate_dispatch_input(call.args)
        except ValueError as error:
            # The bounded handoff contract. Refused as a result, not raised, so the model
            # reads which limit it crossed and can write the specification to a file.
            return ActionResult(call=call, error=str(error))
        try:
            proc = await kernel.dispatch(route[1], brief, parent=parent, ctx=ctx)
        except (LookupError, ValueError) as error:
            return ActionResult(call=call, error=str(error))

        topics = topics_of(brief)
        background = bool(brief.get("background") or brief.get("run_in_background")) \
            or for_brief(brief) is not InteractionMode.RESPONDER
        if background:
            if topics:
                return ActionResult(
                    call=call,
                    output=(
                        f"Registered {route[1]} as {proc.pid}, idle and subscribed to "
                        f"{', '.join(topics)}. Its standing brief runs once an event "
                        "arrives; no turn is spent waiting."
                    ),
                    extra={"pid": proc.pid, "topics": topics},
                )
            return ActionResult(
                call=call,
                output=(
                    f"Started {route[1]} in the background as {proc.pid}. Its report "
                    f"will arrive when it finishes."
                ),
                extra={"pid": proc.pid},
            )
        result = await kernel.wait(proc)
        body = (getattr(result, "message", None) or (str(result) if result else "")
                or getattr(proc, "error", ""))
        status = proc.exit_status.value if proc.exit_status else "unknown"
        artifacts = dict(getattr(proc, "artifacts", {}) or {})
        if artifacts:
            body += "\n" + "\n".join(f"{key}: {value}" for key, value in artifacts.items())
        return ActionResult(
            call=call,
            output=f"[{route[1]} → {status}]\n{body}",
            error="" if status == "done" else f"{route[1]} ended {status}: {body}",
            extra={"pid": proc.pid, "artifacts": artifacts},
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _default_kernel() -> Any:
        from agentevolver.runtime import kernel

        return kernel

    def denial(self, call: ActionCall, routing: Dict[str, Any], agent: Any) -> str:
        """Read-only enforcement, which the tool pipeline cannot do for itself.

        The pipeline sees a tool call, not which agent asked, so an agent-scoped
        permission has to be settled here. Plan mode is separate: it is asked of its own
        hook by the executor, because it is the one policy that changes mid-session.
        """
        route = routing.get(call.name)
        if route is None or route[0] != "tool":
            return ""
        from agentevolver.permission import permission_manager

        allow = getattr(agent, "allow_read_only", None)
        if (
            permission_manager.restrict(getattr(agent, "permission_mode", None)) == "read_only"
            and route[1] in READ_ONLY_DENIED
            and not (callable(allow) and allow(route[1], call.args))
        ):
            return (
                f"read_only agent {getattr(agent, 'name', '?')!r} may not invoke "
                f"framework-mutating tool {route[1]!r}. Report findings instead of "
                "modifying anything."
            )
        return ""

    @staticmethod
    def _from_response(call: ActionCall, response: Any) -> ActionResult:
        """Normalise a manager Response into an ActionResult."""
        success = bool(getattr(response, "success", False))
        message = str(getattr(response, "message", "") or "")
        data = getattr(response, "data", None) or {}
        return ActionResult(
            call=call,
            output=message if success else "",
            error="" if success else (message or "the capability reported a failure"),
            final=bool(data.get("done")) if isinstance(data, dict) else False,
            extra=data if isinstance(data, dict) else {},
        )


__all__ = ["CapabilityRouter", "ToolRouter"]
