"""Browser-execution receipts for tasks explicitly requesting self-review.

This establishes that a pinned artifact was visited, acted on and observed again.
It does not turn the builder's judgment into independent product acceptance.
"""
from urllib.parse import urlsplit


def enabled(ctx):
    return (getattr(ctx, "extra", None) or {}).get("task_manifest", {}).get(
        "run_policy", {}
    ).get("self_review") is True


def _state(ctx):
    return ctx.extra.setdefault("task_state", {})


def _url(release):
    return release.get("release_url") or release.get("site_url") or release.get("url") or ""


def _same_site(actual, target):
    actual, target = urlsplit(actual), urlsplit(target)
    return (bool(target.netloc) and actual.scheme == target.scheme
            and actual.netloc == target.netloc
            and (actual.path == target.path or actual.path.startswith(target.path.rstrip("/") + "/")))


def _key(release):
    return str(release.get("source_revision") or "") + " " + _url(release)


def _targets(ctx):
    task = _state(ctx)
    preview = (task.get("deployment_contract") or {}).get("latest_preview")
    return [*(task.get("deployment_release_history") or []), *([preview] if preview else [])]


def observe_state(ctx, observation):
    if not enabled(ctx):
        return
    if not isinstance(observation, dict):
        return
    extra = (observation or {}).get("extra") or {}
    url = extra.get("url") or ""
    receipts = _state(ctx).setdefault("browser_reviews", {})
    for target in _targets(ctx):
        if not _same_site(url, _url(target)) or not observation.get("state"):
            continue
        receipt = receipts.setdefault(_key(target), {})
        receipt["visited"] = True
        if receipt.get("interaction_call_id") and not receipt.get("failed_call_id"):
            receipt["observed_after_interaction"] = True


def observe_actions(ctx, results, routing, observation):
    if not enabled(ctx):
        return
    url = ((observation or {}).get("extra") or {}).get("url") or ""
    receipts = _state(ctx).setdefault("browser_reviews", {})
    for result in results:
        route = tuple(routing.get(result.call.name) or ())
        if route[:2] != ("environment", "browser_environment") or len(route) < 3:
            continue
        if not result.ok:
            for target in _targets(ctx):
                if _same_site(url, _url(target)):
                    receipt = receipts.setdefault(_key(target), {})
                    receipt["failed_call_id"] = result.call.id
                    receipt["observed_after_interaction"] = False
            continue
        if route[2] not in {
                    "click", "double_click", "type_text", "keypress", "drag", "command",
                }:
            continue
        for target in _targets(ctx):
            if _same_site(url, _url(target)):
                receipt = receipts.setdefault(_key(target), {})
                receipt["interaction_call_id"] = result.call.id
                receipt["observed_after_interaction"] = False
                receipt.pop("failed_call_id", None)


def blocker(ctx, release):
    if not enabled(ctx):
        return ""
    receipt = _state(ctx).get("browser_reviews", {}).get(_key(release), {})
    if not (receipt.get("visited") and not receipt.get("failed_call_id") and receipt.get("interaction_call_id")
            and receipt.get("observed_after_interaction")):
        return ("Use the native browser at " + _url(release)
                + ", perform a meaningful interaction, and observe its result before continuing")
    return ""


def status(ctx):
    if not enabled(ctx):
        return {"required": False, "ready": True}
    from agentevolver.deploy import deployment_manager

    deployment_manager.prepare_task(ctx)
    releases = deployment_manager.release_status(ctx)
    reasons = [releases["reason"]] if not releases.get("ready") else []
    for release in _state(ctx).get("deployment_release_history") or []:
        reason = blocker(ctx, release)
        if reason:
            reasons.append(reason)
    return {"required": True, "ready": not reasons, "reasons": reasons,
            "completed_releases": releases.get("completed_releases", 0),
            "verification": "builder_self_review"}
