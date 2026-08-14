"""Shared pytest fixtures.

The test session runs against a throwaway tree, so generated state — staging
manifests, caches, the deploy registry, run checkpoints, knowledge bases,
workflow evidence — lands in a temp directory instead of the developer's
checkout.

``AGENTEVOLVER_HOME`` alone is not enough. ``process_general`` resolves
``project_root`` / ``workspace_root`` / ``log_root`` to absolute paths when the
config is first processed, and every manager that derives a ``base_dir`` from
``config.log_root`` captures one of those. An absolute path ignores the
override, so the roots are repointed as well — once, at session scope, before
the first manager is built.
"""

import os
from pathlib import Path

import pytest

from agentevolver.config import config


@pytest.fixture(autouse=True, scope="session")
def _isolate_agentevolver_tree(tmp_path_factory):
    home = tmp_path_factory.mktemp("agentevolver-home")
    output = home / "output" / "test"
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTEVOLVER_HOME", str(home))
    patch.setattr(config, "project_root", str(output), raising=False)
    patch.setattr(config, "workspace_root", str(output / "workspace"), raising=False)
    patch.setattr(config, "log_root", str(output / "log"), raising=False)
    yield
    patch.undo()


# ---------------------------------------------------------------------------
# Repairing an interrupted mutation run
# ---------------------------------------------------------------------------
#: `test_consistency_checks_can_fail.py` proves each consistency check still catches its
#: defect by reintroducing the defect in real source and requiring the check to go red.
#: `finally` covers an exception; it does not cover SIGKILL or a ctrl-C at the wrong
#: instant, and a lost restore leaves the tree quietly broken — it happened once, and the
#: next full run reported sixteen unrelated failures with no hint of the cause.
#:
#: So the original bytes are parked beside the file before it is mutated, and the repair
#: below runs for *every* session. It lives here rather than in that test module because
#: a repair that only runs when you happen to invoke the damaged file's own test is not a
#: repair — the run that trips over the damage is usually a different one.
BACKUP_SUFFIX = ".mutation-backup"

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _restore_parked_originals() -> None:
    for backup in _REPO_ROOT.rglob(f"*{BACKUP_SUFFIX}"):
        target = Path(str(backup)[: -len(BACKUP_SUFFIX)])
        try:
            target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
            backup.unlink()
            print(f"restored {target.relative_to(_REPO_ROOT)} "
                  f"from an interrupted mutation run")
        except OSError as error:                                    # noqa: PERF203
            # Reported, never swallowed: a repair that fails silently leaves exactly the
            # state this exists to prevent, and the next failure looks unrelated.
            print(f"could not restore {target}: {error}")


#: Set by the parent when it launches a mutation subprocess. Without it the repair below
#: runs inside that subprocess and undoes the very defect the parent just introduced, so
#: the check under test sees clean source and passes — reported as "this check does not
#: catch its defect" when in fact nothing was ever wrong with it. The two mechanisms are
#: both right and cancel each other out; the parent is the one that knows.
#:
#: A subprocess killed while skipping the repair still leaves its backup behind, and the
#: next ordinary run restores it. Nothing is lost by opting out here.
SKIP_REPAIR_ENV = "AGENTEVOLVER_SKIP_MUTATION_REPAIR"


@pytest.fixture(scope="session", autouse=True)
def repair_interrupted_mutations():
    """Put back anything a killed mutation run left mutated — before and after."""
    if os.environ.get(SKIP_REPAIR_ENV):
        yield
        return
    _restore_parked_originals()
    yield
    _restore_parked_originals()
