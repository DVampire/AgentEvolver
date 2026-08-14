"""The spill contract: what a store is asked to save, and what it hands back."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SpillSource(BaseModel):
    """Which call produced the text — for a readable filename and for inspection.

    Descriptive only. Nothing here is consulted for access control; a store that
    started treating ``tool_name`` as a permission would be reading intent into a
    field whose only job is to make a directory listing legible to a human.
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(description="Tool whose result was spilled, e.g. 'bash'.")
    call_id: str = Field(default="", description="Identifier of the call the result belongs to.")
    label: str = Field(default="result", description="Short human label for the artifact.")


class SpillRef(BaseModel):
    """A saved artifact: where it went, how big it was, and how to read it back."""

    model_config = ConfigDict(extra="forbid")

    locator: str = Field(
        description=(
            "Opaque model-facing handle. The local store renders a filesystem path; a "
            "remote store may render a URI or key. Consumers print it next to "
            "``retrieval_hint`` and never parse it."
        )
    )
    chars: int = Field(description="Exact length of the saved text, in characters.")
    retrieval_hint: str = Field(
        description="Store-supplied sentence telling the agent how to get the full text back."
    )


class SpillStore(BaseModel):
    """Persist a tool result that is too large to show, and return a way back to it.

    One operation, and a deliberately narrow one. The store owns storage and
    nothing else: no retention policy, no decision about when a result is too
    big, no search API. Those belong to the caller, which is why
    :meth:`save_text` neither inspects the size it is given nor rewrites the
    result it came from.

    ``save_text`` **raises** on a real storage failure (permissions, disk full,
    backend down). The caller decides how to degrade — the tool pipeline treats a
    failure as best-effort and keeps the inline excerpt, because turning a
    successful command into an error because its *transcript* could not be filed
    would be a worse outcome than losing the transcript.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="Name of this store implementation.")

    async def save_text(
        self,
        content: str,
        source: SpillSource,
        *,
        session_key: Optional[str] = None,
        suggested_name: str = "output.txt",
    ) -> SpillRef:
        """Persist ``content`` verbatim and return its reference.

        Args:
            content: The full text to save. Stores write it whole — truncating
                here would defeat the entire point of the mechanism.
            source: Which tool and call produced it.
            session_key: Groups artifacts from one session. Stores derive a
                subdirectory from it rather than trusting it as a path.
            suggested_name: A base name hint, e.g. ``bash.txt``. A store sanitises
                it to a single safe path segment; it is never treated as a path.

        Returns:
            The saved artifact's :class:`SpillRef`.

        Raises:
            Exception: On a genuine storage failure.
        """
        raise NotImplementedError("All spill stores must implement save_text")


__all__ = ["SpillRef", "SpillSource", "SpillStore"]
