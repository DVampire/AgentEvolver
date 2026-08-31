"""Translate paired AgentEvolver Trace events into OpenTelemetry spans.

Trace remains the durable source of truth. This bridge is deliberately best-effort and
non-authoritative: an unavailable collector or optional SDK can never change execution.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict

from agentevolver.logger import logger
from agentevolver.trace.types import TraceEvent, TraceEventType

_START_TO_END = {
    TraceEventType.AGENT_START: TraceEventType.AGENT_END,
    TraceEventType.TOOL_START: TraceEventType.TOOL_CALL,
    TraceEventType.SKILL_START: TraceEventType.SKILL_CALL,
    TraceEventType.WORKFLOW_START: TraceEventType.WORKFLOW_END,
    TraceEventType.MODEL_REQUEST: TraceEventType.AGENT_CALL,
}
_END_TO_START = {end: start for start, end in _START_TO_END.items()}


def _nanoseconds(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _primitive(value: Any) -> Any:
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, (str, bool, int, float)) for item in value
    ):
        return list(value)
    return str(value)


class OpenTelemetryTraceBridge:
    """Optional span exporter whose lifecycle follows :class:`TraceManager`."""

    def __init__(self, *, service_name: str = "agentevolver", endpoint: str = "") -> None:
        self.service_name = str(service_name or "agentevolver")
        self.endpoint = str(endpoint or "")
        self._provider: Any = None
        self._tracer: Any = None
        self._status: Any = None
        self._status_code: Any = None
        self._open: Dict[tuple[str, ...], Any] = {}

    def start(self) -> bool:
        """Initialize the optional SDK. Missing packages disable export cleanly."""
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.trace import Status, StatusCode
        except ImportError:
            logger.warning(
                "| ⚠️ OpenTelemetry export requested but the observability extra is not "
                "installed (pip install 'agentevolver[observability]')."
            )
            return False
        try:
            provider = TracerProvider(resource=Resource.create({
                "service.name": self.service_name,
            }))
            exporter_kwargs = {"endpoint": self.endpoint} if self.endpoint else {}
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(**exporter_kwargs))
            )
            self._provider = provider
            self._tracer = provider.get_tracer("agentevolver.trace")
            self._status, self._status_code = Status, StatusCode
            # Do not replace a provider another embedder already installed globally.
            # Spans created through our private provider still export correctly.
            if trace.get_tracer_provider().__class__.__name__ == "ProxyTracerProvider":
                trace.set_tracer_provider(provider)
            logger.info(
                f"| 📡 OpenTelemetry export enabled for {self.service_name} "
                f"({self.endpoint or os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'SDK default')})"
            )
            return True
        except Exception as error:
            self._provider = self._tracer = None
            logger.warning(f"| ⚠️ OpenTelemetry initialization failed: {error}")
            return False

    @staticmethod
    def _key(event: TraceEvent, start_type: TraceEventType) -> tuple[str, ...]:
        metadata = event.metadata or {}
        call_id = str(metadata.get("call_id") or "")
        if start_type is TraceEventType.MODEL_REQUEST:
            # The request carries a snapshot id while the corresponding AGENT_CALL uses
            # the step number. One model decision is admitted per Agent step, so the
            # step is the stable cross-event join key.
            call_id = str(event.step_number if event.step_number is not None else "")
        if not call_id:
            call_id = ":".join((
                str(event.step_number if event.step_number is not None else ""),
                str(event.action_index if event.action_index is not None else ""),
                event.action_name or event.agent_name or "",
            ))
        return (
            event.session_id or "no_session",
            event.task_id or "",
            start_type.value,
            call_id,
        )

    @staticmethod
    def _attributes(event: TraceEvent) -> Dict[str, Any]:
        values: Dict[str, Any] = {
            "agentevolver.event.type": event.event_type.value,
            "agentevolver.session.id": event.session_id or "",
            "agentevolver.task.id": event.task_id or "",
            "agentevolver.agent.name": event.agent_name or "",
            "agentevolver.action.type": event.action_type or "",
            "agentevolver.action.name": event.action_name or "",
            "agentevolver.seq_no": int(event.seq_no or 0),
        }
        if event.duration_ms is not None:
            values["agentevolver.duration_ms"] = float(event.duration_ms)
        for key, value in (event.metadata or {}).items():
            if key in {"call_id", "frame_key", "invocation_key", "program_hash"}:
                values[f"agentevolver.{key}"] = _primitive(value)
        return values

    def submit(self, event: TraceEvent) -> None:
        """Start/end one span synchronously; SDK export itself is batch/background."""
        if self._tracer is None:
            return
        try:
            if event.event_type in _START_TO_END:
                key = self._key(event, event.event_type)
                span = self._tracer.start_span(
                    event.action_name or event.agent_name or event.event_type.value,
                    start_time=_nanoseconds(event.timestamp),
                    attributes=self._attributes(event),
                )
                replaced = self._open.pop(key, None)
                if replaced is not None:
                    replaced.end(end_time=_nanoseconds(event.timestamp))
                self._open[key] = span
                return
            start_type = _END_TO_START.get(event.event_type)
            if start_type is None:
                return
            key = self._key(event, start_type)
            span = self._open.pop(key, None)
            if span is None:
                span = self._tracer.start_span(
                    event.action_name or event.agent_name or event.event_type.value,
                    start_time=_nanoseconds(event.timestamp),
                )
            for name, value in self._attributes(event).items():
                span.set_attribute(name, value)
            if event.success is False or event.error:
                span.set_status(self._status(self._status_code.ERROR, event.error or "failed"))
                if event.error:
                    span.record_exception(RuntimeError(event.error))
            else:
                span.set_status(self._status(self._status_code.OK))
            span.end(end_time=_nanoseconds(event.timestamp))
        except Exception as error:  # observability never changes the run
            logger.warning(f"| ⚠️ OpenTelemetry span export failed: {error}")

    def close(self) -> None:
        """End abandoned spans and boundedly flush the batch processor."""
        if self._provider is None:
            return
        for span in self._open.values():
            try:
                span.set_status(self._status(self._status_code.ERROR, "process stopped"))
                span.end()
            except Exception:
                pass
        self._open.clear()
        try:
            self._provider.force_flush(timeout_millis=5_000)
            self._provider.shutdown()
        except Exception as error:
            logger.warning(f"| ⚠️ OpenTelemetry shutdown failed: {error}")
        finally:
            self._provider = self._tracer = None


__all__ = ["OpenTelemetryTraceBridge"]
