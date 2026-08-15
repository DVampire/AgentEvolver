"""Wire models shared by stdio and WebSocket Gateway transports.

These are the authority for the protocol in every language. `gateway/typescript.py`
renders them into `frontend/src/protocol/gateway.ts`, which the browser and the CLI both
import; nothing declares these shapes by hand a second time. Field descriptions travel
with the render, so the reason a field exists is written once, here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


PROTOCOL_VERSION = 1


class GatewayCommand(BaseModel):
    """A request sent by an interactive client."""

    model_config = ConfigDict(extra="forbid")

    id: str
    method: str
    params: Dict[str, Any] = Field(default_factory=dict)
    protocol_version: int = PROTOCOL_VERSION


class GatewayError(BaseModel):
    """Why a command was refused."""

    code: str
    message: str
    details: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Where a refusal says what was wrong, as opposed to that something was. A "
            "client that reads only `message` can report the failure but never explain it."
        ),
    )


class GatewayResponse(BaseModel):
    """The direct result of a GatewayCommand."""

    kind: Literal["response"] = "response"
    id: str
    ok: bool
    result: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[GatewayError] = None
    protocol_version: int = Field(
        default=PROTOCOL_VERSION,
        description=(
            "Echoed on every response, so a client can tell a server it no longer "
            "understands from one that merely refused this command."
        ),
    )


class GatewayEvent(BaseModel):
    """An ordered, replayable server event."""

    kind: Literal["event"] = "event"
    type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None
    conversation_id: Optional[str] = Field(
        default=None,
        description=(
            "Which line of dialogue this belongs to. A project holds several and the "
            "Gateway broadcasts every event to every client, so without this a client "
            "cannot tell its own conversation's work from the one in the next tab. Absent "
            "on project-wide events, such as a session opening or capabilities changing."
        ),
    )
    task_id: Optional[str] = None
    seq_no: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    protocol_version: int = PROTOCOL_VERSION


def error_response(command_id: str, code: str, message: str, **details: Any) -> GatewayResponse:
    return GatewayResponse(
        id=command_id,
        ok=False,
        error=GatewayError(code=code, message=message, details=details or None),
    )
