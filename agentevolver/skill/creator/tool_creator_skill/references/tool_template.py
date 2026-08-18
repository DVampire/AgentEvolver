"""TEMPLATE — a tool (a callable an agent invokes with a JSON args object).

Copy to `extension/tool/{name}.py`, rename the class, fill the three doc constants and
`__call__`.

A tool's documentation is its fields, and they are split by who needs what and when:
  - `_DESCRIPTION` — one line. The card's subtitle, and the call schema's `description`.
  - `_GUIDANCE` — what the call schema cannot say: when to reach for this, what to
    watch for, when NOT to. Carried in the prompt for every resident tool, every step.
  - `_EXAMPLES` — complete calls. Reached through `inspect_capability_tool`
    (capability_type="tool"), because an example is worth reading before a first call
    and not worth carrying afterwards.

**The parameters are not a field.** They are derived from `__call__`'s signature and its
Google-style `Args:` docstring, and travel in the request's own `tools` array — which is
how the model calls anything at all. Writing them out again as prose is a third spelling
of one contract, and the prose is the copy that goes stale. Document each argument in
`Args:` instead.

`__call__` must return a `Response` — return `success=False` on expected failures rather
than raising, and do heavyweight imports inside `__call__` to avoid cycles.
"""

from typing import Any, Dict, List
from pydantic import Field
from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType
from agentevolver.registry import TOOL

_DESCRIPTION = "One line: what the tool does."

_GUIDANCE = """
When and how to use it; caveats; when NOT to use it. Everything the arguments do not
already say — the schema states what to pass, this states whether to reach for it.
"""

_EXAMPLES = [
    '{"name": "my_tool", "args": {"arg_name": "value"}}',
]


@TOOL.register_module(force=True)
class MyTool(Tool):
    """One-line purpose."""

    name: str = "my_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    # enable_evolving=True marks the tool as evolvable (the optimize agent may edit it).
    enable_evolving: bool = Field(default=True, description="Whether the tool may be evolved (self-optimized)")

    def __init__(self, enable_evolving: bool = True, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, arg_name: str, **kwargs) -> Response:
        """Do the work.

        This docstring is not decoration: the ``Args:`` block below is parsed into the
        call schema the model is sent, so each line here becomes that argument's
        description. An argument with no line here reaches the model unexplained.

        Accept `ctx` via **kwargs if you need the current session. Prefer giving
        optional args sensible defaults so a missing arg fails gracefully rather
        than raising a TypeError.

        Args:
            arg_name: What this argument is, and what a good value looks like.
        """
        try:
            # --- implementation ---
            result = f"processed: {arg_name}"
            return Response(
                type=ResponseType.TOOL,
                success=True,
                message=result,
                data={"arg_name": arg_name, "result": result},
            )
        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=str(e))
