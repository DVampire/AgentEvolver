"""HTML views of the canonical request sent to a model.

The structured :class:`RequestSnapshot` remains the source of truth.  This module only
creates an escaped, human-readable side view; nothing is parsed back from the HTML and a
rendering failure can never change a model call.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
from html import escape
from typing import Any, Optional

from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.trace.types import TraceEvent, TraceEventType
from agentevolver.visual import css_path, js_path


_BACKGROUND_TASKS: set[asyncio.Task] = set()
_LATEST_TASKS: dict[str, asyncio.Task] = {}
_UNSAFE_PATH = re.compile(r"[^A-Za-z0-9_.-]+")
_LAYER_LABELS = {
    "stable": "stable prefix",
    "task": "task anchor",
    "checkpoint": "checkpoint",
    "recent": "recent turn",
    "live": "live context",
}


def _safe_component(value: Any, fallback: str) -> str:
    cleaned = _UNSAFE_PATH.sub("_", str(value or "")).strip("._")
    return cleaned or fallback


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return _json(content)


def _content_size(content: Any) -> int:
    return len(_content_text(content))


def _tokens(value: Any) -> int:
    """Use the same conservative meter as request-pressure accounting."""
    from agentevolver.model.pressure import estimate_tokens

    return estimate_tokens(value)


def _message_layer(message: dict[str, Any], index: int, total: int) -> str:
    """Classify one message in the cache-oriented context layout."""
    role = str(message.get("role") or "unknown").lower()
    text = _content_text(message.get("content", ""))
    if role == "system":
        return "stable"
    if role == "user" and index == total - 1:
        return "live"
    if role == "user" and ("<task>" in text or index <= 1):
        return "task"
    if "<memory-checkpoint>" in text or "<memory>" in text:
        return "checkpoint"
    return "recent"


def _badge(text: Any, badge_type: str = "") -> str:
    suffix = f" badge-{escape(badge_type)}" if badge_type else ""
    return f'<span class="badge{suffix}">{escape(str(text))}</span>'


def _render_json_block(title: str, value: Any, *, open_: bool = False) -> str:
    opened = " open" if open_ else ""
    return (
        f'<details class="json-block"{opened}>'
        f"<summary>{escape(title)}</summary>"
        f'<pre class="json-view">{escape(_json(value))}</pre>'
        "</details>"
    )


def _render_tool_call(call: Any, index: int) -> str:
    data = call if isinstance(call, dict) else {"value": call}
    function = data.get("function") or {}
    name = function.get("name") or data.get("name") or f"tool call {index + 1}"
    call_id = data.get("id") or ""
    arguments = function.get("arguments", data.get("arguments", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError):
            pass
    return "".join([
        '<div class="tool-call">',
        '<div class="tool-call-head">',
        f'<span class="tool-call-name">{escape(str(name))}</span>',
        _badge(call_id, "id") if call_id else "",
        "</div>",
        f'<pre class="json-view">{escape(_json(arguments) if not isinstance(arguments, str) else arguments)}</pre>',
        "</div>",
    ])


def _render_message(message: Any, index: int, total: int) -> str:
    data = message if isinstance(message, dict) else {"role": "unknown", "content": message}
    role = str(data.get("role") or "unknown").lower()
    content = data.get("content", "")
    cached = bool(data.get("cache"))
    layer = _message_layer(data, index, total)
    layer_label = _LAYER_LABELS[layer]
    tool_calls = data.get("tool_calls") or []
    search = " ".join((role, layer, layer_label)).lower()

    badges = [
        _badge(f"#{index + 1}", "index"),
        _badge(layer_label, "layer"),
        _badge("cache boundary" if cached else "uncached", "cache" if cached else "muted"),
        _badge(f"{_content_size(content):,} chars", "muted"),
    ]
    if data.get("name"):
        badges.append(_badge(data["name"], "name"))
    if data.get("tool_call_id"):
        badges.append(_badge(data["tool_call_id"], "id"))
    if data.get("is_error") is not None:
        badges.append(_badge("error" if data["is_error"] else "success", "error" if data["is_error"] else "ok"))

    extras = {
        key: value for key, value in data.items()
        if key not in {"role", "cache", "content", "tool_calls", "provider_state", "name", "tool_call_id", "is_error"}
    }
    parts = [
        f'<article id="message-{index + 1}" class="message-card role-{escape(role)} layer-{escape(layer)}" '
        f'data-role="{escape(role)}" data-layer="{escape(layer)}" '
        f'data-cache="{"boundary" if cached else "uncached"}" '
        f'data-search="{escape(search, quote=True)}">',
        '<header class="message-head">',
        f'<span class="role-mark">{escape(role)}</span>',
        '<span class="message-badges">', *badges, "</span>",
        '<button class="message-toggle" type="button" data-expand-message>Expand</button>',
        "</header>",
        f'<pre class="message-content">{escape(_content_text(content))}</pre>',
    ]
    if tool_calls:
        parts += [
            f'<section class="tool-calls"><h3>{len(tool_calls)} tool call(s)</h3>',
            *[_render_tool_call(call, call_index) for call_index, call in enumerate(tool_calls)],
            "</section>",
        ]
    if data.get("provider_state"):
        parts.append(_render_json_block("Provider state", data["provider_state"]))
    if extras:
        parts.append(_render_json_block("Additional message fields", extras))
    parts.append("</article>")
    return "\n".join(parts)


def _render_sequence(messages: list[Any]) -> str:
    """Compact navigable view of the exact provider-neutral message order."""
    if not messages:
        return ""
    nodes = []
    for index, message in enumerate(messages):
        data = message if isinstance(message, dict) else {"role": "unknown"}
        role = str(data.get("role") or "unknown").lower()
        layer = _message_layer(data, index, len(messages))
        detail = data.get("name") if role == "tool" else None
        label = f"{role} · {detail}" if detail else role
        cached = " cache-boundary" if data.get("cache") else ""
        nodes.append("".join([
            f'<button class="sequence-node role-{escape(role)}{cached}" '
            f'data-message-index="{index + 1}" type="button" ',
            f'title="{escape(_LAYER_LABELS[layer], quote=True)}">',
            f'<small>#{index + 1}</small><strong>{escape(label)}</strong>',
            "</button>",
        ]))
    return '<div class="sequence" aria-label="Message sequence">' + "".join(nodes) + "</div>"


def _asset_refs(file_path: str) -> tuple[str, str]:
    """Return browser-relative links to the PathManager-owned viewer assets."""
    output_dir = os.path.dirname(os.path.abspath(file_path))
    css = os.path.relpath(css_path("request.css"), start=output_dir)
    javascript = os.path.relpath(js_path("request.js"), start=output_dir)
    return css, javascript


def _tool_name(tool: Any, index: int) -> str:
    if not isinstance(tool, dict):
        return f"tool {index + 1}"
    function = tool.get("function") or tool
    return str(function.get("name") or tool.get("name") or f"tool {index + 1}")


def _render_tools(tools: list[Any]) -> str:
    if not tools:
        return '<div class="empty-state">No native tools were included in this request.</div>'
    cards = []
    for index, tool in enumerate(tools):
        cards.append(
            '<details class="schema-card">'
            f'<summary><span>{escape(_tool_name(tool, index))}</span>{_badge(f"#{index + 1}", "index")}</summary>'
            f'<pre class="json-view">{escape(_json(tool))}</pre>'
            "</details>"
        )
    return "\n".join(cards)


def _render_context_map(messages: list[Any], snapshot: dict[str, Any]) -> str:
    totals = {
        key: {"messages": 0, "chars": 0, "tokens": 0, "cached": 0}
        for key in _LAYER_LABELS
    }
    for index, message in enumerate(messages):
        data = message if isinstance(message, dict) else {"content": message}
        layer = _message_layer(data, index, len(messages))
        totals[layer]["messages"] += 1
        totals[layer]["chars"] += _content_size(data.get("content", ""))
        totals[layer]["tokens"] += _tokens(data)
        totals[layer]["cached"] += int(bool(data.get("cache")))

    cards = []
    for layer, label in _LAYER_LABELS.items():
        item = totals[layer]
        cards.append("".join([
            f'<article class="layer-card layer-{layer}">',
            f'<span class="layer-name">{escape(label)}</span>',
            f'<strong>{item["messages"]}</strong><span>messages</span>',
            f'<small>≈{item["tokens"]:,} tokens · {item["chars"]:,} chars · '
            f'{item["cached"]} cache boundaries</small>',
            "</article>",
        ]))

    details = [
        '<div class="layer-map">', *cards, "</div>",
        '<p class="notice cache-note"><strong>Cache reading:</strong> a cache boundary is '
        'the framework marker included in this canonical request. Provider-reported cache '
        'read/write token counts arrive with the response and remain in the trace usage event.</p>',
    ]
    if snapshot.get("parameters"):
        details.append(_render_json_block(
            "Effective parameters", snapshot["parameters"], open_=True,
        ))
    if snapshot.get("pressure"):
        details.append(_render_json_block(
            "Context pressure / pruning", snapshot["pressure"], open_=True,
        ))
    if snapshot.get("response_format"):
        details.append(_render_json_block("Response format", snapshot["response_format"]))
    return "\n".join(details)


def _tool_argument_values(messages: list[Any]) -> list[Any]:
    values = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            values.append(function.get("arguments", call.get("arguments", {})))
    return values


def _body_after_prefix_tokens(messages: list[Any]) -> int:
    """Estimate the mutable body after the latest task/checkpoint prefix."""
    boundary = -1
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        layer = _message_layer(message, index, len(messages))
        if layer in {"task", "checkpoint"}:
            boundary = index
    return _tokens(messages[boundary + 1:]) if boundary + 1 < len(messages) else 0


def _request_diagnostics(
    event: TraceEvent,
    snapshot: dict[str, Any],
    *,
    usage: Optional[dict[str, Any]] = None,
    previous_event: Optional[TraceEvent] = None,
) -> dict[str, Any]:
    messages = snapshot.get("messages") or []
    cache_indices = [
        index for index, message in enumerate(messages)
        if isinstance(message, dict) and message.get("cache")
    ]
    fixed_end = next((
        index for index in cache_indices
        if _message_layer(messages[index], index, len(messages)) == "task"
    ), 0 if messages else -1)
    rolling_end = cache_indices[-1] if cache_indices else fixed_end
    fixed_tokens = _tokens(messages[: fixed_end + 1]) if fixed_end >= 0 else 0
    rolling_tokens = _tokens(messages[: rolling_end + 1]) if rolling_end >= 0 else 0
    provider_states = [
        message.get("provider_state")
        for message in messages
        if isinstance(message, dict) and message.get("provider_state")
    ]
    arguments = _tool_argument_values(messages)
    body_tokens = _body_after_prefix_tokens(messages)

    usage = usage or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_tokens") or 0)
    cache_write = int(usage.get("cache_write_tokens") or 0)
    # Anthropic reports uncached input separately; OpenAI reports total input and a
    # cached subset. Normalize both conventions before presenting one hit ratio.
    provider = str(snapshot.get("provider") or "").lower()
    denominator = (
        input_tokens + cache_read + cache_write
        if "anthropic" in provider
        else max(input_tokens, cache_read + cache_write)
    )
    cache_hit = (cache_read / denominator) if denominator else None

    pressure = snapshot.get("pressure") or {}
    current = int(pressure.get("estimated_tokens_after") or 0)
    capacity = int(pressure.get("input_capacity_tokens") or 0)
    policy = pressure.get("compaction_policy") or {}
    recent_steps = sum(
        isinstance(message, dict)
        and message.get("role") == "assistant"
        and _message_layer(message, index, len(messages)) == "recent"
        for index, message in enumerate(messages)
    )

    slope = 0.0
    body_slope = 0.0
    if previous_event is not None:
        previous = previous_event.input or {}
        previous_pressure = previous.get("pressure") or {}
        previous_tokens = int(previous_pressure.get("estimated_tokens_after") or 0)
        step_delta = max(
            1, int(event.step_number or 0) - int(previous_event.step_number or 0)
        )
        slope = max(0.0, (current - previous_tokens) / step_delta)
        previous_body = _body_after_prefix_tokens(previous.get("messages") or [])
        body_slope = max(0.0, (body_tokens - previous_body) / step_delta)

    def projected_steps(value: int, target: int, growth: float) -> Optional[int]:
        if not target:
            return None
        if value >= target:
            return 0
        if growth <= 0:
            return None
        return max(1, math.ceil((target - value) / growth))

    compaction_candidates = []
    after_steps = int(policy.get("compact_after_steps") or 0)
    if after_steps:
        compaction_candidates.append(max(0, after_steps - recent_steps))
    body_eta = projected_steps(
        body_tokens,
        int(policy.get("compact_body_tokens") or 0),
        body_slope,
    )
    if body_eta is not None:
        compaction_candidates.append(body_eta)
    token_eta = projected_steps(
        current,
        int(policy.get("compact_at_tokens") or 0),
        slope,
    )
    if token_eta is not None:
        compaction_candidates.append(token_eta)

    return {
        "fixed_prefix_tokens": fixed_tokens,
        "rolling_prefix_tokens": rolling_tokens,
        "body_after_prefix_tokens": body_tokens,
        "provider_state_tokens": _tokens(provider_states) if provider_states else 0,
        "tool_argument_tokens": _tokens(arguments) if arguments else 0,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": usage.get("cost"),
        "cache_hit_ratio": cache_hit,
        "compaction_eta": min(compaction_candidates) if compaction_candidates else None,
        "capacity_eta": projected_steps(current, capacity, slope),
        "growth_tokens_per_step": slope,
        "body_growth_tokens_per_step": body_slope,
    }


def _eta(value: Optional[int]) -> str:
    if value is None:
        return "—"
    return "now" if value == 0 else f"≈{value} steps"


def _render_diagnostics(metrics: dict[str, Any]) -> str:
    hit = metrics["cache_hit_ratio"]
    hit_text = "pending" if hit is None else f"{hit:.1%}"
    cards = [
        ("Fixed prefix", f'≈{metrics["fixed_prefix_tokens"]:,}', "tokens"),
        ("Rolling prefix", f'≈{metrics["rolling_prefix_tokens"]:,}', "tokens cumulative"),
        ("Body after prefix", f'≈{metrics["body_after_prefix_tokens"]:,}', "tokens"),
        ("Provider state", f'≈{metrics["provider_state_tokens"]:,}', "tokens"),
        ("Tool arguments", f'≈{metrics["tool_argument_tokens"]:,}', "tokens"),
        ("Cache hit", hit_text, f'{metrics["cache_read_tokens"]:,} read tokens'),
        ("Provider input", f'{metrics["input_tokens"]:,}',
         f'{metrics["cache_write_tokens"]:,} cache-write tokens'),
        ("Output / cost", f'{metrics["output_tokens"]:,} tokens',
         "pending" if metrics["cost"] is None else f'${float(metrics["cost"]):.6f}'),
        ("Compaction", _eta(metrics["compaction_eta"]), "at current request"),
        ("Context capacity", _eta(metrics["capacity_eta"]), "at current growth"),
        ("Growth", f'≈{metrics["growth_tokens_per_step"]:,.0f}', "tokens / step"),
        ("Body growth", f'≈{metrics["body_growth_tokens_per_step"]:,.0f}', "tokens / step"),
    ]
    return '<div class="diagnostic-grid">' + "".join(
        '<article class="diagnostic-card">'
        f'<span>{escape(label)}</span><strong>{escape(value)}</strong>'
        f'<small>{escape(detail)}</small></article>'
        for label, value, detail in cards
    ) + "</div>"


def request_html_path(log_root: str, event: TraceEvent) -> str:
    """Return the unique output path for one model request event."""
    agent = _safe_component(event.agent_name, "agent")
    metadata = event.metadata or {}
    seq = f"{event.seq_no:06d}" if event.seq_no is not None else "unsequenced"
    step = int(event.step_number or 0)
    attempt = int(metadata.get("attempt") or 1)
    route = int(metadata.get("route_index") or 0)
    snapshot_id = str(metadata.get("request_snapshot_id") or "")
    short_id = _safe_component(snapshot_id.removeprefix("sha256:")[:10], "request")
    filename = f"{seq}-step-{step:04d}-attempt-{attempt:02d}-route-{route:02d}-{short_id}.html"
    return str(path_manager.under(
        log_root,
        P.LOG_MODEL_REQUEST,
        agent_name=agent,
        filename=filename,
    ))


def request_log_root(trace_root: str) -> str:
    """Resolve the owning session log root through PathManager when one is bound."""
    roots = path_manager.session_roots()
    if roots:
        return str(roots["log"])
    # Direct unit runners may initialize Trace without binding a Session. Their
    # explicit trace root remains the only available authority.
    return os.path.dirname(os.path.abspath(trace_root))


def render_request_html(
    event: TraceEvent,
    file_path: str,
    *,
    usage: Optional[dict[str, Any]] = None,
    previous_event: Optional[TraceEvent] = None,
) -> str:
    """Render a MODEL_REQUEST event into a safely escaped HTML page."""
    if event.event_type is not TraceEventType.MODEL_REQUEST:
        raise ValueError("request viewer only renders MODEL_REQUEST events")
    snapshot = event.input or {}
    messages = snapshot.get("messages") or []
    tools = snapshot.get("tools") or []
    metadata = event.metadata or {}
    cached = sum(bool(m.get("cache")) for m in messages if isinstance(m, dict))
    role_counts: dict[str, int] = {}
    for message in messages:
        role = str(message.get("role") if isinstance(message, dict) else "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
    content_chars = sum(
        _content_size(m.get("content", "")) for m in messages if isinstance(m, dict)
    )

    css, javascript = _asset_refs(file_path)
    title = f"{event.agent_name or 'agent'} · model request · step {event.step_number or 0}"
    timestamp = event.timestamp.isoformat()
    request_json = _json(snapshot)
    diagnostics = _request_diagnostics(
        event, snapshot, usage=usage, previous_event=previous_event,
    )

    route_rows = [
        ("Operation", (snapshot.get("parameters") or {}).get("operation") or "generate"),
        ("Requested", snapshot.get("requested_model") or "—"),
        ("Routed", snapshot.get("routed_model") or "—"),
        ("Provider", snapshot.get("provider") or "—"),
        ("Provider model", snapshot.get("provider_model") or "—"),
        ("Model API", snapshot.get("model_type") or "—"),
        ("Snapshot schema", snapshot.get("schema_version") or "—"),
        ("Attempt", metadata.get("attempt", 1)),
        ("Route index", metadata.get("route_index", 0)),
        ("Trace sequence", event.seq_no if event.seq_no is not None else "—"),
        ("Endpoint fingerprint", snapshot.get("endpoint_fingerprint") or "—"),
    ]
    route_table = "".join(
        f"<dt>{escape(str(key))}</dt><dd>{escape(str(value))}</dd>" for key, value in route_rows
    )
    message_html = "\n".join(
        _render_message(message, index, len(messages)) for index, message in enumerate(messages)
    ) or '<div class="empty-state">No messages were included in this request.</div>'
    role_options = "".join(
        f'<option value="{escape(role, quote=True)}">{escape(role.title())} ({count})</option>'
        for role, count in sorted(role_counts.items())
    )

    sections = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        f"  <title>{escape(title)}</title>",
        f'  <link rel="stylesheet" href="{escape(css, quote=True)}">',
        f'  <script defer src="{escape(javascript, quote=True)}"></script>',
        "</head>",
        "<body>",
        '<main class="request-shell">',
        '<header class="hero">',
        '<div class="eyebrow">Canonical model request</div>',
        f"<h1>{escape(event.agent_name or 'agent')}</h1>",
        f'<p class="subtitle">Captured immediately before provider dispatch · {escape(timestamp)}</p>',
        '<div class="metric-row">',
        f'<div class="metric"><strong>{len(messages)}</strong><span>messages</span></div>',
        f'<div class="metric"><strong>{len(tools)}</strong><span>native tools</span></div>',
        f'<div class="metric"><strong>{cached}</strong><span>cache boundaries</span></div>',
        f'<div class="metric"><strong>{content_chars:,}</strong><span>content chars</span></div>',
        "</div>",
        "</header>",
        '<section class="route-panel">',
        f'<dl class="route-grid">{route_table}</dl>',
        '<div class="snapshot-id">',
        '<span>Snapshot</span>',
        f'<code>{escape(str(snapshot.get("snapshot_id") or "—"))}</code>',
        "</div>",
        "</section>",
        '<nav class="tabs" aria-label="Request sections">',
        '<button class="tab-button active" data-tab-target="conversation">Conversation</button>',
        '<button class="tab-button" data-tab-target="context">Context map</button>',
        f'<button class="tab-button" data-tab-target="tools">Tools <span>{len(tools)}</span></button>',
        '<button class="tab-button" data-tab-target="request">Canonical JSON</button>',
        "</nav>",
        '<section class="tab-panel active" data-tab-panel="conversation">',
        '<div class="toolbar">',
        '<label class="search"><span>Search</span><input id="message-search" type="search" placeholder="role, layer, or content"></label>',
        f'<label class="role-filter"><span>Role</span><select id="role-filter"><option value="all">All roles ({len(messages)})</option>{role_options}</select></label>',
        '<label class="layer-filter"><span>Layer</span><select id="layer-filter"><option value="all">All layers</option><option value="stable">Stable prefix</option><option value="task">Task anchor</option><option value="checkpoint">Checkpoint</option><option value="recent">Recent turns</option><option value="live">Live context</option></select></label>',
        '<label class="cache-filter"><span>Cache</span><select id="cache-filter"><option value="all">All messages</option><option value="boundary">Cache boundary</option><option value="uncached">Uncached</option></select></label>',
        '<button id="toggle-wrap" class="quiet-button">No wrap</button>',
        '<button id="collapse-details" class="quiet-button">Collapse details</button>',
        "</div>",
        '<div class="legend"><span><i class="stable"></i>stable prefix / task anchor</span><span><i class="rolling"></i>recent assistant + tool turns</span><span><i class="live"></i>live context</span></div>',
        _render_sequence(messages),
        f'<div id="message-list" class="message-list">{message_html}</div>',
        '<div id="no-results" class="empty-state hidden">No messages match this filter.</div>',
        "</section>",
        '<section class="tab-panel" data-tab-panel="context">',
        '<div class="section-heading"><div><span class="eyebrow">Prompt architecture</span><h2>Context layers</h2></div><p>Stable content stays at the front; rolling turns and the live step remain at the tail.</p></div>',
        _render_context_map(messages, snapshot),
        '<div class="section-heading"><div><span class="eyebrow">Request diagnostics</span><h2>Tokens, cache, and growth</h2></div><p>Token counts are conservative estimates unless they come from provider usage.</p></div>',
        _render_diagnostics(diagnostics),
        "</section>",
        '<section class="tab-panel" data-tab-panel="tools">',
        '<div class="section-heading"><div><span class="eyebrow">Action space</span><h2>Native tool schemas</h2></div><p>These schemas are part of the request and therefore part of the model cache key.</p></div>',
        _render_tools(tools),
        "</section>",
        '<section class="tab-panel" data-tab-panel="request">',
        '<div class="section-heading"><div><span class="eyebrow">Source of truth</span><h2>Provider-neutral request snapshot</h2></div><button id="copy-json" class="quiet-button">Copy JSON</button></div>',
        '<p class="notice">This is the framework-level request captured before provider-specific field serialization. It is escaped for safe local viewing and is never parsed back into the agent context.</p>',
        f'<pre id="canonical-json" class="json-view canonical-json">{escape(request_json)}</pre>',
        "</section>",
    ]
    sections += ["</main>", "</body>", "</html>"]
    return "\n".join(sections)


def write_request_html(
    event: TraceEvent,
    log_root: str,
    *,
    usage: Optional[dict[str, Any]] = None,
    previous_event: Optional[TraceEvent] = None,
) -> str:
    """Atomically write one request page and return its path."""
    file_path = request_html_path(log_root, event)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    temporary = f"{file_path}.{os.getpid()}.tmp"
    html = render_request_html(
        event, file_path, usage=usage, previous_event=previous_event,
    )
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(html)
        os.replace(temporary, file_path)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass
    return file_path


def schedule_request_html(
    event: TraceEvent,
    log_root: Optional[str],
    *,
    usage: Optional[dict[str, Any]] = None,
    previous_event: Optional[TraceEvent] = None,
) -> None:
    """Render off the provider hot path; log and contain all observational failures."""
    if not log_root or event.event_type is not TraceEventType.MODEL_REQUEST:
        return
    # Deep-copy now: the trace event is retained and fanned out after emit.
    captured = event.model_copy(deep=True)
    previous = previous_event.model_copy(deep=True) if previous_event else None
    captured_usage = dict(usage or {})
    file_path = request_html_path(str(log_root), captured)
    predecessor = _LATEST_TASKS.get(file_path)

    async def _write_in_order() -> str:
        # A POST_STEP usage refresh may be scheduled before the initial background
        # render finishes. Serialize writes per page so the richer refresh wins.
        if predecessor is not None:
            try:
                await predecessor
            except Exception:  # noqa: BLE001 - the refresh can repair a failed first write
                pass
        return await asyncio.to_thread(
            write_request_html,
            captured,
            str(log_root),
            usage=captured_usage,
            previous_event=previous,
        )

    task = asyncio.create_task(
        _write_in_order(),
        name=f"request-html-{event.seq_no if event.seq_no is not None else 'pending'}",
    )
    _BACKGROUND_TASKS.add(task)
    _LATEST_TASKS[file_path] = task

    def _done(finished: asyncio.Task) -> None:
        _BACKGROUND_TASKS.discard(finished)
        if _LATEST_TASKS.get(file_path) is finished:
            _LATEST_TASKS.pop(file_path, None)
        if finished.cancelled():
            return
        error = finished.exception()
        if error is not None:
            logger.debug(f"| Model request HTML was not written: {error}")

    task.add_done_callback(_done)


async def flush_request_html(timeout: float = 5.0) -> bool:
    """Wait briefly for pages already scheduled, without blocking model dispatch."""
    pending = tuple(_BACKGROUND_TASKS)
    if not pending:
        return True
    _, remaining = await asyncio.wait(pending, timeout=timeout)
    if remaining:
        logger.warning(f"| {len(remaining)} model request HTML page(s) still pending at shutdown")
    return not remaining


__all__ = [
    "render_request_html",
    "flush_request_html",
    "request_html_path",
    "request_log_root",
    "schedule_request_html",
    "write_request_html",
]
