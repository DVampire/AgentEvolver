"""Python shown in a document still parses.

A code block is the part of a document a reader trusts most and the part nothing checks.
Prose that goes stale reads oddly and gets fixed; an example that goes stale gets copied,
pasted, and run. The failure is silent on the documentation side and loud somewhere else,
which is the worst arrangement available.

This parses every ````python` block in the repository's own documentation. Parsing is the
whole check — it catches the drift that actually happens, which is a renamed keyword
argument or a signature that grew a parameter, and it costs nothing. It cannot catch an
example that parses and no longer means anything; that is what the surrounding prose and
review are for.

Blocks that are deliberately not whole programs — a signature on its own, a fragment with
an elided body — opt out by starting with ``# fragment``. Making that explicit is the point:
without it, the honest response to a red gate is to relabel the block as ``text``, and the
repository quietly loses the checking on every example at once.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Directories whose Markdown is not ours to keep parsing: a vendored reference checkout,
#: generated trees, and dependency installs.
SKIP_DIRS = {
    "others",
    "node_modules",
    "output",
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
}

#: A block a reader is not meant to run as-is. Marked at the top of the block, in the block,
#: so the reason travels with the code rather than living in a list somewhere else.
FRAGMENT_MARKER = "# fragment"

_BLOCK = re.compile(
    r"^```(?P<lang>[a-zA-Z0-9_+-]*)\s*$(?P<body>.*?)^```\s*$", re.DOTALL | re.MULTILINE
)

#: Languages whose blocks this file parses. `py` and `python3` are the same request.
PYTHON_LANGUAGES = {"python", "py", "python3"}


def _documents() -> list[Path]:
    return [
        path
        for path in sorted(ROOT.rglob("*.md"))
        if not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts)
    ]


def _python_blocks(text: str):
    """Every Python block in one document, as ``(line_number, source)``."""
    for match in _BLOCK.finditer(text):
        if match.group("lang").lower() not in PYTHON_LANGUAGES:
            continue
        body = match.group("body")
        line = text[: match.start()].count("\n") + 1
        yield line, body


#: Group blocks by document.  A separate pytest item for every fenced block made
#: collection dominate this otherwise cheap check (large skill references contain many
#: snippets).  Grouping preserves every parse and every source line in the failure while
#: paying pytest's parametrisation overhead only once per document.
DOCUMENT_BLOCKS = [
    (path, blocks)
    for path in _documents()
    if (blocks := list(_python_blocks(path.read_text(encoding="utf-8"))))
]
BLOCK_COUNT = sum(len(blocks) for _, blocks in DOCUMENT_BLOCKS)
BLOCK_BATCH_SIZE = 25
BLOCK_BATCHES = [
    DOCUMENT_BLOCKS[index : index + BLOCK_BATCH_SIZE]
    for index in range(0, len(DOCUMENT_BLOCKS), BLOCK_BATCH_SIZE)
]


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def _is_fragment(body: str) -> bool:
    """Whether a block opted out, by carrying the marker on its first non-blank line.

    First line only, deliberately: a `# fragment` further down would exempt a block whose
    opening lines a reader has already taken as runnable.
    """
    for line in body.splitlines():
        if line.strip():
            return line.strip().startswith(FRAGMENT_MARKER)
    return False


@pytest.mark.parametrize(
    "documents",
    BLOCK_BATCHES,
    ids=lambda batch: f"{batch[0][0].relative_to(ROOT)}+{len(batch) - 1}",
)
def test_python_blocks_in_documents_parse(documents: list[tuple[Path, list[tuple[int, str]]]]):
    """Every Python block is valid, retaining document and line in a batched failure."""
    failures = []
    for path, blocks in documents:
        for line, body in blocks:
            if _is_fragment(body):
                continue
            try:
                ast.parse(body)
            except SyntaxError as error:
                failures.append(
                    f"{path.relative_to(ROOT)}:{line} — {error.msg} (block line {error.lineno})"
                )

    assert not failures, (
        "Python blocks that do not parse:\n  "
        + "\n  ".join(failures)
        + f"\nFix each example, or open a deliberate fragment with `{FRAGMENT_MARKER}`."
    )


def test_the_repository_actually_has_python_blocks_to_check():
    """A gate over an empty set passes forever and proves nothing.

    If the block-matching regex ever stops matching — a change in fence style, a stricter
    language tag — every example in the repository silently stops being checked, and the
    suite stays green. This is the check that notices.
    """
    assert BLOCK_COUNT >= 5, (
        f"only {BLOCK_COUNT} Python blocks found across the documentation — the fence "
        f"pattern probably stopped matching"
    )


# --------------------------------------------------------------------------- #
# The extractor
# --------------------------------------------------------------------------- #
def test_a_block_in_another_language_is_not_parsed_as_python():
    """Most fences here are shell or JSON, and neither is valid Python."""
    text = "```sh\npytest --cov\n```\n"

    assert list(_python_blocks(text)) == []


def test_the_reported_line_points_at_the_fence():
    """A failure message is only useful if it names where to look."""
    text = "intro\n\n```python\nx = 1\n```\n"

    ((line, _),) = _python_blocks(text)

    assert line == 3


@pytest.mark.parametrize("tag", ["python", "py", "python3"])
def test_every_spelling_of_python_is_checked(tag: str):
    """`py` and `python3` are the same request, and an unchecked spelling is an escape
    hatch nobody intended to create."""
    assert list(_python_blocks(f"```{tag}\nx = 1\n```\n"))


def test_the_fragment_marker_only_counts_on_the_first_line():
    """Otherwise a comment deep in a block exempts the lines above it, which a reader has
    already read as runnable code."""
    assert _is_fragment("# fragment\ndef f(...):\n")
    assert _is_fragment("\n  # fragment — signature only\nx\n")
    assert not _is_fragment("x = 1\n# fragment\n")
