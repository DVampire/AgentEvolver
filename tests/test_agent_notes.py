"""A decision record is only worth keeping while it is still findable and still true.

The corpus under `.agents/notes/` exists because reasoning kept ending up in commit
messages and nowhere else — `_REFREEZE_RATIO = 0.25` and its companion floor were chosen
against a measurement, and the measurement lived in a commit and a chat log. Notes fix
that only if they stay accurate, so the ways they rot are what this file guards: a note
naming a package that was renamed away, an `affects` path that no longer exists, a cited
commit that never did, a section left as a heading with nothing under it, a note moved to
`superseded/` without saying what replaced it.

The subjects are discovered rather than listed. Packages come from the `agentevolver/`
tree, notes and postmortems from disk, and the lifecycle folders and required sections
from `.agents/notes/README.md` itself — so the convention document and the check cannot
drift apart, which is the failure that makes a written convention worse than none.

File contents are read inside each test rather than at collection: collection runs before
session fixtures, and a module-level read captures whatever happens to be on disk at that
instant.
"""

import re
import subprocess
from datetime import date
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / ".agents" / "notes"
NOTES_README = NOTES / "README.md"
POSTMORTEMS = ROOT / "docs" / "postmortem"
PACKAGE_ROOT = ROOT / "agentevolver"

#: Frontmatter every note carries. `commits` is here because a note summarizes commit
#: messages rather than replacing them: a reader who wants the numbers behind a claim
#: should be one `git show` away, and a note citing nothing cannot be told apart from a
#: note someone invented.
REQUIRED_KEYS = {"status", "date", "owner", "affects", "commits"}
#: What a note in `superseded/` adds. Retirement is a move plus these two facts.
SUPERSEDED_KEYS = {"superseded_by", "superseded_on"}

FILENAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
POSTMORTEM_FILENAME = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
#: Enough prose that the section says something. A heading with a sentence under it is
#: how a required-section rule gets satisfied without being met.
MIN_SECTION_CHARS = 200


# --------------------------------------------------------------------------- #
# Discovery — the subjects come from the tree and from the convention document
# --------------------------------------------------------------------------- #
def _readme() -> str:
    return NOTES_README.read_text(encoding="utf-8")


def _lifecycles() -> list:
    """The lifecycle folders the README's layout block declares.

    Read from the README rather than hardcoded here, so adding a lifecycle means editing
    the document a human reads and getting the check for free — instead of the two
    disagreeing and the document losing.
    """
    return re.findall(r"^\s+([a-z]+)/YYYY-MM-DD-topic-slug\.md$", _readme(), re.M)


def _required_sections() -> list:
    """The body skeleton the README prescribes, taken from its own example block."""
    block = re.search(r"```markdown\n# Agent Note: <title>\n(.*?)```", _readme(), re.S)
    assert block, "the README no longer shows a body skeleton for a note"
    return re.findall(r"^## .+$", block.group(1), re.M)


def _packages() -> set:
    """Every top-level package under `agentevolver/` — the closed set of `owner` values."""
    return {path.name for path in PACKAGE_ROOT.iterdir()
            if path.is_dir() and (path / "__init__.py").is_file()}


def _note_paths() -> list:
    return sorted(p for p in NOTES.rglob("*.md") if p.name != "README.md")


def _postmortem_paths() -> list:
    return sorted(p for p in POSTMORTEMS.glob("*.md") if p.name != "README.md")


def _split(path: Path):
    """A note's frontmatter mapping and its body."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path.name} must open with YAML frontmatter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), f"{path.name} frontmatter must be a mapping"
    return data, text[match.end():]


def _sections(body: str) -> dict:
    """`## heading` -> the prose beneath it, up to the next heading of the same level."""
    found = {}
    parts = re.split(r"^(## .+)$", body, flags=re.M)
    for heading, prose in zip(parts[1::2], parts[2::2]):
        found[heading.strip()] = prose.strip()
    return found


#: Paths only; the text is read per test. A killed run or a concurrent edit can change a
#: file between collection and assertion, and a check that reports a defect fixed ten
#: minutes ago is worse than one that reports nothing.
NOTE_PATHS = _note_paths()
POSTMORTEM_PATHS = _postmortem_paths()
NOTE_IDS = [p.name for p in NOTE_PATHS]
POSTMORTEM_IDS = [p.name for p in POSTMORTEM_PATHS]


# --------------------------------------------------------------------------- #
# The corpus exists and is where the convention says it is
# --------------------------------------------------------------------------- #
def test_the_notes_were_actually_found():
    """A discovery that returns nothing makes every parametrized check below vacuous.

    pytest reports zero collected cases as a pass, so an empty or moved directory would
    turn this whole file green while checking nothing at all.
    """
    assert NOTE_PATHS, f"no notes found under {NOTES}"
    assert POSTMORTEM_PATHS, f"no postmortems found under {POSTMORTEMS}"
    assert _lifecycles(), "the README declares no lifecycle folders"
    assert _required_sections(), "the README prescribes no sections"
    assert len(_packages()) > 20, f"only found packages {sorted(_packages())}"


def test_nothing_sits_outside_a_lifecycle_folder():
    """A note one directory up is a note nobody browsing the tree will find.

    The lifecycle is the only thing the path encodes, so a file that escapes it has no
    status at all — and the checks that read status from the folder skip it silently.
    """
    lifecycles = set(_lifecycles())
    stray = [str(p.relative_to(NOTES)) for p in NOTE_PATHS
             if p.parent.parent != NOTES or p.parent.name not in lifecycles]
    assert not stray, (f"these are not in one of {sorted(lifecycles)}: {stray}")


@pytest.mark.parametrize("path", NOTE_PATHS, ids=NOTE_IDS)
def test_every_note_is_named_for_the_day_its_decision_landed(path):
    """The date is the decision's, not the note's, and the two are easy to confuse.

    A note written weeks later and filed under the day it was typed puts the reasoning
    beside the wrong commits, which is exactly the trail `commits` exists to preserve.
    """
    match = FILENAME.match(path.name)
    assert match, f"{path.name} must be YYYY-MM-DD-topic-slug.md"
    front, _ = _split(path)
    assert str(front["date"]) == match.group(1), (
        f"{path.name}: frontmatter date {front['date']!r} disagrees with the filename")


# --------------------------------------------------------------------------- #
# Frontmatter — the facts a note is indexed and checked by
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", NOTE_PATHS, ids=NOTE_IDS)
def test_every_note_declares_the_required_frontmatter(path):
    front, _ = _split(path)
    missing = REQUIRED_KEYS - front.keys()
    assert not missing, f"{path.name} is missing {sorted(missing)}"
    assert isinstance(front["affects"], list) and front["affects"], (
        f"{path.name}: affects must be a non-empty list of repo-relative paths")
    assert isinstance(front["commits"], list) and front["commits"], (
        f"{path.name}: commits must be a non-empty list — a note that cites nothing "
        f"cannot be told apart from one someone invented")
    assert isinstance(front["date"], date), (
        f"{path.name}: date must be a YAML date, not {type(front['date']).__name__}")


@pytest.mark.parametrize("path", NOTE_PATHS, ids=NOTE_IDS)
def test_the_status_agrees_with_the_folder(path):
    """Two places record the lifecycle, so they can disagree.

    Moving a file without updating `status` is the whole of retiring a note done halfway:
    the tree says superseded and the note still reads as current.
    """
    front, _ = _split(path)
    assert front["status"] == path.parent.name, (
        f"{path.name} sits in {path.parent.name}/ but declares status "
        f"{front['status']!r}")


@pytest.mark.parametrize("path", NOTE_PATHS, ids=NOTE_IDS)
def test_every_note_names_a_package_that_still_exists(path):
    """`owner` is the one classification a note carries, and it is checkable.

    This is why the class is a package rather than a word like "architecture": a package
    that is renamed or removed makes its notes fail here, where a taxonomy of words would
    quietly keep filing notes under a category nothing corresponds to.
    """
    front, _ = _split(path)
    packages = _packages()
    assert front["owner"] in packages, (
        f"{path.name}: owner {front['owner']!r} is not a package under agentevolver/ "
        f"({sorted(packages)})")


@pytest.mark.parametrize("path", NOTE_PATHS, ids=NOTE_IDS)
def test_every_affected_path_still_exists(path):
    """A note pointing at a file that moved sends the reader somewhere empty.

    The reader then has to decide whether the decision moved with the file or was deleted
    with it, and the cheapest answer — assume it is stale — throws away the reasoning.
    """
    front, _ = _split(path)
    gone = [target for target in front["affects"] if not (ROOT / target).exists()]
    assert not gone, (
        f"{path.name} lists paths that no longer exist: {gone}. Update the note in the "
        f"change that moved them.")


@pytest.mark.parametrize("path", NOTE_PATHS, ids=NOTE_IDS)
def test_every_cited_commit_is_a_real_commit(path):
    """The citation is the note's evidence; an invented SHA is worse than none.

    A reader who runs `git show` on it gets an error and no way to tell whether the note
    is wrong or their checkout is shallow, so both the note and the trail behind it stop
    being trustworthy.
    """
    front, _ = _split(path)
    for sha in front["commits"]:
        assert re.fullmatch(r"[0-9a-f]{7,40}", str(sha)), (
            f"{path.name}: {sha!r} is not a short SHA")
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout; the SHA format is all that can be checked")
    unknown = []
    for sha in front["commits"]:
        probe = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                               cwd=ROOT, capture_output=True)
        if probe.returncode != 0:
            unknown.append(str(sha))
    assert not unknown, f"{path.name} cites commits this repository does not have: {unknown}"


# --------------------------------------------------------------------------- #
# The body — the part a reader actually needs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", NOTE_PATHS, ids=NOTE_IDS)
def test_every_note_opens_with_its_title(path):
    _, body = _split(path)
    first = body.lstrip("\n").splitlines()[0]
    assert re.match(r"^# Agent Note: \S", first), (
        f"{path.name} must open with `# Agent Note: <title>`, got {first!r}")


@pytest.mark.parametrize("path", NOTE_PATHS, ids=NOTE_IDS)
def test_every_note_carries_the_four_sections_in_order(path):
    """Order matters because the sections build on each other.

    `## Problem` has to stand without the solution; if it cannot be written first it is
    usually a restatement of the fix, which is the shape of a note that records nothing.
    """
    _, body = _split(path)
    required = _required_sections()
    present = [h for h in re.findall(r"^## .+$", body, re.M) if h in required]
    assert present == required, (
        f"{path.name} has sections {present}; the README prescribes {required}")


@pytest.mark.parametrize("path", NOTE_PATHS, ids=NOTE_IDS)
def test_no_required_section_is_a_heading_with_nothing_under_it(path):
    """A note that restates the code is worthless, and this is how one gets written.

    The required headings are easy to satisfy with a sentence each, and a note in that
    shape costs a read and returns nothing — which is the argument for deleting the
    convention rather than for keeping notes. `## What would make this wrong` is the one
    that goes empty first: it is the only section that cannot be paraphrased from the
    diff.
    """
    _, body = _split(path)
    sections = _sections(body)
    thin = {h: len(sections.get(h, "")) for h in _required_sections()
            if len(sections.get(h, "")) < MIN_SECTION_CHARS}
    assert not thin, (
        f"{path.name} has sections under {MIN_SECTION_CHARS} characters: {thin}. "
        f"A heading with a sentence under it satisfies the rule without meeting it.")


@pytest.mark.parametrize("path", NOTE_PATHS + POSTMORTEM_PATHS + [NOTES_README],
                         ids=NOTE_IDS + POSTMORTEM_IDS + ["notes-README.md"])
def test_every_relative_link_resolves(path):
    """Cross-references are how one decision points at the one that constrains it.

    A note that cites its neighbour by name in prose survives a rename; a link does not,
    and a dead link is read as "that note was deleted" rather than "that note moved".
    """
    text = path.read_text(encoding="utf-8")
    targets = re.findall(r"\]\((?!https?:|#)([^)]+)\)", text)
    broken = [t for t in targets if not (path.parent / t.split("#")[0]).exists()]
    assert not broken, f"{path.name} links to files that do not exist: {broken}"


# --------------------------------------------------------------------------- #
# Retirement
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", NOTE_PATHS, ids=NOTE_IDS)
def test_a_superseded_note_says_what_replaced_it(path):
    """A retired note with no forward pointer is a trap.

    It reads exactly like a current one to anyone who lands on it from a search, and the
    only signal that it is stale is a directory name they did not look at.
    """
    front, body = _split(path)
    if front["status"] != "superseded":
        pytest.skip("only superseded notes carry a replacement")
    missing = SUPERSEDED_KEYS - front.keys()
    assert not missing, f"{path.name} is missing {sorted(missing)}"
    replacement = NOTES / "implemented" / str(front["superseded_by"])
    assert replacement.is_file(), (
        f"{path.name}: superseded_by names {front['superseded_by']!r}, which is not a "
        f"note under implemented/")
    assert "## Superseded" in body, (
        f"{path.name} must append a `## Superseded` section saying what happened")


@pytest.mark.parametrize("path", NOTE_PATHS, ids=NOTE_IDS)
def test_an_implemented_note_does_not_claim_a_replacement(path):
    """The half-finished retirement in the other direction.

    Adding `superseded_by` and forgetting the move leaves a note that contradicts its own
    folder, and every check keyed on the folder keeps treating it as current.
    """
    front, _ = _split(path)
    if front["status"] == "superseded":
        pytest.skip("only implemented notes are checked for a stray replacement")
    stray = SUPERSEDED_KEYS & front.keys()
    assert not stray, (
        f"{path.name} is in {path.parent.name}/ but carries {sorted(stray)}; move it to "
        f"superseded/ or drop the field")


# --------------------------------------------------------------------------- #
# The convention document itself
# --------------------------------------------------------------------------- #
def test_the_notes_readme_carries_module_style_frontmatter():
    """The convention document follows the convention the repo already has.

    Every package README under `agentevolver/` declares the same seven keys; a document
    describing how to write documentation is the last place to invent a second shape.
    """
    front, body = _split(NOTES_README)
    required = {"name", "description", "version", "type", "category",
                "requirements", "metadata"}
    assert required <= front.keys(), f"README is missing {sorted(required - front.keys())}"
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(front["version"]))
    assert isinstance(front["requirements"], list)
    assert isinstance(front["metadata"], dict)
    assert re.search(r"(?im)^#\s+\S", body), "README needs a human-readable title"


def test_the_readme_explains_every_frontmatter_key_it_requires():
    """A key enforced here and undocumented there is a rule nobody can follow.

    The reader hits a failing test naming a field the convention never mentions, and the
    fastest way out is to guess at a value — which passes the check and records nothing.
    """
    readme = _readme()
    undocumented = [key for key in sorted(REQUIRED_KEYS | SUPERSEDED_KEYS)
                    if f"`{key}`" not in readme]
    assert not undocumented, (
        f"the README does not document {undocumented}, which this file requires")


def test_the_readme_describes_the_retirement_path_the_check_enforces():
    """Retirement is the part of a convention that decays first.

    Writing notes is the fun half; taking one out of service is the half that gets skipped,
    and a corpus of notes that are all silently stale is worse than no corpus.
    """
    readme = _readme()
    assert "superseded" in readme, "the README must name the retirement lifecycle"
    assert "## Superseded" in readme or "`## Superseded`" in readme, (
        "the README must say a retired note appends a `## Superseded` section")


# --------------------------------------------------------------------------- #
# Postmortems
# --------------------------------------------------------------------------- #
def test_every_postmortem_is_numbered_and_uniquely_so():
    """Two postmortems sharing a number make the index ambiguous.

    The number is how they are cited from notes and commit messages, so a collision breaks
    the references rather than the files.
    """
    numbers = {}
    for path in _postmortem_paths():
        match = POSTMORTEM_FILENAME.match(path.name)
        assert match, f"{path.name} must be NNNN-slug.md"
        numbers.setdefault(match.group(1), []).append(path.name)
    collisions = {n: names for n, names in numbers.items() if len(names) > 1}
    assert not collisions, f"duplicate postmortem numbers: {collisions}"


def test_every_postmortem_is_listed_in_the_index():
    """The index is the only way to find one without knowing it exists.

    A file added to the directory and not to the table is invisible to the reader who came
    looking for whether this class of failure has happened before — which is the single
    question a postmortem exists to answer.
    """
    readme = (POSTMORTEMS / "README.md").read_text(encoding="utf-8")
    linked = set(re.findall(r"\]\(([0-9]{4}-[^)]+\.md)\)", readme))
    on_disk = {p.name for p in _postmortem_paths()}
    assert on_disk == linked, (
        f"index and directory disagree — only on disk: {sorted(on_disk - linked)}; "
        f"only in the index: {sorted(linked - on_disk)}")


@pytest.mark.parametrize("path", POSTMORTEM_PATHS, ids=POSTMORTEM_IDS)
def test_every_postmortem_explains_why_nothing_caught_it(path):
    """The section that separates a postmortem from a commit message.

    A write-up that only explains the bug is the fix restated. What earns the file is the
    account of why the tests, the review and the measurement all passed it through.
    """
    text = path.read_text(encoding="utf-8")
    number = POSTMORTEM_FILENAME.match(path.name).group(1)
    assert text.startswith(f"# Postmortem {number}: "), (
        f"{path.name} must open with `# Postmortem {number}: <title>`")
    sections = _sections(text)
    required = ["## Summary", "## What happened", "## Why nothing caught it",
                "## What changed"]
    present = [h for h in re.findall(r"^## .+$", text, re.M) if h in required]
    assert present == required, f"{path.name} has {present}, expected {required}"
    assert len(sections["## Why nothing caught it"]) >= MIN_SECTION_CHARS, (
        f"{path.name}: the escape analysis is the point of the document")


# --------------------------------------------------------------------------- #
# The checks themselves
# --------------------------------------------------------------------------- #
def test_the_checks_can_actually_fail():
    """Every check above rests on three discoveries, and each can go empty quietly.

    If `_packages()` returned nothing, every `owner` would be rejected — loud, and not the
    danger. The danger is the opposite shape: `_required_sections()` returning `[]` makes
    the section-order and section-substance checks pass on a note with no body at all, and
    `_lifecycles()` returning `[]` would make `test_nothing_sits_outside_a_lifecycle_folder`
    the only thing that noticed. Both read the README with a regular expression, and a
    reformatted README breaks them silently.
    """
    assert "agent" in _packages() and "trace" in _packages(), \
        "package discovery must find real packages"
    assert "agentevolver" not in _packages(), \
        "discovery must yield package names, not the root"

    lifecycles = _lifecycles()
    assert "implemented" in lifecycles, "the README's layout block is no longer parsed"
    assert all(l.isidentifier() for l in lifecycles), \
        f"a lifecycle name that is not a directory name: {lifecycles}"

    sections = _required_sections()
    assert sections[0] == "## Problem", \
        f"the README's body skeleton is no longer parsed (got {sections})"
    assert "## What would make this wrong" in sections, \
        "the falsifier section is the reason for the format; it must be required"

    parsed = _sections("## A\n\nfirst\n\n## B\n\nsecond\n")
    assert parsed == {"## A": "first", "## B": "second"}, \
        f"section splitting is broken, so every substance check is vacuous: {parsed}"
