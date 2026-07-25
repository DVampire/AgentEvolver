"""The data-pipeline capability kinds: plugins (datasource) + process + benchmark.

A ``plugin`` is a packaging unit surfaced on the canvas as a semantic
``datasource`` node; a ``process`` node is a pure transform; a ``benchmark`` node
evaluates. All three are workflow step types dispatched by ``workflow_runtime``,
and every one returns the canonical ``{message, data, files}`` envelope so nodes
compose across edges. These tests pin that wiring without touching the network.
"""

from __future__ import annotations

import pytest

from agentevolver.data import data_manager
from agentevolver.plugins import FMPPlugin, Plugin, plugin_manager
from agentevolver.process import (
    DeriveReturnProcessor,
    FilterRowsProcessor,
    SelectFieldsProcessor,
    process_manager,
)
from agentevolver.response.types import Response, ResponseType
from agentevolver.workflow.compiler import WorkflowCompiler
from agentevolver.workflow.runtime import workflow_runtime
from agentevolver.workflow.types import StepType


class _StubSource(Plugin):
    """Deterministic data-source plugin standing in for a real provider."""

    name: str = "stub_source"
    description: str = "Fixed records for tests."
    kind: str = "data_source"

    async def __call__(self, **kwargs) -> Response:
        return Response(
            type=ResponseType.TOOL, success=True, message="stub 2 rows",
            data={"records": [
                {"date": "2024-01-01", "close": 1.0, "volume": 10, "junk": "x"},
                {"date": "2024-01-02", "close": 2.0, "volume": 20, "junk": "y"},
            ]},
        )


def _pipeline_html(source: str) -> str:
    return f"""<html><body><workflow name="t" version="1.0.0"><flow>
      <datasource id="src" name="{source}"/>
      <process id="clean" name="select_fields">
        <arg name="records" value="${{src.data.records}}"/>
        <arg name="fields" value='["date","close"]'/>
      </process>
    </flow></workflow></body></html>"""


@pytest.mark.asyncio
async def test_default_capabilities_register() -> None:
    await plugin_manager.initialize()
    await process_manager.initialize()
    assert "yahoo" in await plugin_manager.list()
    assert "select_fields" in await process_manager.list()


@pytest.mark.asyncio
async def test_derive_return_and_filter_rows() -> None:
    rows = [{"d": "1", "close": 10.0}, {"d": "2", "close": 11.0}, {"d": "3", "close": 9.0}]
    derived = await DeriveReturnProcessor()(records=rows, field="close")
    returns = [r["return"] for r in derived.data["records"]]
    assert returns[0] is None and returns[1] == pytest.approx(0.1)

    # numeric string comparison value is coerced, so this filters numerically
    kept = await FilterRowsProcessor()(records=rows, field="close", op="gt", value="10")
    assert [r["close"] for r in kept.data["records"]] == [11.0]


@pytest.mark.asyncio
async def test_fmp_requires_key() -> None:
    # No key configured / no FMP_API_KEY → a graceful failed Response, not a crash.
    result = await FMPPlugin()(symbol="AAPL", api_key="")
    assert not result.success and "key" in result.message.lower()


@pytest.mark.asyncio
async def test_select_fields_coerces_json_string_fields() -> None:
    rows = [{"date": "d", "close": 1.0, "junk": "x"}]
    # fields arrives as a JSON string (how list ports compile), not a real list.
    result = await SelectFieldsProcessor()(records=rows, fields='["date","close"]')
    assert result.success
    assert result.data["records"] == [{"date": "d", "close": 1.0}]


def test_compiler_accepts_pipeline_tags() -> None:
    src = """<html><body><workflow name="t" version="1.0.0"><flow>
      <datasource id="a" name="yahoo"/>
      <process id="b" name="select_fields"/>
      <benchmark id="c" name="gsm8k"/>
    </flow></workflow></body></html>"""
    definition = WorkflowCompiler().compile(src)
    kinds = {step.id: step.type for step in definition.program}
    assert kinds == {"a": StepType.DATASOURCE, "b": StepType.PROCESS, "c": StepType.BENCHMARK}


@pytest.mark.asyncio
async def test_runtime_runs_datasource_to_process() -> None:
    await plugin_manager.initialize()
    await process_manager.initialize()
    await plugin_manager.register(_StubSource(), override=True)

    definition = WorkflowCompiler().compile(_pipeline_html("stub_source"))
    run = await workflow_runtime.run(definition)

    assert run.successful, run.error
    clean = run.invocations["root.1:clean"].output
    # The datasource's records crossed the ${src.data.records} edge and were
    # projected to exactly the selected fields.
    assert clean["data"]["records"] == [
        {"date": "2024-01-01", "close": 1.0},
        {"date": "2024-01-02", "close": 2.0},
    ]


@pytest.mark.asyncio
async def test_pipeline_saves_and_loads_dataset() -> None:
    await plugin_manager.initialize()
    await process_manager.initialize()
    await data_manager.initialize()
    await plugin_manager.register(_StubSource(), override=True)

    # datasource → process → data(save_dataset), each edge over the ${node.data} port.
    src = """<html><body><workflow name="t" version="1.0.0"><flow>
      <datasource id="src" name="stub_source"/>
      <process id="clean" name="select_fields">
        <arg name="records" value="${src.data}"/>
        <arg name="fields" value='["date","close"]'/>
      </process>
      <data id="save" name="save_dataset">
        <arg name="name" value="pytest_pipeline_ds"/>
        <arg name="records" value="${clean.data}"/>
      </data>
    </flow></workflow></body></html>"""
    run = await workflow_runtime.run(WorkflowCompiler().compile(src))
    assert run.successful, run.error

    # The saved dataset reads back as exactly the processed records.
    loaded = await data_manager(name="load_dataset", input={"name": "pytest_pipeline_ds"})
    assert loaded.success
    assert loaded.data["records"] == [
        {"date": "2024-01-01", "close": 1.0},
        {"date": "2024-01-02", "close": 2.0},
    ]
