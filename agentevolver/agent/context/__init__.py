"""Agent context: the registry, and the window a request fills.

Both meanings of the word live here, in separate files. ``manager.py`` is the agent
registry — the module convention every manager in this tree follows, so
``from agentevolver.agent.context import AgentContextManager`` still resolves. Everything
else is model-facing context: what the model sees, in what order, and what it costs.

| Module            | Owns                                                            |
|-------------------|-----------------------------------------------------------------|
| `layers.py`       | the four layers and per-layer token accounting                  |
| `envelope.py`     | the validated fixed → checkpoint → recent → live envelope       |
| `conversation.py` | the held history of one run                                     |
| `assembler.py`    | layout, cache breakpoints and folding, from a held conversation |
| `builder.py`      | the same envelope, projected from a persisted trace             |
| `sanitize.py`     | stripping author-only template comments                         |
| `project.py`      | CLAUDE.md / MEMORY.md / AGENTS.md in the fixed layer            |
| `errors.py`       | the protocol error every module above may raise                 |
| `manager.py`      | the agent registry, lifecycle and version history               |
| `capabilities.py` | which capabilities this request offers, as native tool schemas  |

Both builders produce the same :class:`ContextEnvelope`, so cache placement, pressure
accounting and provider serialisation are shared no matter where the history came from.
"""

from agentevolver.agent.context.assembler import ContextAssembler, context_assembler
from agentevolver.agent.context.builder import ContextBuilder, context_builder
from agentevolver.agent.context.capabilities import assemble_native_tools
from agentevolver.agent.context.conversation import Conversation
from agentevolver.agent.context.envelope import ContextEnvelope
from agentevolver.agent.context.errors import ContextProtocolError
from agentevolver.agent.context.layers import LAYERS, ContextMessages
from agentevolver.agent.context.project import (
    MAX_PROJECT_CONTEXT_CHARS,
    PROJECT_CONTEXT_FILES,
    load_project_context,
)
from agentevolver.agent.context.sanitize import strip_rendered_comments


def __getattr__(name: str):
    """Hand out the registry on demand.

    ``manager`` needs the agent contracts, the contracts module names the loop, and the
    loop reads this package's assembler — so importing the registry eagerly here closes
    a cycle. Resolving it on first access keeps
    ``from agentevolver.agent.context import AgentContextManager`` working while leaving
    the window modules importable on their own.
    """
    if name == "AgentContextManager":
        from agentevolver.agent.context.manager import AgentContextManager

        return AgentContextManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "LAYERS",
    "AgentContextManager",
    "MAX_PROJECT_CONTEXT_CHARS",
    "PROJECT_CONTEXT_FILES",
    "ContextAssembler",
    "ContextBuilder",
    "ContextEnvelope",
    "ContextMessages",
    "ContextProtocolError",
    "assemble_native_tools",
    "Conversation",
    "context_assembler",
    "context_builder",
    "load_project_context",
    "strip_rendered_comments",
]
