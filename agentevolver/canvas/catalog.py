"""Builds the palette: structural workflow steps, io declarations, and one
entry per registered tool / agent / workflow."""

from __future__ import annotations

from typing import Any, Dict, List

from agentevolver.canvas.types import NodeSpec, ParamSpec
from agentevolver.logger import logger


def _structural_specs(agent_names: List[str]) -> List[NodeSpec]:
    agent_options = sorted(agent_names) or None
    return [
        NodeSpec(
            id="io/input", category="io", label="Flow input",
            description="Declares one workflow input; reference it anywhere as ${inputs.<name>}.",
            params=[
                ParamSpec(name="name", label="Name", required=True, connectable=False),
                ParamSpec(name="input_type", label="Type", type="select",
                          options=["string", "number", "boolean", "array", "object"], connectable=False),
                ParamSpec(name="required", label="Required", type="boolean", connectable=False),
                ParamSpec(name="default", label="Default", connectable=False),
                ParamSpec(name="description", label="Description", multiline=True, connectable=False),
            ],
        ),
        NodeSpec(
            id="io/output", category="io", label="Flow output",
            description="Publishes one value as a named workflow output.",
            params=[ParamSpec(name="name", label="Name", required=True, connectable=False)],
        ),
        NodeSpec(
            id="step/map", category="structural", step_type="map", label="Map",
            description="Run the contained steps once per item, concurrently.",
            has_items=True, container=True,
            params=[
                ParamSpec(name="item_name", label="Item variable", default="item", connectable=False),
                ParamSpec(name="concurrency", label="Concurrency", type="number", connectable=False),
            ],
        ),
        NodeSpec(
            id="step/branch", category="structural", step_type="branch", label="Branch",
            description="Run the then/else steps depending on a ${...} test expression.",
            container=True,
            params=[ParamSpec(name="condition", label="Test", required=True, multiline=True,
                              description="e.g. ${check} or a comparison over step results", connectable=False)],
        ),
        NodeSpec(
            id="step/loop", category="structural", step_type="loop", label="Loop",
            description="Repeat the contained steps until/while a condition, bounded by max rounds.",
            container=True,
            params=[
                ParamSpec(name="max_rounds", label="Max rounds", type="number", required=True, connectable=False),
                ParamSpec(name="condition", label="Condition", multiline=True, connectable=False),
                ParamSpec(name="condition_mode", label="Mode", type="select",
                          options=["until", "while"], default="until", connectable=False),
            ],
        ),
        NodeSpec(
            id="step/reduce", category="structural", step_type="reduce", label="Reduce",
            description="Fold a list of results into one via an agent.",
            has_task=True, has_items=True,
            params=[ParamSpec(name="target", label="Agent", type="select", required=True,
                              options=agent_options, connectable=False)],
        ),
        NodeSpec(
            id="step/verify", category="structural", step_type="verify", label="Verify",
            description="Independently verify each item via an agent.",
            has_task=True, has_items=True,
            params=[
                ParamSpec(name="target", label="Agent", type="select", required=True,
                          options=agent_options, connectable=False),
                ParamSpec(name="concurrency", label="Concurrency", type="number", connectable=False),
                ParamSpec(name="min_votes", label="Min votes", type="number", connectable=False),
            ],
        ),
        NodeSpec(
            id="step/checkpoint", category="structural", step_type="checkpoint", label="Checkpoint",
            description="Persist run state so the workflow can resume from here.",
        ),
    ]


def _param_from_schema(name: str, schema: Dict[str, Any], required: bool) -> ParamSpec:
    json_type = schema.get("type")
    options = schema.get("enum")
    if isinstance(options, list) and options:
        param_type, options = "select", [str(option) for option in options]
    elif json_type in {"integer", "number"}:
        param_type, options = "number", None
    elif json_type == "boolean":
        param_type, options = "boolean", None
    elif json_type == "string":
        param_type, options = "string", None
    else:
        param_type, options = "json", None
    return ParamSpec(
        name=name,
        label=name.replace("_", " ").capitalize(),
        type=param_type,
        required=required,
        default=schema.get("default"),
        options=options,
        multiline=param_type == "string" and name in {"command", "content", "code", "script", "text", "prompt"},
        description=str(schema.get("description", "")),
    )


def _tool_spec(name: str, info: Any) -> NodeSpec:
    function_calling = getattr(info, "function_calling", None) or {}
    function = function_calling.get("function", {}) if isinstance(function_calling, dict) else {}
    parameters = function.get("parameters") if isinstance(function, dict) else None
    params: List[ParamSpec] = []
    if isinstance(parameters, dict):
        required = set(parameters.get("required") or [])
        properties = parameters.get("properties")
        if isinstance(properties, dict):
            params = [
                _param_from_schema(key, value if isinstance(value, dict) else {}, key in required)
                for key, value in properties.items()
            ]
    return NodeSpec(
        id=f"tool/{name}", category="tool", step_type="tool", target=name,
        label=name.removesuffix("_tool").replace("_", " ").capitalize(),
        description=str(getattr(info, "description", "") or ""),
        params=params,
    )


def _agent_spec(name: str, info: Any) -> NodeSpec:
    return NodeSpec(
        id=f"agent/{name}", category="agent", step_type="agent", target=name,
        label=name.removesuffix("_agent").replace("_", " ").capitalize(),
        description=str(getattr(info, "description", "") or ""),
        has_task=True,
    )


def _workflow_spec(name: str, definition: Any) -> NodeSpec:
    params = [
        ParamSpec(
            name=input_name,
            label=input_name.replace("_", " ").capitalize(),
            type="string" if getattr(spec, "type", "string") == "string" else "json",
            required=bool(getattr(spec, "required", False)),
            default=getattr(spec, "default", None),
            description=str(getattr(spec, "description", "")),
        )
        for input_name, spec in (getattr(definition, "inputs", {}) or {}).items()
    ]
    return NodeSpec(
        id=f"workflow/{name}", category="workflow", step_type="workflow", target=name,
        label=name.replace("_", " ").capitalize(),
        description=str(getattr(definition, "description", "") or ""),
        params=params,
    )


async def build_catalog() -> List[NodeSpec]:
    """Assemble the palette; registry failures degrade to a smaller palette."""
    agent_names: List[str] = []
    try:
        from agentevolver.agent import agent_manager
        agent_names = await agent_manager.list()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"| ⚠️ Canvas palette: agent registry unavailable: {exc}")

    specs = _structural_specs(agent_names)

    try:
        from agentevolver.tool import tool_manager
        for name in await tool_manager.list():
            try:
                info = await tool_manager.get_info(name)
                if info is not None:
                    specs.append(_tool_spec(name, info))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"| ⚠️ Canvas palette: skipping tool {name}: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"| ⚠️ Canvas palette: tool registry unavailable: {exc}")

    for name in agent_names:
        try:
            from agentevolver.agent import agent_manager
            specs.append(_agent_spec(name, await agent_manager.get_info(name)))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"| ⚠️ Canvas palette: skipping agent {name}: {exc}")

    try:
        from agentevolver.workflow import workflow_manager
        for name in workflow_manager.list():
            try:
                specs.append(_workflow_spec(name, workflow_manager.get(name)))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"| ⚠️ Canvas palette: skipping workflow {name}: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"| ⚠️ Canvas palette: workflow registry unavailable: {exc}")

    return specs


__all__ = ["build_catalog"]
