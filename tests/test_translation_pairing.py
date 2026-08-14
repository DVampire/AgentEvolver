"""A translated document and its source stay the same document.

`README.md` and `README_zh.md` are one page in two languages, and nothing has ever
compared them. So they drift the only way they can: someone adds a section, a command, or
a link to the English side, and the Chinese side keeps describing the version before it.
Nobody notices, because the Chinese reader has no second copy to compare against — they
just follow instructions for software that no longer works that way. `scripts/INSTALL.md`
is the worse case of the two: it is followed literally, by someone who has not installed
the project yet.

The criterion is structure, not text. A translation must change every word, so any
comparison of content is either vacuous or fails on every correct translation. What a
translation must NOT change is the shape: the same sections in the same order and nesting,
the same code blocks in the same languages, and the same files linked from the same
places. Those are the parts that carry the instructions, and a change to any of them on
one side only is exactly the drift that happens. "Both files exist" would have passed on
every version of these files ever written, including the ones that disagreed.

Pairs are discovered from the filesystem, so a translation added later is checked by this
file as it stands. Both `X_zh.md` (what this repository uses) and `X.zh.md` count.
"""

from pathlib import Path
from typing import Dict, List, Tuple
import re

import pytest

from tests.test_doc_links import ROOT, SKIP_DIRS, documents, prose


#: Both spellings, so the convention can change without the check going quiet.
SUFFIXES = ("_zh.md", ".zh.md")


# --------------------------------------------------------------------------- #
# Finding the pairs
# --------------------------------------------------------------------------- #
def _source_of(translation: Path) -> Path:
    """The document a translation is a translation of."""
    for suffix in SUFFIXES:
        if translation.name.endswith(suffix):
            return translation.with_name(translation.name[: -len(suffix)] + ".md")
    raise ValueError(f"{translation} is not a translation")


def translations() -> List[Path]:
    """Every translated document in the repository, found by walking it.

    Discovered rather than listed: a list would have to be edited by the same person who
    forgot to update the translation, at the same moment.
    """
    return [path for path in documents()
            if any(path.name.endswith(suffix) for suffix in SUFFIXES)]


PAIRS = [(_source_of(path), path) for path in translations()]


# --------------------------------------------------------------------------- #
# What structure means
# --------------------------------------------------------------------------- #
def headings(text: str) -> List[int]:
    """The nesting depth of each heading, in order.

    Depths rather than titles, because the titles are translated. `[1, 2, 2, 3]` says the
    document has one top heading, two sections, and a subsection in the second — and that
    is the claim a translation has to keep.
    """
    return [len(match.group(1))
            for match in re.finditer(r"^(#{1,6})\s+\S", prose(text), re.M)]


def fence_languages(text: str) -> List[str]:
    """The info string of each fenced code block, in order.

    A code block is an instruction to type something. Its language is not translated, so a
    `bash` block appearing on one side only means one language's reader is being told to
    run a command the other is not.
    """
    languages, in_fence = [], False
    for line in re.sub(r"<!--.*?-->", "", text, flags=re.S).splitlines():
        match = re.match(r"^\s*(?:`{3,}|~{3,})(.*)$", line)
        if not match:
            continue
        if not in_fence:
            languages.append(match.group(1).strip())
        in_fence = not in_fence
    return languages


def _counterpart_neutral(target: str) -> str:
    """A link target with the language marker removed.

    The two sides link to different files on purpose: the English README points at
    `scripts/INSTALL.md` and the Chinese one at `scripts/INSTALL_zh.md`. Comparing the raw
    targets would report that difference as drift on every pair, forever. Comparing them
    with the marker stripped asks the question that matters — do both sides send the
    reader to the same place.
    """
    for suffix in SUFFIXES:
        if target.endswith(suffix):
            return target[: -len(suffix)] + ".md"
    return target


def link_targets(path: Path) -> List[str]:
    """Local link targets in one document, in order, language-neutral.

    In-page anchors are dropped: a Chinese heading has a Chinese anchor, and requiring
    those to match would require the headings not to be translated.
    """
    targets = []
    for match in re.finditer(r"\]\(\s*<?([^)>\s]+)",
                             prose(path.read_text(encoding="utf-8"))):
        target = match.group(1)
        if target.startswith(("#", "//")) or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
            continue
        targets.append(_counterpart_neutral(target))
    return targets


def structure(path: Path) -> Dict[str, object]:
    """Everything a translation must preserve, as one comparable record."""
    text = path.read_text(encoding="utf-8")
    return {"headings": headings(text),
            "code blocks": fence_languages(text),
            "links": link_targets(path)}


def divergences(source: Path, translation: Path) -> List[str]:
    """Where the pair stops being the same document."""
    left, right = structure(source), structure(translation)
    out = []
    for key in left:
        if left[key] != right[key]:
            out.append(f"{key} differ: {source.name} has {left[key]!r}, "
                       f"{translation.name} has {right[key]!r}")
    return out


IDS = [str(source.relative_to(ROOT)) for source, _ in PAIRS]


# --------------------------------------------------------------------------- #
# The scan
# --------------------------------------------------------------------------- #
def test_the_walk_found_the_translated_documents():
    """With no pairs discovered, every case below is skipped and the file reports green.

    That is the state this check exists to make impossible: silence that looks like
    agreement. The two pairs are named because losing one of them — a README renamed, a
    translation deleted — is itself a change worth a red result.
    """
    assert IDS, "no translated documents found; the suffixes or the walk are wrong"
    assert "README.md" in IDS, "README.md has no translation in the scan"
    assert "scripts/INSTALL.md" in IDS, "scripts/INSTALL.md has no translation in the scan"


def test_the_walk_covers_the_directories_documents_live_in():
    """Guards the scan, not the pairs: `documents()` is shared with the link check.

    If its skip list ever grew to cover `scripts/` or the repository root, this file would
    quietly stop checking anything while still passing.
    """
    assert "scripts" not in SKIP_DIRS and "docs" not in SKIP_DIRS
    assert len(documents()) > 100


# --------------------------------------------------------------------------- #
# The invariant
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source,translation", PAIRS, ids=IDS)
def test_both_halves_of_a_pair_are_present(source: Path, translation: Path):
    """A translation whose source was renamed is a page nothing links to any more.

    It does not disappear — it keeps serving its readers the old content, from a URL that
    still works, with no sign that the document it translated has moved on.
    """
    assert source.exists(), (
        f"{translation.relative_to(ROOT)} translates {source.name}, which does not "
        f"exist; either the source was renamed or the translation was orphaned")


@pytest.mark.parametrize("source,translation", PAIRS, ids=IDS)
def test_a_translation_keeps_the_structure_of_its_source(source: Path, translation: Path):
    """Sections, code blocks and links must line up; the words must not.

    The tempting check is a heading *count*, which passes when a section is added on one
    side and another is deleted on the other, and passes when a `##` becomes a `###`.
    Comparing the ordered depths costs the same and catches both.
    """
    if not source.exists():
        pytest.skip("reported by the presence check")
    problems = divergences(source, translation)
    assert not problems, "\n  ".join(
        [f"{source.name} and {translation.name} are no longer the same document:"]
        + problems)


@pytest.mark.parametrize("source,translation", PAIRS, ids=IDS)
def test_each_side_links_to_the_other(source: Path, translation: Path):
    """The switcher is how a reader gets to their language, and it is easy to omit.

    Without it the translation is reachable only by guessing the filename. This is checked
    on the raw targets rather than the neutralised ones, because neutralising is exactly
    what would make a link to the wrong side look correct.
    """
    if not source.exists():
        pytest.skip("reported by the presence check")

    def raw(path: Path) -> str:
        return prose(path.read_text(encoding="utf-8"))

    assert f"({translation.name})" in raw(source), (
        f"{source.name} never links to {translation.name}; a reader who needs the "
        f"translation cannot find it")
    assert f"({source.name})" in raw(translation), (
        f"{translation.name} never links back to {source.name}")


# --------------------------------------------------------------------------- #
# The check itself
# --------------------------------------------------------------------------- #
def _write_pair(tmp_path: Path, left: str, right: str) -> Tuple[Path, Path]:
    (tmp_path / "doc.md").write_text(left, encoding="utf-8")
    (tmp_path / "doc_zh.md").write_text(right, encoding="utf-8")
    return tmp_path / "doc.md", tmp_path / "doc_zh.md"


def test_a_faithful_translation_passes(tmp_path: Path):
    """The half that decides whether the check is usable.

    A structural check that fires on ordinary translation work gets switched off within a
    week. Different words, different anchors, translated comments inside a code block: all
    of that is a correct translation and must produce nothing.
    """
    source, translation = _write_pair(
        tmp_path,
        "# Title\n\nSee [guide](guide.md).\n\n## Install\n\n```bash\nrun  # do it\n```\n",
        "# 标题\n\n见 [指南](guide.md)。\n\n## 安装\n\n```bash\nrun  # 执行\n```\n")
    assert divergences(source, translation) == []


@pytest.mark.parametrize("mutation,expected", [
    ("# Title\n\nSee [guide](guide.md).\n\n## Install\n\n```bash\nrun\n```\n\n## Extra\n",
     "headings"),
    ("# Title\n\nSee [guide](guide.md).\n\n## Install\n\n```bash\nrun\n```\n"
     "\n```python\nx\n```\n", "code blocks"),
    ("# Title\n\nSee [guide](other.md).\n\n## Install\n\n```bash\nrun\n```\n", "links"),
])
def test_drift_on_one_side_is_detected(tmp_path: Path, mutation: str, expected: str):
    """Each kind of drift, reintroduced, must go red.

    These are the three shapes real drift takes — a section added, a command added, a link
    repointed — and each one is invisible to the check that would otherwise be written
    here ("both files exist"). A check that cannot fail reports the pair as consistent,
    which is worse than not checking.
    """
    source, translation = _write_pair(
        tmp_path, mutation,
        "# 标题\n\n见 [指南](guide.md)。\n\n## 安装\n\n```bash\nrun\n```\n")
    problems = divergences(source, translation)
    assert any(problem.startswith(expected) for problem in problems), (
        f"drift in {expected} was not reported; got {problems}")
