from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ResponseType(str, Enum):
    TOOL = "tool"
    AGENT = "agent"
    SKILL = "skill"
    CONNECTOR = "connector"
    ENVIRONMENT = "environment"
    WORKFLOW = "workflow"
    LLM = "llm"
    CONSTRAINT = "constraint"
    PROMPT = "prompt"
    COMMAND = "command"


class Response(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: ResponseType
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    files: Optional[List[str]] = None
    parsed_model: Optional[BaseModel] = None
    usage: Optional[Any] = None          # TokenUsage at runtime; Any to avoid circular import
    extra: Optional[Dict[str, Any]] = None  # extension metadata, including archive/model-view hints


__all__ = ["Response", "ResponseType"]
