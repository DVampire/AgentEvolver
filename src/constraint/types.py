"""Constraint types — Constraint base class, ConstraintConfig and ConstraintContext."""

from __future__ import annotations

from typing import Any, Dict, Optional, Type
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.dynamic import dynamic_manager
from src.session import BaseContext
from src.response.types import Response, ResponseType


class ConstraintContext(BaseContext):
    """Context passed into constraint manager and individual constraint instances."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(default="", description="Task ID — matches the task_id used throughout the agent loop.")
    name: str = Field(default="", description="Name of the constraint being checked.")
    work_dir: Optional[str] = Field(default=None, description="Working directory available to the caller.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload for this constraint check.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this constraint context.")


class Constraint(BaseModel):
    """Base class for all constraints.

    Subclass and override ``__call__(input, ctx)`` to implement constraint logic.
    Return ``Response(success=False, ...)`` when violated, ``success=True`` when passing.

    Per-task state lives in ``_state[task_id]`` — a plain dict lazily initialized
    on first use inside ``__call__``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="Unique name for this constraint.")
    description: str = Field(default="", description="The description of the constraint.")
    metadata: Optional[Dict[str, Any]] = Field(default={}, description="The metadata of the constraint.")
    require_grad: bool = Field(default=False, description="Whether the constraint requires gradients.")
    enabled: bool = Field(default=True, description="Set to False to temporarily disable without removing.")

    _state: Dict[str, Dict[str, Any]] = PrivateAttr(default_factory=dict)

    def _cleanup(self, task_id: str) -> None:
        self._state.pop(task_id, None)

    async def __call__(self, input: Dict[str, Any], ctx: ConstraintContext) -> Response:
        return Response(type=ResponseType.CONSTRAINT, success=True, message="")


class ConstraintConfig(BaseModel):
    """Constraint configuration"""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="The name of the constraint")
    description: str = Field(default="", description="The description of the constraint")
    metadata: Optional[Dict[str, Any]] = Field(default={}, description="The metadata of the constraint")
    require_grad: bool = Field(default=False, description="Whether the constraint requires gradients")
    enabled: bool = Field(default=True, description="Whether the constraint is enabled")
    version: str = Field(default="1.0.0", description="Version of the constraint")

    cls: Optional[Type[Constraint]] = Field(default=None, description="The class of the constraint")
    config: Optional[Dict[str, Any]] = Field(default={}, description="The initialization configuration of the constraint")
    instance: Optional[Constraint] = Field(default=None, description="The instance of the constraint")
    code: Optional[str] = Field(default=None, description="Source code for dynamically generated constraint classes (used when cls cannot be imported from a module)")
    path: Optional[str] = Field(default=None, description="Absolute path to the constraint's source file")

    # Default representations
    function_calling: Optional[Dict[str, Any]] = Field(default=None, description="Default function calling representation")
    text: Optional[str] = Field(default=None, description="Default text representation")
    args_schema: Optional[Type[BaseModel]] = Field(default=None, description="Default args schema (BaseModel type)")

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """Dump the model to a dictionary, recursively serializing nested Pydantic models."""

        result = {
            "name": self.name,
            "description": self.description,
            "metadata": self.metadata,
            "require_grad": self.require_grad,
            "enabled": self.enabled,
            "version": self.version,

            "cls": dynamic_manager.get_class_string(self.cls) if self.cls else None,
            "config": self.config,
            "instance": None,
            "code": self.code,
            "path": self.path,

            "function_calling": self.function_calling,
            "text": self.text,
            "args_schema": dynamic_manager.serialize_args_schema(self.args_schema) if self.args_schema else None,
        }

        return result

    @classmethod
    def model_validate(cls, data: Dict[str, Any]) -> 'ConstraintConfig':
        """Validate the model from a dictionary."""
        name = data.get("name")
        description = data.get("description", "")
        metadata = data.get("metadata")
        require_grad = data.get("require_grad", False)
        enabled = data.get("enabled", True)
        version = data.get("version")

        cls_ = None
        code = data.get("code")
        if code:
            class_name = dynamic_manager.extract_class_name_from_code(code)
            if class_name:
                try:
                    cls_ = dynamic_manager.load_class(
                        code,
                        class_name=class_name,
                        base_class=Constraint,
                        context="constraint"
                    )
                except Exception:
                    cls_ = None
            else:
                cls_ = None
        else:
            cls_ = None

        config = data.get("config")
        instance = data.get("instance", None)

        function_calling = data.get("function_calling")
        text = data.get("text")
        args_schema = dynamic_manager.deserialize_args_schema(data.get("args_schema"))

        return cls(name=name,
            description=description,
            metadata=metadata,
            require_grad=require_grad,
            enabled=enabled,
            version=version,
            cls=cls_,
            config=config,
            instance=instance,
            code=code,
            function_calling=function_calling,
            text=text,
            args_schema=args_schema,
        )


__all__ = ["ConstraintContext", "Constraint", "ConstraintConfig"]
