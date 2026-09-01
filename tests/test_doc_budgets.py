"""A standing document that grows without limit is a document that stops being read.

Some documents here are read once, on purpose, by someone looking for one thing — a
decision record, a postmortem. Their length is whatever the reasoning needs and budgeting
them would be wrong.

Others are read constantly and by everyone: the READMEs, the standing orders, the testing
convention. Those have a different failure mode. Nobody ever decides to make one
unreadable; it happens one justified paragraph at a time, and the moment it crosses from
"reference" to "wall" passes without an event. This file makes that moment an event.

The ceiling is a guardrail, not a reduction target. When it goes red the first move is to
**relocate** — the content usually belongs in a tier that owns it, per
[DOC-STANDARD.md](../docs/DOC-STANDARD.md) — the second is to **condense**, and only the
third is to raise the number. A ceiling too low to hold what the document must say is a bug
in the ceiling, and raising it is the fix.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Standing documents and their word ceilings.
#:
#: Only documents that are read repeatedly appear here. Decision records, postmortems, and
#: module READMEs are deliberately absent: the first two are as long as their reasoning, and
#: the third is bounded by the module rather than by prose discipline.
#:
#: Generated files are absent for the same reason — `docs/tool-catalog.md` is as long as the
#: tool registry, and `tests/test_tool_catalog.py` already gates it.
#:
#: Each ceiling sits above the document's current size with room to work. Raising one is a
#: normal change; it needs a sentence in the commit saying what the words bought.
#:
#: Ceilings are per-document rather than per-tier, and a translation's number is not its
#: source's. `word_count` scores Chinese by character and English by word, and one English
#: word is worth roughly one and a half to two characters — so the same document scores
#: about 1.7× in Chinese while saying exactly the same thing. Setting one number for both
#: would either exempt the English or condemn the Chinese. Normalizing by a fudge factor
#: was the alternative and it is worse: the factor would be invented, and every future
#: reader would have to trust a constant nobody could justify.
BUDGETS: dict[str, int] = {
    "README.md": 3_500,
    "README_zh.md": 5_800,  # ≈ the English ceiling in characters; see above
    "docs/DOC-STANDARD.md": 1_000,
    "tests/README.md": 950,
    "docs/canvas.md": 700,
    "docs/capability-schemas.md": 300,
    "docs/workflows.md": 550,
}

#: One CJK ideograph, kana, or Hangul syllable. Matched individually because these scripts
#: do not separate words with spaces.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")

_FENCE = re.compile(r"^```")


def word_count(text: str) -> int:
    """Words in prose, counting CJK by character and everything else by whitespace run.

    Splitting on whitespace alone is the obvious implementation and it silently exempts
    every Chinese document in the repository: `README_zh.md` is roughly the same length as
    its English sibling and scores a quarter of the words, because Chinese does not put
    spaces between them. A budget that cannot see a document is not a budget.

    Fenced code blocks do not count. A ceiling that punished a worked example would push
    examples out of the documents that need them most, which is the opposite of the point.
    """
    prose: list[str] = []
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            prose.append(line)
    body = "\n".join(prose)
    cjk = len(_CJK.findall(body))
    latin = len([token for token in _CJK.sub(" ", body).split() if any(c.isalnum() for c in token)])
    return cjk + latin


# --------------------------------------------------------------------------- #
# The budgets themselves
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("relative,ceiling", sorted(BUDGETS.items()))
def test_a_standing_document_stays_under_its_ceiling(relative: str, ceiling: int):
    """The gate. When it fails: relocate, then condense, then raise the number."""
    path = ROOT / relative
    count = word_count(path.read_text(encoding="utf-8"))

    assert count <= ceiling, (
        f"{relative} is {count} words, over its {ceiling}-word ceiling by {count - ceiling}. "
        f"Move content to the tier that owns it (docs/DOC-STANDARD.md), condense it, or "
        f"raise the ceiling in tests/test_doc_budgets.py and say in the commit what the "
        f"extra words bought."
    )


@pytest.mark.parametrize("relative", sorted(BUDGETS))
def test_every_budgeted_document_exists(relative: str):
    """A budget for a deleted file silently passes forever.

    Worse, it hides a real regression: recreate that path later and it is born budgeted at
    a number nobody chose for it.
    """
    assert (ROOT / relative).is_file(), (
        f"{relative} is budgeted but missing — delete its entry or restore the file"
    )


def test_the_standing_documents_are_all_budgeted():
    """A new top-level document should have to state its ceiling.

    `docs/` also holds tiers that are deliberately unbudgeted, so this checks only the flat
    files directly under it plus the repository-root READMEs — the ones everybody reads.
    """
    standing = {"README.md", "README_zh.md"}
    standing |= {f"docs/{path.name}" for path in (ROOT / "docs").glob("*.md")}

    generated = {"docs/tool-catalog.md"}  # gated by test_tool_catalog.py instead
    missing = sorted(standing - set(BUDGETS) - generated)

    assert not missing, (
        f"standing documents with no ceiling: {missing}. Add each to BUDGETS, or to the "
        f"`generated` set if a generator owns its length."
    )


# --------------------------------------------------------------------------- #
# The counter
# --------------------------------------------------------------------------- #
def test_chinese_prose_is_counted_by_character():
    """The reason this file does not just call `str.split()`.

    Whitespace splitting scores an entire Chinese paragraph as one word, which would exempt
    every translated document in the repository from its own ceiling.
    """
    assert word_count("这是一句没有空格的中文。") == 11  # 11 CJK chars, punctuation ignored
    assert word_count("hello world") == 2


def test_a_mixed_line_counts_both_scripts():
    """Our documents are mixed constantly — a Chinese sentence naming an English symbol."""
    assert word_count("门禁 gate 通过") == 5  # 门禁(2) + gate(1) + 通过(2)


def test_code_blocks_do_not_count_against_the_ceiling():
    """A budget that taxed examples would push them out of the documents that need them."""
    with_code = "prose here\n```python\nx = 1\ny = 2\n```\nmore prose\n"

    assert word_count(with_code) == 4  # "prose here" + "more prose"


def test_punctuation_alone_is_not_a_word():
    """Otherwise a table of separators would outweigh the sentence above it."""
    assert word_count("--- | --- | ---") == 0
