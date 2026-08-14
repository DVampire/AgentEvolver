"""Every file a document points at is a file that exists.

A moved module takes its README's neighbours with it, a renamed skill breaks the four
pages that referenced it, and nothing anywhere reports it. The reader finds out; the test
suite never does. The repository has 400-odd Markdown files that link to each other, to
images under `docs/assets/`, and to source files — one rename touches more of them than
anybody checks by hand.

This is the cheap half of link rot and the half that is decidable: does the target exist.
Anchors within a page are not checked. Resolving `#some-heading` means reimplementing
GitHub's slug algorithm, and a check that is wrong about slugs produces failures nobody
can act on, which is how a check gets deleted.

The subjects are discovered by walking the tree, so a document added tomorrow is covered
without anyone remembering to add it here.
"""

from pathlib import Path
from typing import Iterator, List, Tuple
import re
import urllib.parse

import pytest


ROOT = Path(__file__).resolve().parents[1]

#: `others/` is a vendored reference implementation with its own docs and its own broken
#: links; `node_modules` and `output` are generated. Checking them would report defects
#: nobody in this repository can fix, which trains people to ignore the check.
SKIP_DIRS = {"others", "node_modules", "output", ".git", ".venv", "venv",
             "__pycache__", "site-packages", ".mypy_cache", ".pytest_cache"}

#: Targets that are illustrations of a link, not links. Each entry is
#: `(document, target, why)`. A template shows the agent filling it in what a citation or
#: a figure embed looks like, and the example target is meant not to resolve.
#:
#: An entry is a claim about the file, so `test_no_exemption_outlives_its_document` fails
#: when the claim stops holding — the link resolved, or the document stopped containing
#: it. Nothing may be added here to silence a genuinely broken link.
PLACEHOLDER_LINKS = {
    ("agentevolver/skill/science/indication_dossier_skill/references/06-writing-style.md",
     "url1", "an example of the inline-citation format, inside a quoted sample finding"),
    ("agentevolver/skill/science/indication_dossier_skill/references/06-writing-style.md",
     "url2", "the second half of the same quoted sample"),
    ("agentevolver/skill/authoring/report_design_skill/references/"
     "analysis_report_template.md", "figure_1.png",
     "a slot in a report template; the agent copies the file and supplies the figure"),
    ("agentevolver/skill/authoring/report_design_skill/references/"
     "analysis_report_template.md", "figure_2.png", "the second figure slot"),
}


# --------------------------------------------------------------------------- #
# Reading a document
# --------------------------------------------------------------------------- #
def documents() -> List[Path]:
    """Every Markdown file this repository owns."""
    found = []
    for path in sorted(ROOT.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        found.append(path)
    return found


def prose(text: str) -> str:
    """The document with its code removed.

    Fenced blocks, inline spans and HTML comments all contain things shaped like links
    that are not links: a `<img src="/hero.jpg">` in a worked example, a commented-out
    figure embed left as a note to whoever fills the template in. Reading them as live
    links reported seven failures on this repository, every one of them wrong.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    kept, in_fence = [], False
    for line in text.splitlines():
        if re.match(r"^\s*(`{3,}|~{3,})", line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return re.sub(r"`[^`\n]*`", "", "\n".join(kept))


def _is_local(target: str) -> bool:
    """Whether the target names something in this repository.

    A leading `/` is a site-absolute web path, not a repository path — `/tasks/123` in a
    checklist is a URL on somebody's deployment. Treating it as `ROOT / "tasks/123"`
    would invent a failure.
    """
    if not target or target.startswith(("#", "//", "/")):
        return False
    return not re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I)


def links(path: Path) -> Iterator[Tuple[str, str]]:
    """`(target, kind)` for every local file reference in one document.

    Three syntaxes, because all three are used here: inline `[text](target)` including
    image embeds, reference definitions `[id]: target`, and raw HTML attributes — module
    READMEs embed screenshots with `<img src=...>` where Markdown's own syntax cannot
    carry the width.
    """
    body = prose(path.read_text(encoding="utf-8", errors="replace"))
    seen = set()
    patterns = (
        (r"\]\(\s*<?([^)>\s]+)", "link"),
        (r"^\s*\[[^\]^]+\]:\s*<?(\S+)>?\s*$", "reference"),
        (r"<(?:img|a|source|video|iframe)\b[^>]*?(?:src|href)=\"([^\"]+)\"", "html"),
    )
    for pattern, kind in patterns:
        for match in re.finditer(pattern, body, re.M):
            target = match.group(1).rstrip(">")
            if _is_local(target) and (target, kind) not in seen:
                seen.add((target, kind))
                yield target, kind


def resolve(path: Path, target: str) -> Path:
    """Where a target points, with the fragment and query cut off."""
    cleaned = urllib.parse.unquote(target.split("#")[0].split("?")[0])
    return (path.parent / cleaned).resolve()


def broken(path: Path) -> List[str]:
    """Targets in one document that do not exist on disk."""
    relative = str(path.relative_to(ROOT))
    exempt = {target for doc, target, _ in PLACEHOLDER_LINKS if doc == relative}
    out = []
    for target, kind in links(path):
        if target in exempt:
            continue
        if not target.split("#")[0]:
            continue
        if not resolve(path, target).exists():
            out.append(f"{relative}: {kind} -> {target}")
    return out


DOCUMENTS = documents()


# --------------------------------------------------------------------------- #
# The scan
# --------------------------------------------------------------------------- #
def test_the_walk_found_the_documentation():
    """A walk that matches nothing passes every case below without reading a file."""
    assert len(DOCUMENTS) > 100, f"only found {len(DOCUMENTS)} Markdown files"
    assert any(p.name == "README.md" and p.parent == ROOT for p in DOCUMENTS), \
        "the top-level README is not in the scan"


def test_the_scan_extracts_links_from_the_files_it_walks():
    """Separate from the walk: finding the files and reading them are different failures.

    A regex that stopped matching would leave every document with zero links and every
    assertion below true. The count is deliberately low — it guards against zero, not
    against a specific corpus.
    """
    total = sum(len(list(links(path))) for path in DOCUMENTS)
    assert total > 50, f"only {total} local links across {len(DOCUMENTS)} documents"


# --------------------------------------------------------------------------- #
# The invariant
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_file_a_document_points_at_exists(path):
    """One case per document, so a failure names the page that has to be edited.

    Parametrised rather than collected into one assertion for the same reason: a single
    case listing thirty broken links across nine files gets triaged as "the docs are
    broken" and left.
    """
    problems = broken(path)
    assert not problems, "\n  ".join(["unresolvable references:"] + problems)


def test_no_exemption_outlives_its_document():
    """An exemption is a claim about a file, and files change.

    Left unchecked, this list becomes the place broken links go to be forgotten: the
    template gets rewritten, the placeholder disappears, and the entry stays — covering
    whatever takes that name next.
    """
    stale = []
    for document, target, reason in sorted(PLACEHOLDER_LINKS):
        path = ROOT / document
        if not path.exists():
            stale.append(f"{document} no longer exists ({reason})")
            continue
        present = [t for t, _ in links(path)]
        if target not in present:
            stale.append(f"{document} no longer contains {target!r} ({reason})")
        elif resolve(path, target).exists():
            stale.append(f"{document}: {target!r} now resolves; it needs no exemption")
    assert not stale, "\n  ".join(["exemptions that no longer describe anything:"] + stale)


def test_a_broken_link_is_detected(tmp_path: Path):
    """The check must go red on a link it has not been shown.

    Every cheap way to write this check passes on everything: resolving against the wrong
    directory makes every relative link "exist" as long as the repository root has a
    similarly named file, and an `except OSError` around the lookup turns a missing file
    into a pass. So it is handed one target that resolves and one that does not.
    """
    (tmp_path / "real.md").write_text("body\n", encoding="utf-8")
    doc = tmp_path / "index.md"
    doc.write_text("[fine](real.md) and [gone](missing.md)\n", encoding="utf-8")

    found = [target for target, _ in links(doc)]
    assert found == ["real.md", "missing.md"], f"the extractor found {found}"
    assert resolve(doc, "real.md").exists()
    assert not resolve(doc, "missing.md").exists()


def test_a_link_inside_a_code_block_is_not_a_link(tmp_path: Path):
    """Documentation about Markdown contains Markdown, and it must not be followed.

    `SKILL.md` files show the agent what to write, so they are full of `[text](path.md)`
    examples whose targets are meant to be imaginary. Following them would make every
    authoring guide fail, and the reflex fix is to stop checking `SKILL.md` files — which
    is where the real links are.
    """
    doc = tmp_path / "guide.md"
    doc.write_text(
        "```markdown\n[example](nowhere.md)\n```\n\n"
        "<!-- [commented](gone.md) -->\n\n"
        "Write `[label](target.md)` inline.\n",
        encoding="utf-8")
    assert list(links(doc)) == []
