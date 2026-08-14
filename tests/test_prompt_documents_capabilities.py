"""A block the model can receive must be one the prompt has defined.

`<capability-context-changes>` is synthesized at runtime and appended after the history,
so it appears in no template. Nothing described it: a reader of the HTML could not tell
the block existed, and the model met an unannounced tag mid-conversation and had to guess
whether it replaced the catalog or amended it.

The description belongs in the *system* message, ahead of the cache breakpoint — it is
fixed for the session, so putting it there costs one write and nothing per step.
"""

import re
from pathlib import Path

import pytest

from agentevolver.prompt.types import parse_prompt_file

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = sorted((ROOT / "agentevolver" / "prompt" / "default").glob("*.html")) + \
            sorted((ROOT / "extension" / "prompt").glob("*.html"))


def _inline(text: str, base: Path) -> str:
    """Resolve `<module src>` includes.

    Shared rules live in modules, so a template can document something without the word
    appearing in its own file. Checking the raw text alone reports four false failures.
    """
    def sub(match):
        path = (base / match.group(1)).resolve()
        return path.read_text(encoding="utf-8") if path.exists() else ""
    return re.sub(r'<module src="([^"]+)"></module>', sub, text)


def _with_capabilities():
    for path in TEMPLATES:
        config = parse_prompt_file(str(path))
        if "capability-context" in (config.user_template or ""):
            yield path, config


def test_there_is_something_to_check():
    """A scan that matches nothing passes vacuously."""
    assert list(_with_capabilities()), "no template carries a capability catalog"


@pytest.mark.parametrize("path", [p for p, _ in _with_capabilities()], ids=lambda p: p.name)
def test_every_catalog_template_explains_the_changes_block(path):
    config = parse_prompt_file(str(path))
    system = _inline(config.system_template or "", path.parent)
    assert ("capability-context-changes" in system
            or "capability_context_changes" in system), (
        f"{path.name} can receive a <capability-context-changes> block but never says so")


@pytest.mark.parametrize("path", [p for p, _ in _with_capabilities()], ids=lambda p: p.name)
def test_the_changes_block_is_described_as_an_amendment(path):
    """"Supersedes what it names" and "replaces the catalog" are opposite instructions.

    The block lists only what moved, so read as a replacement it would strip the model of
    every capability the delta happens not to mention.
    """
    config = parse_prompt_file(str(path))
    system = _inline(config.system_template or "", path.parent).lower()
    assert any(word in system for word in ("amendment", "supersedes", "does not mention")), (
        f"{path.name} names the block but does not say it amends rather than replaces")


@pytest.mark.parametrize("path", [p for p, _ in _with_capabilities()], ids=lambda p: p.name)
def test_the_explanation_sits_before_the_cache_breakpoint(path):
    """In the system message, not beside the catalog.

    The breakpoint is at `</capability-context>` in the user turn. Text placed after it
    is re-read at full price on every step, and this text never changes.
    """
    config = parse_prompt_file(str(path))
    user = config.user_template or ""
    tail = user[user.index("</capability-context>"):]
    assert "capability-context-changes" not in tail, (
        f"{path.name} explains the block after the breakpoint, where it is not cacheable")
