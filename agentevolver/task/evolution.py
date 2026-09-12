"""Opt-in task evidence for a required capability improvement.

Receipts come from executed actions, never from a model's final prose. They survive
conversation compaction in task_state. This checks provenance and lifecycle, not the
semantic truth of an evaluator's judgment. Ordinary tasks have no such requirement.
"""

from copy import deepcopy


def required(ctx):
    settings = (getattr(ctx, "extra", None) or {}).get("task_manifest", {}).get("evolution", {})
    return settings.get("require_verified_improvement") is True or bool(settings.get("required_modules"))


def required_modules(ctx):
    """Explicit experiment coverage; ordinary tasks do not impose a type quota."""
    from agentevolver.capability.types import COMPONENT_TYPES

    modules = (getattr(ctx, "extra", None) or {}).get("task_manifest", {}).get(
        "evolution", {}
    ).get("required_modules", [])
    allowed = {entry.type for entry in COMPONENT_TYPES}
    if not isinstance(modules, list) or any(not isinstance(m, str) or m not in allowed for m in modules):
        raise ValueError("evolution.required_modules must be a list of supported component types")
    return list(dict.fromkeys(modules))


def state(ctx):
    return ctx.extra.setdefault("task_state", {}).setdefault(
        "evolution_evidence", {"calls": {}, "candidates": {}}
    )


def identity(report):
    return ":".join(str(report.get(k) or "") for k in ("module", "name", "version"))


def observe(ctx, results, routing):
    if not required(ctx):
        return
    audit = state(ctx)
    for result in results:
        call = result.call
        if call.id in audit["calls"]:
            continue
        route = tuple(routing.get(call.name) or ())
        receipt = {"sequence": len(audit["calls"]), "route": list(route),
                   "ok": result.ok and (result.extra or {}).get("exit_code") in (None, 0),
                   "background": bool(
                       call.args.get("background") or call.args.get("run_in_background")
                   )}
        audit["calls"][call.id] = receipt
        if not result.ok:
            continue
        data = result.extra or {}
        if route[:2] == ("tool", "adoption_tool"):
            action = call.args.get("action")
            if action == "register" and data.get("registered") and data.get("version"):
                key = identity(data)
                # An immutable version keeps its original provenance and verdict
                # when a caller repeats registration; no new candidate was created.
                audit["candidates"].setdefault(key, {
                    "module": data["module"], "name": data["name"],
                    "version": data["version"], "registration": call.id,
                })
            elif action == "record_decision" and isinstance(data.get("decision"), dict):
                decision = data["decision"]
                candidate = audit["candidates"].get(identity(decision["evaluation"]))
                if candidate is not None:
                    candidate.update(decision=deepcopy(decision), decision_call=call.id,
                                     decision_sequence=receipt["sequence"])
                    candidate.pop("use", None)
            continue
        # A direct invocation after adoption identifies the actual callable consumer.
        # Looking at the manifest, registering, or reading a source file is not use.
        from agentevolver.extension import extension_manager

        for candidate in audit["candidates"].values():
            if route[:2] != (candidate["module"], candidate["name"]):
                continue
            active = extension_manager.read_manifest().find(*route[:2])
            if active is not None and active.version == candidate["version"]:
                receipt["component"] = identity(candidate)


def _decision_problem(candidate, audit):
    decision = candidate.get("decision") or {}
    report = decision.get("evaluation") or {}
    if decision.get("decision") != "keep" or report.get("verdict") != "pass":
        return "a passing version-scoped keep decision is missing"
    gap = report.get("capability_gap") or {}
    if not all(str(gap.get(k) or "").strip() for k in (
        "user_need", "required_operation", "limitation", "acceptance_criterion"
    )):
        return "the evaluated report needs a concrete capability_gap diagnosis"
    for field in ("observation_evidence_ids", "baseline_evidence_ids"):
        ids = gap.get(field) or []
        if not ids or any(i not in audit["calls"] for i in ids):
            return f"capability_gap.{field} must cite this task's executed calls"
        registration = audit["calls"][candidate["registration"]]["sequence"]
        if any(audit["calls"][i]["sequence"] >= registration for i in ids):
            return "observation and baseline evidence must precede candidate registration"
    cases = report.get("cases") or []
    comparisons = [c for c in cases if c.get("kind") == "comparison"]
    reuse = [c for c in cases if c.get("kind") in ("reuse", "regression")]
    if not comparisons or not reuse:
        return "evaluation needs a comparison and an independent reuse/regression case"
    for case in cases:
        if not case.get("passed") or any(i not in audit["calls"] for i in case["evidence_ids"]):
            return "evaluation cases must cite executed passing evidence"
    if not any(set(c["evidence_ids"]) != set(r["evidence_ids"])
               for c in comparisons for r in reuse):
        return "the reuse case cannot simply repeat the comparison's evidence"
    return ""


def record_use(ctx, report):
    """Bind a claimed real-work outcome to observed post-adoption execution."""
    if not required(ctx):
        raise ValueError("record_use requires a task with evolution.require_verified_improvement")
    audit = state(ctx)
    candidate = audit["candidates"].get(identity(report))
    if candidate is None:
        raise ValueError("No candidate registered by this task matches the usage report")
    problem = _decision_problem(candidate, audit)
    if problem:
        raise ValueError(problem)
    from agentevolver.extension import extension_manager

    active = extension_manager.read_manifest().find(candidate["module"], candidate["name"])
    if active is None or active.version != candidate["version"]:
        raise ValueError("Usage must name the exact active adopted version")
    consumer_id = report.get("consumer_call_id")
    if not isinstance(consumer_id, str) or not consumer_id.strip():
        raise ValueError("consumer_call_id must be an observed tool-call ID")
    consumer = audit["calls"].get(consumer_id) or {}
    if (consumer.get("component") != identity(candidate) or not consumer.get("ok")
            or consumer.get("background")
            or consumer.get("sequence", -1) <= candidate["decision_sequence"]):
        raise ValueError("consumer_call_id must name a successful synchronous candidate invocation after keep")
    ids = report.get("evidence_ids") or []
    if (not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids)
            or not isinstance(report.get("outcome"), str) or not report["outcome"].strip()):
        raise ValueError("Usage requires outcome and real-work evidence_ids")
    for call_id in ids:
        receipt = audit["calls"].get(call_id) or {}
        if (not receipt.get("ok") or receipt.get("background")
                or receipt.get("sequence", -1) < consumer["sequence"]):
            raise ValueError("Usage evidence must be successful calls at or after consumer use")
    if candidate["module"] == "skill":
        # Loading instructions is only the first half of using a skill.
        operations = [audit["calls"][i] for i in ids if i != consumer_id]
        if not any(r["sequence"] > consumer["sequence"] and r["route"][:2] not in (
            ["tool", "inspect_tool"], ["tool", "adoption_tool"], ["tool", "done_tool"]
        ) and r["route"] and r["route"][0] != "skill" for r in operations):
            raise ValueError("Reading a skill alone is not use; cite a subsequent real product operation")
    candidate["use"] = deepcopy(report)
    return candidate["use"]


def status(ctx):
    if not required(ctx):
        return {"required": False, "ready": True}
    from agentevolver.extension import extension_manager

    audit = state(ctx)
    expected = required_modules(ctx)
    reasons = []
    ready = []
    for key, candidate in audit["candidates"].items():
        active = extension_manager.read_manifest().find(candidate["module"], candidate["name"])
        if active is None or active.version != candidate["version"]:
            continue
        problem = _decision_problem(candidate, audit)
        decision = candidate.get("decision") or {}
        if decision.get("decision") in ("rollback", "unload"):
            problem = "rejected candidate is still active; execute rollback/unload"
        if problem:
            reasons.append(f"{key}: {problem}")
        elif candidate.get("use"):
            ready.append(key)
        else:
            reasons.append(f"{key}: post-adoption consumer evidence is missing; call adoption_tool record_use")
    if not ready:
        reasons.append("No verified capability improvement has completed registration, evaluation, keep and real use")
    verified_modules = {audit["candidates"][key]["module"] for key in ready}
    missing_modules = [module for module in expected if module not in verified_modules]
    if missing_modules:
        reasons.append("Required component types still need verified adoption and real use: " + ", ".join(missing_modules))
    return {"required": True, "ready": bool(ready) and not reasons,
            "required_modules": expected, "missing_modules": missing_modules,
            "verified_components": ready, "reasons": reasons,
            "receipts": {key: {
                "registration_call_id": audit["candidates"][key]["registration"],
                "decision_call_id": audit["candidates"][key]["decision_call"],
                "use": deepcopy(audit["candidates"][key]["use"]),
            } for key in ready}}


def live_notice(ctx):
    try:
        check = status(ctx)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return f"Evolution evidence is unavailable: {error}. Repair the evidence store before claiming completion."
    if not check["required"]:
        return ""
    if check["ready"]:
        return "System evolution evidence complete: " + ", ".join(check["verified_components"])
    return ("Required system evolution remains incomplete. After actual environment interaction or user feedback, diagnose "
            "the required operation and baseline limitation before changing a capability. "
            + "; ".join(check["reasons"]) +
            ". Report a concrete blocker if this cannot be completed; do not invent evidence.")


def finalize(ctx, response):
    from agentevolver.task.self_review import status as review_status

    try:
        review = review_status(ctx)
    except (OSError, ValueError, KeyError, TypeError) as error:
        review = {"required": True, "ready": False,
                  "reasons": [f"Could not verify the declared browser review: {error}"]}
    if review["required"]:
        response = response.model_copy(update={
            "success": response.success and review["ready"],
            "message": response.message + (
                "\n\nWebsite self-review incomplete: " + "; ".join(review["reasons"])
                if response.success and not review["ready"] else ""),
            "data": {**(response.data or {}), "self_review_status": review},
        })
    try:
        check = status(ctx)
    except (OSError, ValueError, KeyError, TypeError) as error:
        check = {"required": True, "ready": False,
                 "reasons": [f"Could not verify evolution evidence: {error}"]}
    if not check["required"]:
        return response
    update = {"data": {**(response.data or {}), "evolution_status": check}}
    if not check["ready"] and "System evolution incomplete:" not in response.message:
        update.update(success=False, message=(
            response.message + "\n\nSystem evolution incomplete: " + "; ".join(check["reasons"])
        ))
    return response.model_copy(update=update)
