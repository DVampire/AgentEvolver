"""How a capability presents itself in a roster, at each level of detail.

Four capability types are backed by a document — a skill by SKILL.md, a connector
by CONNECTOR.md, a plugin by PLUGIN.md, an environment by ENVIRONMENT.md — and all
four answer ``get_instruction`` with the same shape: what this is, where its
document is, and at ``full`` the document itself. That shape was written out four
times, once per manager, which is four places for it to drift and four places to
edit when a fifth document-backed type arrives.

The levels are the two that have callers:

``brief``
    What a prompt carries for every resident capability, every step. Enough to
    choose one, and where to look for the rest.

``full``
    What ``inspect_capability_tool`` returns for the one capability an agent has
    stopped to ask about — the document.

A ``tool`` is not here: it has no document, so its own fields *are* its
documentation and ``tool_manager`` composes them itself.
"""

from __future__ import annotations

from typing import Sequence

from agentevolver.utils.string_utils import render_capability_card

#: The levels a roster is rendered at, in order.
INSTRUCTION_LEVELS = ("brief", "full")


def roster_card(
    name: str,
    description: str = "",
    *,
    meta: str = "",
    manifest_label: str = "",
    manifest_path: str = "",
    manifest_from: str = "brief",
    notes: Sequence[str] = (),
    document: str = "",
    footer: str = "",
    level: str = "brief",
) -> str:
    """One document-backed capability's card.

    Args:
        name: The capability's registered name — the card's heading.
        description: Its one-line description, printed as the subtitle.
        meta: A short tag beside the description (version, transport, type).
        manifest_label: The document's filename, e.g. ``SKILL.md``.
        manifest_path: Absolute path to it.
        manifest_from: The level from which the path is named. ``brief`` (the
            default) names it always; ``full`` withholds it, which is what a skill
            does — invoking a skill returns its paths anyway, so a roster that
            repeated them would charge every step for what one call hands over.
        notes: Lines to place above the document — a plugin's own usage notes.
        document: The document body, included only at ``full``.
        footer: Lines to place after the document, also only at ``full``. An
            environment names its actions' qualified call names here, because the
            prose above says ``run`` while the schema says ``remote_host__run``.
        level: One of :data:`INSTRUCTION_LEVELS`.

    Returns:
        The rendered card. Callers join them with a blank line between.
    """
    body: list[str] = []
    if manifest_path and manifest_label and (manifest_from == "brief" or level == "full"):
        body.append(f"- **{manifest_label}**: {manifest_path}")
    body += [line for line in notes if line]
    if level == "full":
        if (document or "").strip():
            body += ["", document.strip()]
        if (footer or "").strip():
            body += ["", footer.strip()]
    return render_capability_card(
        name=name,
        description=description or "",
        meta=meta,
        body="\n".join(body),
    )


def roster(cards: Sequence[str]) -> str:
    """Join rendered cards into one roster, dropping the ones that came out empty."""
    return "\n\n".join(card for card in cards if card.strip())


__all__ = ["INSTRUCTION_LEVELS", "roster", "roster_card"]
