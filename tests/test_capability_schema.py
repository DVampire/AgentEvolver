"""Every callable capability describes itself the same way, in both projections.

Tools, agents, skills, connectors, environments and workflows are all reachable by a
model, and each used to hand-build its own function-calling dict. `CapabilitySchema` is
the single contract they now render: one JSON projection that is handed to the provider as
a tool definition, and one Markdown projection that is pasted into a prompt. The two have
to describe the same capability — when they drift, the model is told one set of parameters
and shown another, and the resulting invalid call looks like a model mistake.

The names carry meaning too: a connector or environment action is exposed as
`<capability>__<action>`, and dispatch splits on that separator to find its target. And
the per-capability lookup must agree with the bulk roster the agent loop actually sends,
which is why the projection returned by `function_callings()` is compared against
`get_schema` rather than trusted separately.
"""

from types import SimpleNamespace

import pytest

from agentevolver.agent.context import AgentContextManager
from agentevolver.agent.server import AgentManagerServer
from agentevolver.capability import CapabilitySchema
from agentevolver.connector.server import ConnectorManagerServer
from agentevolver.environment.server import EnvironmentManagerServer
from agentevolver.skill.server import SkillManagerServer
from agentevolver.tool.server import ToolManagerServer
from agentevolver.workflow import WorkflowContextManager


class _InfoManagerMixin:
    """Answers `get_info`/`list` from a fixed record, so no registry has to be loaded."""

    async def get_info(self, name):
        return self._test_info

    async def list(self):
        return [self._test_info.name]


def _manager(cls, info):
    """A real manager class with its lookup stubbed — the schema code under test is untouched."""
    derived = type(f"Test{cls.__name__}", (_InfoManagerMixin, cls), {})
    instance = derived()
    object.__setattr__(instance, "_test_info", info)
    return instance


def _assert_formats(json_schema, markdown, expected_name):
    """The minimum both projections must agree on: the name, and a described parameter object."""
    assert json_schema["function"]["name"] == expected_name
    assert json_schema["function"]["parameters"]["type"] == "object"
    assert markdown.startswith(f"## `{expected_name}`")
    assert "### Parameters" in markdown


@pytest.mark.asyncio
async def test_every_callable_manager_renders_both_projections_under_one_name(tmp_path):
    """Six managers, one contract — checked together because the failure is always partial.

    Each manager reaches its parameters differently (a tool's declared `function_calling`,
    a skill's `input_schema`, a connector's remote `inputSchema`, an environment action's
    inferred args, a workflow's `<schema>` block). Nothing forces a new one to render
    through `CapabilitySchema`, so the way this breaks is one manager quietly going back to
    hand-built output while the other five stay right — and a capability that is callable
    but undescribed is invisible to the model rather than broken loudly.

    `function_callings()` is compared to `get_schema` in the same loop because that roster,
    not the single lookup, is what the agent loop sends to the provider. If only one of the
    two were correct, the schema surfaced in prompts and the schema enforced at call time
    would disagree.
    """
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    tool = _manager(
        ToolManagerServer,
        SimpleNamespace(
            name="sample_tool",
            description="tool",
            type=None,
            function_calling={
                "type": "function",
                "function": {
                    "name": "sample_tool",
                    "description": "tool",
                    "parameters": parameters,
                },
            },
        ),
    )
    agent = _manager(AgentManagerServer, SimpleNamespace(name="sample_agent", description="agent"))
    skill = _manager(
        SkillManagerServer,
        SimpleNamespace(
            name="sample_skill",
            description="skill",
            type="worker",
            type_tags=["worker"],
            input_schema=parameters,
        ),
    )
    connector = _manager(
        ConnectorManagerServer,
        SimpleNamespace(
            name="sample_connector",
            description="connector",
            actions=["query"],
            action_schemas={"query": parameters},
        ),
    )
    action = SimpleNamespace(
        description="environment action",
        function_calling={
            "type": "function",
            "function": {"parameters": parameters},
        },
        args_schema=None,
    )
    environment = _manager(
        EnvironmentManagerServer,
        SimpleNamespace(
            name="sample_environment",
            actions={"act": action},
        ),
    )

    # Connectors and environments are addressed per action, so their exposed function name
    # is `<capability>__<action>` — dispatch splits on that separator to route the call.
    checks = [
        (tool, "sample_tool", None, "sample_tool"),
        (agent, "sample_agent", None, "sample_agent"),
        (skill, "sample_skill", None, "sample_skill"),
        (connector, "sample_connector", "query", "sample_connector__query"),
        (environment, "sample_environment", "act", "sample_environment__act"),
    ]
    for manager, name, action_name, function_name in checks:
        json_schema = await manager.get_schema(name, action=action_name, format="json")
        markdown = await manager.get_schema(name, action=action_name, format="md")
        _assert_formats(json_schema, markdown, function_name)
        projected = await manager.function_callings()
        assert projected[0][0] == json_schema

    workflow = WorkflowContextManager(
        builtin_dir=tmp_path / "missing",
        evaluation_path=tmp_path / "evaluations.json",
    )
    workflow.register("""
    <workflow name="complex_flow" description="complex">
      <inputs><input name="files" required="true"><schema for="files">
        {"type":"array","items":{"type":"string"},"minItems":1}
      </schema></inputs>
      <flow><agent name="general_agent" /></flow>
    </workflow>
    """)
    json_schema = await workflow.get_schema("complex_flow", format="json")
    markdown = await workflow.get_schema("complex_flow", format="md")
    _assert_formats(json_schema, markdown, "workflow__complex_flow")
    # A declared `<schema>` must survive whole. Collapsing it to a bare `array` would let
    # the model pass anything as an element and only fail once the workflow ran.
    assert json_schema["function"]["parameters"]["properties"]["files"]["items"] == {
        "type": "string"
    }


def test_a_schema_a_provider_would_reject_is_refused_where_it_is_built():
    """Both of these produce a tool definition that fails at the provider, far from its cause.

    A `required` entry naming a property that was never declared, and a `strict` schema
    that forgot `additionalProperties: false`, are both easy to write by hand and both
    accepted by every local type check. The provider rejects the whole request — meaning
    one malformed capability disables the entire tool roster for that call, and the error
    names the API, not the manager that produced the schema.
    """
    with pytest.raises(ValueError, match="required"):
        CapabilitySchema(
            name="bad",
            parameters={
                "type": "object",
                "properties": {},
                "required": ["missing"],
                "additionalProperties": False,
            },
        )
    with pytest.raises(ValueError, match="additionalProperties"):
        CapabilitySchema(name="bad", strict=True, parameters={"type": "object", "properties": {}})


def test_supplying_a_default_workspace_does_not_mutate_the_callers_config(tmp_path):
    """The manager fills in `base_dir`; it must fill in a copy.

    Agents require a `base_dir`, and schema inspection instantiates them without any
    class-specific config file — so the manager supplies one. The config dict it is handed
    belongs to the registry and is reused for later instantiations. Setting the key in
    place would pin the first agent's directory onto whatever is constructed next, so two
    agents would share a workspace and a `setdefault` on the second call would find the
    first one's value already there.
    """

    class SampleAgent:
        model_fields = {"name": SimpleNamespace(default="sample_agent")}

    manager = AgentContextManager(base_dir=str(tmp_path))
    original = {"model_name": "test-model"}
    prepared = manager._prepare_instance_config(SampleAgent, original)

    assert original == {"model_name": "test-model"}
    assert prepared["base_dir"] == str(tmp_path / "sample_agent")
    # An explicitly configured base_dir is a choice, not a gap: it must not be overridden.
    assert (
        manager._prepare_instance_config(
            SampleAgent,
            {"base_dir": "/explicit"},
        )["base_dir"]
        == "/explicit"
    )
