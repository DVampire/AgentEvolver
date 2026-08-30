from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticUndefined
from agentevolver.dynamic import dynamic_manager
from agentevolver.session import BaseContext
from agentevolver.response.types import Response


#: Cap on how much of one command's output reaches the agent, in characters.
#: ~8k tokens: generous for reading a build log or a diff, and small enough that a
#: single result cannot dominate a turn.
OUTPUT_LIMIT = 32_000

#: Split of that budget between the start and the end of the output. The head carries what
#: the command set out to do, the tail carries how it ended — the error, the summary line,
#: the exit status. The middle of an oversized dump is the least informative part.
_HEAD_SHARE = 0.75


def clip_output(text: str, limit: int = OUTPUT_LIMIT) -> str:
    """Bound one command's output, keeping its beginning and its end.

    Unbounded output is not a cosmetic problem. A single `strings` call against a 31KB
    binary returned 14,419,441 characters; that one result was handed to the agent whole
    and then stored in its memory, so every subsequent turn sent a prompt of about 4.3
    million tokens against a 1,048,576-token limit. The run died of consecutive 400s
    having produced nothing since, and the reported cause named neither the command nor
    the size. The agent could not have read 14MB either — the output was useless at that
    length to everyone involved.

    What is dropped is stated in place, so the agent knows it is looking at an excerpt and
    can narrow the command rather than wonder why the text stops.

    This builds the excerpt and nothing else. Whether the dropped middle is still
    reachable is the caller's business: the tool pipeline saves the full text to the
    spill store before calling this and appends the locator, so in normal operation
    the middle is one `read_file_tool` away rather than gone.
    """
    if len(text) <= limit:
        return text
    head = int(limit * _HEAD_SHARE)
    tail = limit - head
    dropped = len(text) - limit
    return (
        f"{text[:head]}\n"
        f"\n[... {dropped:,} characters elided of {len(text):,} total. This is an excerpt: "
        f"the beginning and the end. Narrow the command — grep, head, a smaller range — if "
        f"you need what is in between ...]\n\n"
        f"{text[-tail:]}"
    )


class ToolContext(BaseContext):
    """Context passed into tool manager and individual tool instances."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    #: The **session** this call belongs to, not the call. `BaseContext.from_context`
    #: carries the caller's id through unchanged, so every tool call in one run sees the
    #: same value — which is what `spill`, permission and provenance want, since they
    #: scope by run. What identifies one *call* is `ToolExecution.call_id`, minted by the
    #: execution pipeline; nothing in this context is unique per call.
    id: str = Field(default="", description="The session this call belongs to.")
    name: str = Field(default="", description="Name of the tool being called.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload passed to the tool.")
    #: Ambient run state — workspace and log roots, the sandbox handle, allowlists,
    #: lineage a conversion would otherwise drop. Inherited: `_inherited_ambient` seeds a
    #: sub-agent from it, so anything belonging to *one call* does not go here. That is
    #: what `tool_manager.__call__`'s `execution_context` is for.
    extra: Dict[str, Any] = Field(default_factory=dict, description="Ambient run state, inherited by sub-agents.")


class Tool(BaseModel):
    """Base class for all tools that can be exposed through function calling."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="The name of the tool")
    description: str = Field(description="The description of the tool")
    #: What a caller needs beyond the call schema: when to reach for this, what it
    #: does that one line of description cannot say, what goes wrong. The schema
    #: states the arguments; this states everything the arguments do not, which is
    #: why it is the one part a prompt carries for every resident tool.
    guidance: str = Field(default="", description="When and how to use this tool; what the schema cannot say")

    #: Complete example calls, one per entry. Read before a first call and not worth
    #: carrying afterwards, so they reach a model at ``full`` — through
    #: ``inspect_tool`` — rather than in every step's prompt.
    examples: List[str] = Field(default_factory=list, description="Complete example calls")

    #: The authored instruction as one markdown blob, in the older four-section form
    #: (Function / Guidance / Parameters / Example).
    #:
    #: Superseded by the fields above, and kept for what still produces one: a tool
    #: written before the split, and a tool an optimizer generates at runtime. A
    #: config with neither ``guidance`` nor ``examples`` is read out of this, so
    #: nothing has to be migrated to keep working — but ``## Function`` restates the
    #: description and ``## Parameters`` restates the schema, and a new tool that
    #: writes them writes them twice.
    instruction: str = Field(default="", description="Legacy single-blob instruction; prefer guidance + examples")
    metadata: Optional[Dict[str, Any]] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Permission mode: read_only / workspace_write / danger_full_access")
    #: Whether calling this tool changes state, as opposed to reporting on it.
    #:
    #: This exists to make a distinction the agent cannot otherwise see about itself:
    #: measuring always succeeds and never breaks anything, while changing something can
    #: fail — so an agent that is unsure keeps measuring, and its own history reads as
    #: activity. One run spent 65 turns on a one-line fix having already located the line:
    #: 134 shell commands, 14 file reads, 4 edits. It could see every output; it could not
    #: see the ratio.
    #:
    #: None means "depends on the arguments" — a shell command may do either — and the
    #: effect is judged by whether observable state actually changed.
    mutates: Optional[bool] = Field(
        default=None,
        description="True if the tool changes state, False if it only reports on it, None if it depends.",
    )
    #: Budget for one call of this tool, in seconds; ``None`` takes the manager default.
    #:
    #: Declared here rather than passed by the caller, because the tool is what knows
    #: what its work costs: a file read that has not returned in ten seconds is stuck,
    #: while a build legitimately runs for twenty minutes. One shared default has to be
    #: generous enough for the build, which means the stuck read holds the agent for
    #: half an hour before it learns anything.
    #:
    #: This is the budget for the *call*. A tool that also bounds something inside
    #: itself — `bash_tool.timeout` bounds the child process — keeps that separate;
    #: the inner bound should be the smaller of the two, so the tool returns its own
    #: diagnostic instead of being cut off mid-report.
    call_timeout_seconds: Optional[float] = Field(
        default=None,
        description="Budget for one call of this tool, in seconds. None uses the manager default.",
    )

    async def __call__(self, **kwargs) -> Response:
        """Call the tool with the given arguments."""
        raise NotImplementedError("All tools must implement __call__")

    def permission_request(
        self, arguments: Dict[str, Any], ctx: Optional[ToolContext] = None,
    ) -> Any:
        """Describe the concrete operation for the central permission guard.

        Return a ``permission.PermissionRequest`` when the arguments map to a generic
        read/write/bash operation, or ``None`` when this tool has no such mapping. The
        manager evaluates the request before entering ``__call__``. Implementations may
        temporarily retain an internal check as defense in depth for callers that invoke
        a Tool instance directly instead of using ``tool_manager``.

        This is intentionally a method of the Tool rather than a name-based table in the
        executor: only the implementation knows whether an argument is a path, command,
        content payload, or a harmless identifier.
        """
        return None

    def will_mutate(self, arguments: Dict[str, Any]) -> Optional[bool]:
        """Predict effects from one concrete call; argument-dependent tools override."""
        return self.mutates

class ToolConfig(BaseModel):
    """Tool configuration"""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    
    name: str = Field(description="The name of the tool")
    description: str = Field(description="The description of the tool")
    guidance: str = Field(default="", description="When and how to use this tool; what the schema cannot say")
    examples: List[str] = Field(default_factory=list, description="Complete example calls")
    instruction: str = Field(default="", description="Legacy single-blob instruction; prefer guidance + examples")
    metadata: Optional[Dict[str, Any]] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Permission mode: read_only / workspace_write / danger_full_access")
    mutates: Optional[bool] = Field(
        default=None, description="True if the tool changes state, False if it only reports on it"
    )
    call_timeout_seconds: Optional[float] = Field(
        default=None, description="Budget for one call of this tool, in seconds; None uses the manager default"
    )
    version: str = Field(default="1.0.0", description="Version of the tool")

    cls: Optional[Type[Tool]] = Field(default=None, description="The class of the tool")
    config: Optional[Dict[str, Any]] = Field(default={}, description="The initialization configuration of the tool")
    instance: Optional[Tool] = Field(default=None, description="The instance of the tool")
    code: Optional[str] = Field(default=None, description="Source code for dynamically generated tool classes (used when cls cannot be imported from a module)")
    path: Optional[str] = Field(default=None, description="Absolute path to the tool's source file")
    
    # Default representations
    function_calling: Optional[Dict[str, Any]] = Field(default=None, description="Default function calling representation")
    text: Optional[str] = Field(default=None, description="Default text representation")
    args_schema: Optional[Type[BaseModel]] = Field(default=None, description="Default args schema (BaseModel type)")

    @model_validator(mode="after")
    def _fill_instruction_from_cls(self) -> "ToolConfig":
        """Backfill the authored documentation from the tool class.

        Every ToolConfig construction site passes `cls`, so the tool's own
        `guidance` / `examples` / `instruction` come along without being threaded
        through each call. All three, not just the blob: a tool that has been split
        into fields would otherwise arrive here with nothing to render, which is
        exactly the silent-empty failure the split was meant to avoid.
        """
        if self.cls is None:
            return self
        try:
            fields = self.cls.model_fields
        except Exception:  # noqa: BLE001 — a class that is not a model has nothing to give
            return self
        for name in ("instruction", "guidance", "examples"):
            if getattr(self, name, None):
                continue
            field = fields.get(name)
            if field is None:
                continue
            # A field declared with `default_factory` reports `PydanticUndefined` as
            # its default, which is truthy — assigning it would put the sentinel on
            # the config and blow up wherever the value is used.
            default = field.default
            if default is not PydanticUndefined and default:
                setattr(self, name, default)
        return self

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """Dump the model to a dictionary, recursively serializing nested Pydantic models."""
        
        result = {
            "name": self.name,
            "description": self.description,
            "guidance": self.guidance,
            "examples": list(self.examples),
            "instruction": self.instruction,
            "metadata": self.metadata,
            "enable_evolving": self.enable_evolving,
            "permission_mode": self.permission_mode,
            "mutates": self.mutates,
            "call_timeout_seconds": self.call_timeout_seconds,
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
    def model_validate(cls, data: Dict[str, Any]) -> 'ToolConfig':
        """Validate the model from a dictionary."""
        name = data.get("name")
        description = data.get("description")
        instruction = data.get("instruction", "")
        metadata = data.get("metadata")
        enable_evolving = data.get("enable_evolving", False)
        permission_mode = data.get("permission_mode", "workspace_write")
        mutates = data.get("mutates")
        call_timeout_seconds = data.get("call_timeout_seconds")
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
                        base_class=Tool,
                        context="tool"
                    )
                except Exception as e:
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
            instruction=instruction,
            metadata=metadata,
            enable_evolving=enable_evolving,
            permission_mode=permission_mode,
            mutates=mutates,
            call_timeout_seconds=call_timeout_seconds,
            version=version,
            cls=cls_,
            config=config,
            instance=instance,
            code=code,
            function_calling=function_calling,
            text=text,
            args_schema=args_schema,
        )

__all__ = [
    "Tool",
    "ToolConfig",
    "ToolContext",
]
