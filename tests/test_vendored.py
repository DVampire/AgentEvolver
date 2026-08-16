"""What is in `others/` is recorded where a clone can see it.

Those checkouts are gitignored, so a fresh clone has none of them and nothing in the
tracked tree would otherwise say they exist. Their provenance — upstream, commit, licence
— lives only in a nested `.git` that one machine has, which is the same failure as a
dependency with no lockfile: reproducible on the machine that already worked.

The pin is not bookkeeping. A reference kept to settle "what does the upstream actually
do" cannot settle it if its version is unknown, and the notes written about one cite a
commit. So a checkout that moves and a manifest that does not is a disagreement worth
failing over.

These skip rather than fail when `others/` is absent. It is reference material, not a
dependency: CI has no reason to clone five repositories to check a table, and a gate that
demanded it would make the absence of optional material an error.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OTHERS = ROOT / "others"
MANIFEST = ROOT / "docs" / "vendored.md"


def _checkouts() -> list[Path]:
    if not OTHERS.is_dir():
        return []
    return sorted(path for path in OTHERS.iterdir() if (path / ".git").exists())


def _rows() -> dict[str, str]:
    """Directory → the commit the manifest pins it at."""
    rows = re.findall(r"^\| `([^`]+)/` \|[^|]+\| `([0-9a-f]{40})`", MANIFEST.read_text(), re.M)
    return {name: commit for name, commit in rows}


def _head(path: Path) -> str:
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_the_manifest_exists_whether_or_not_the_checkouts_do():
    """The tracked half must stand alone — it is the half a clone gets."""
    assert MANIFEST.exists(), "docs/vendored.md is the only record a fresh clone has"
    assert _rows(), "the manifest lists no checkouts"


@pytest.mark.skipif(not _checkouts(), reason="others/ is optional reference material")
def test_every_checkout_is_listed():
    """An unlisted checkout is one nobody can attribute or licence-check."""
    missing = [p.name for p in _checkouts() if p.name not in _rows()]
    assert not missing, f"these are in others/ but not in docs/vendored.md: {missing}"


@pytest.mark.skipif(not _checkouts(), reason="others/ is optional reference material")
def test_the_manifest_lists_nothing_that_is_not_there():
    """A row for a checkout nobody has reads as a claim about material that was consulted."""
    present = {p.name for p in _checkouts()}
    assert not (set(_rows()) - present), (
        f"docs/vendored.md lists checkouts that are not in others/: {set(_rows()) - present}"
    )


@pytest.mark.skipif(not _checkouts(), reason="others/ is optional reference material")
@pytest.mark.parametrize("checkout", [p.name for p in _checkouts()])
def test_the_pinned_commit_is_the_one_checked_out(checkout):
    """The assertion the pin is for.

    Notes written about a checkout cite a commit. Moving the checkout without moving the
    row leaves those notes describing code that is no longer there, and nothing says so.
    """
    assert _rows()[checkout] == _head(OTHERS / checkout), (
        f"{checkout} is checked out at a different commit than docs/vendored.md records; "
        f"update the row in the same change"
    )


@pytest.mark.skipif(not _checkouts(), reason="others/ is optional reference material")
@pytest.mark.parametrize("checkout", [p.name for p in _checkouts()])
def test_every_checkout_carries_its_licence(checkout):
    """Recording a licence that is not in the checkout would be worse than recording none."""
    files = [f.name for f in (OTHERS / checkout).iterdir()
             if f.is_file() and f.name.lower().startswith(("license", "licence"))]
    assert files, f"{checkout} has no licence file, but docs/vendored.md names one"


@pytest.mark.skipif(not _checkouts(), reason="others/ is optional reference material")
def test_reference_material_is_unmodified():
    """Edited reference is not reference.

    A reader comparing this repository against a modified checkout is comparing against
    something neither project published, and the difference they find is one we made.
    """
    edited = [
        p.name for p in _checkouts()
        if subprocess.run(["git", "-C", str(p), "status", "--porcelain"],
                          capture_output=True, text=True, check=True).stdout.strip()
    ]
    assert not edited, f"these checkouts have local changes: {edited}"
