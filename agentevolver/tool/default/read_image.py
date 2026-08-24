"""ReadImageTool — put an image file in front of the model, or refuse and say why."""

import os
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.attachment import AttachmentError, attachment_manager
from agentevolver.config import config
from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.sandbox.project import check_session_path
from agentevolver.tool.types import Tool

_DESCRIPTION = "Look at an image file. Requires a model that accepts image input."

_GUIDANCE = """
Look at an image file — a screenshot, a diagram, a plot, a photograph.

- The image is attached to the conversation and stays visible for the rest of the run, so read it once.
- PNG, JPEG, GIF and WebP only, up to 5 MB. The format is read from the file's bytes, not its extension.
- Only works when the current model accepts image input. If it does not, the call is refused and reading the file some other way will not help — say so rather than retrying.
- Use read_file_tool for text; this tool does not describe the image in words, it shows it to you.
"""

_EXAMPLES = [
    '{"name": "read_image_tool", "args": {"path": "/abs/path/to/screenshot.png"}}',
]


def _routed_model(ctx: Any) -> Optional[str]:
    """Which model the turn this tool call belongs to is being sent to.

    The agent records it on the context before building the turn, so the answer is the
    route the tool result will actually travel on rather than whatever the config says is
    the default. Falling back to the configured role matters for callers that reach a tool
    outside an agent turn — a workflow tool step, a direct call — where there is no turn to
    have recorded anything.
    """
    from agentevolver.model import model_manager

    named = (getattr(ctx, "extra", {}) or {}).get("model_name")
    return named or model_manager.resolve_role(None)


@TOOL.register_module(force=True)
class ReadImageTool(Tool):
    """Attach an image to the conversation, refusing when the route cannot carry one."""

    name: str = "read_image_tool"
    # Declared, not inherited: this tool reads a file and adds to the conversation, and
    # `mutates is False` is what lets plan mode admit it. The base `workspace_write`
    # default would be a claim on the workspace it never makes.
    permission_mode: str = "read_only"
    #: local I/O plus a base64 encode of at most 5 MB; a minute means a wedged mount.
    call_timeout_seconds: float = 60
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={"canvas_category": "files"})
    enable_evolving: bool = Field(default=False)
    mutates: bool = False

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    def permission_request(self, arguments, ctx=None):
        return PermissionRequest(
            op=Operation.READ, target=str(arguments.get("path") or "")
        )

    async def __call__(self, path: str, **kwargs) -> Response:
        """Attach the image at ``path`` to the conversation.

        Args:
            path: Absolute path to a PNG, JPEG, GIF or WebP file.
        """
        from agentevolver.model import model_manager

        ctx = kwargs.get("ctx")
        try:
            denial = check_session_path(ctx, path, write=False)
            if denial:
                return Response(type=ResponseType.TOOL, success=False, message=denial)

            # The route check runs before the file is touched. Sending an image to a
            # model with no image input spends the call and fails inside the provider,
            # with an error naming neither this tool nor the file — and by then the
            # image is already in the turn, so every retry on that route fails the same
            # way. Refusing first costs one cheap tool result instead.
            model_name = _routed_model(ctx)
            model = model_manager.get_model_config(model_name) if model_name else None
            if model is None:
                return Response(
                    type=ResponseType.TOOL, success=False,
                    message=(
                        f"Cannot read {path} as an image: the model for this turn "
                        f"({model_name or 'unknown'}) is not a registered model, so whether "
                        f"it accepts images is unknown."
                    ),
                )
            if not model.supports_vision:
                return Response(
                    type=ResponseType.TOOL, success=False,
                    message=(
                        f"Cannot read {path} as an image: model '{model.model_name}' does not "
                        f"accept image input. Nothing about the file will change that — "
                        f"switch to an image-capable model, or work from a description."
                    ),
                )

            if not os.path.exists(path):
                return Response(type=ResponseType.TOOL, success=False, message=f"Error: File not found: {path}")
            if not os.path.isfile(path):
                return Response(type=ResponseType.TOOL, success=False, message=f"Error: Path is not a file: {path}")

            result = permission_manager.check(
                self.name,
                PermissionRequest(op=Operation.READ, target=path),
            )
            if not result.allowed:
                return Response(type=ResponseType.TOOL, success=False, message=f"Permission denied: {result.reason}")

            try:
                attachment = attachment_manager.save_image(path, session_key=str(getattr(ctx, "id", "") or ""))
            except AttachmentError as error:
                return Response(type=ResponseType.TOOL, success=False, message=f"Error: {error}")

            warning_prefix = f"Warning: {result.warning}\n\n" if result.warning else ""
            return Response(
                type=ResponseType.TOOL,
                success=True,
                # The tool result is text — that is all a tool result can be here. It says
                # the image is coming; the image itself rides the next request, attached by
                # the agent from the live set. Saying "attached below" would be a lie about
                # ordering the model would then reason from.
                message=(
                    f"{warning_prefix}Attached {attachment.media_type} image from {path} "
                    f"({attachment.byte_count:,} bytes). It is visible from the next message onward."
                ),
                files=[path],
                data={
                    "attachment_id": attachment.attachment_id,
                    "media_type": attachment.media_type,
                    "bytes": attachment.byte_count,
                    "locator": attachment.locator,
                },
            )

        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=f"Error reading image: {e}")
