"""Required entity families need task-local, versioned execution receipts."""
from types import SimpleNamespace

import pytest

from agentevolver.agent.loop.decision import ActionCall, ActionResult
from agentevolver.response import Response, ResponseType
from agentevolver.task import evolution


@pytest.fixture
def audit(monkeypatch):
    from agentevolver.extension import extension_manager

    active = {}
    manifest = SimpleNamespace(find=lambda module, name: active.get((module, name)))
    monkeypatch.setattr(type(extension_manager), "read_manifest", lambda self: manifest)
    ctx = SimpleNamespace(extra={"task_manifest": {"evolution": {
        "require_verified_improvement": True,
        "required_modules": ["skill", "agent", "connector"],
    }}})
    return ctx, active


def observe(ctx, call_id, route, *, args=None, extra=None):
    name = "__".join(route)
    evolution.observe(ctx, [ActionResult(
        ActionCall(id=call_id, name=name, args=args or {}), extra=extra or {},
    )], {name: route})


def adopted(audit, module, *, version="1.0.0", use=True, name=None):
    ctx, active = audit
    name = name or f"reusable_{module}"
    prefix = f"{name}-{version}"
    ids = {label: f"{prefix}-{label}" for label in (
        "observation", "baseline", "registration", "comparison", "reuse",
        "decision", "consumer", "operation",
    )}
    component = dict(module=module, name=name, version=version)
    observe(ctx, ids["observation"], ("environment", "browser_environment", "state"))
    observe(ctx, ids["baseline"], ("tool", "bash_tool"))
    active[(module, name)] = SimpleNamespace(**component)
    observe(ctx, ids["registration"], ("tool", "adoption_tool"),
            args={"action": "register"}, extra={**component, "registered": True})
    route = (module, name, "fetch") if module == "connector" else (module, name)
    observe(ctx, ids["comparison"], route)
    observe(ctx, ids["reuse"], route)
    report = {**component, "verdict": "pass", "capability_gap": {
        "user_need": "A reconstructable investigation from changing source packets",
        "required_operation": "Preserve provenance across a revised investigation",
        "limitation": "The baseline drops source identity on the changed input",
        "acceptance_criterion": "Preserve identity on comparison and independent packet",
        "observation_evidence_ids": [ids["observation"]],
        "baseline_evidence_ids": [ids["baseline"]],
    }, "cases": [
        {"kind": "comparison", "passed": True,
         "evidence_ids": [ids["baseline"], ids["comparison"]]},
        {"kind": "reuse", "passed": True, "evidence_ids": [ids["reuse"]]},
    ]}
    observe(ctx, ids["decision"], ("tool", "adoption_tool"),
            args={"action": "record_decision"},
            extra={"decision": {"decision": "keep", "evaluation": report}})
    observe(ctx, ids["consumer"], route)
    usage = {**component, "consumer_call_id": ids["consumer"],
             "evidence_ids": [ids["consumer"]], "outcome": "New packet used in the product"}
    if use:
        observe(ctx, ids["operation"], ("environment", "browser_environment", "state"))
        usage["evidence_ids"].append(ids["operation"])
        evolution.record_use(ctx, usage)
    return usage


def test_a_tool_cannot_substitute_for_required_entity_families(audit):
    ctx, _ = audit
    adopted(audit, "tool")
    check = evolution.status(ctx)
    assert check["verified_components"] == ["tool:reusable_tool:1.0.0"]
    assert check["missing_modules"] == ["skill", "agent", "connector"]
    assert not check["ready"]
    response = evolution.finalize(ctx, Response(
        type=ResponseType.AGENT, success=True, message="Website published.",
    ))
    assert not response.success
    assert "skill, agent, connector" in response.message


def test_all_required_families_need_post_adoption_use(audit):
    ctx, _ = audit
    adopted(audit, "skill")
    adopted(audit, "agent")
    usage = adopted(audit, "connector", use=False)
    assert evolution.status(ctx)["missing_modules"] == ["connector"]
    evolution.record_use(ctx, usage)
    check = evolution.status(ctx)
    assert check["ready"] and check["missing_modules"] == []
    assert len(check["receipts"]) == 3


def test_loading_a_skill_alone_does_not_satisfy_coverage(audit):
    ctx, _ = audit
    usage = adopted(audit, "skill", use=False)
    with pytest.raises(ValueError, match="Reading a skill alone"):
        evolution.record_use(ctx, usage)
    assert "skill" in evolution.status(ctx)["missing_modules"]


def test_optimized_version_requires_its_own_decision_and_consumer(audit):
    ctx, active = audit
    for module in ("skill", "agent", "connector"):
        adopted(audit, module)
    assert evolution.status(ctx)["ready"]
    # Simulate a registered update before any new evaluation: old use is insufficient.
    active[("agent", "reusable_agent")].version = "1.1.0"
    assert evolution.status(ctx)["missing_modules"] == ["agent"]
    usage = adopted(audit, "agent", version="1.1.0", use=False)
    assert not evolution.status(ctx)["ready"]
    evolution.record_use(ctx, usage)
    check = evolution.status(ctx)
    assert check["ready"]
    assert "agent:reusable_agent:1.1.0" in check["verified_components"]
    assert "agent:reusable_agent:1.0.0" not in check["verified_components"]
    del active[("connector", "reusable_connector")]
    assert evolution.status(ctx)["missing_modules"] == ["connector"]


def test_new_task_cannot_claim_previous_tasks_components(audit):
    ctx, _ = audit
    for module in ("skill", "agent", "connector"):
        adopted(audit, module)
    another = SimpleNamespace(extra={"task_manifest": ctx.extra["task_manifest"]})
    assert not evolution.status(another)["ready"]
    assert evolution.status(another)["verified_components"] == []


@pytest.mark.parametrize("modules", ["skill", ["connetcor"], [None]])
def test_invalid_required_modules_fail_closed(audit, modules):
    ctx, _ = audit
    ctx.extra["task_manifest"]["evolution"]["required_modules"] = modules
    with pytest.raises(ValueError, match="supported component types"):
        evolution.status(ctx)
    result = evolution.finalize(ctx, Response(
        type=ResponseType.AGENT, success=True, message="Finished.",
    ))
    assert not result.success


def test_ordinary_tasks_keep_optional_evolution():
    assert evolution.status(SimpleNamespace(extra={})) == {"required": False, "ready": True}


def test_two_environments_need_distinct_names_and_consumers(audit):
    ctx, active = audit
    ctx.extra["task_manifest"]["evolution"] = {
        "required_module_counts": {"connector": 1, "environment": 2},
    }
    adopted(audit, "connector")
    adopted(audit, "environment", name="factors")
    adopted(audit, "environment", name="factors", version="1.1.0")
    check = evolution.status(ctx)
    assert not check["ready"]
    assert check["missing_module_counts"] == {"environment": 1}
    use = adopted(audit, "environment", name="strategies", use=False)
    assert not evolution.status(ctx)["ready"]
    evolution.record_use(ctx, use)
    assert evolution.status(ctx)["ready"]
    del active[("environment", "factors")]
    assert evolution.status(ctx)["missing_module_counts"] == {"environment": 1}


@pytest.mark.parametrize("counts", [{"environment": True}, {"environment": 0},
    {"environment": -1}, {"environment": 1.5}, {"connetcor": 1}, ["environment"]])
def test_invalid_module_counts_fail_closed(audit, counts):
    ctx, _ = audit
    ctx.extra["task_manifest"]["evolution"] = {"required_module_counts": counts}
    with pytest.raises(ValueError, match="positive integers"):
        evolution.status(ctx)
    result = evolution.finalize(ctx, Response(type=ResponseType.AGENT, success=True, message="Done"))
    assert not result.success


def test_counts_extend_existing_required_modules(audit):
    ctx, _ = audit
    ctx.extra["task_manifest"]["evolution"]["required_module_counts"] = {"environment": 2, "skill": 2}
    assert evolution.required_module_counts(ctx) == {"skill": 2, "agent": 1, "connector": 1, "environment": 2}
