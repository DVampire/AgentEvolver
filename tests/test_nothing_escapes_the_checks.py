"""New code must not be able to arrive uncovered.

This is the guard that makes the others durable. A gate written today covers what exists
today; the failure a year from now is a provider added by someone who never read these
files, slipping past checks that never mentioned it. dsh's tool catalog does the same
thing — a completeness guard globs the packages and fails when the generator's manifest
is missing one, so a new tool cannot be silently undocumented.

The rule for adding a subject here is deliberately harsh: a package that is not covered
must be listed with a reason. "Not covered" and "covered" are then both explicit, and
neither can be reached by omission.
"""

import importlib
import pkgutil
from pathlib import Path

import agentevolver.model as model_package

ROOT = Path(__file__).resolve().parents[1]

#: Provider packages with no serializer, and why. A package that grows one must be
#: removed from here — the serializer gate then covers it automatically.
NO_SERIALIZER = {
    # (populated as such packages appear; empty means every provider has one)
}


def _provider_packages():
    root = Path(model_package.__file__).parent
    return {m.name for m in pkgutil.iter_modules([str(root)]) if m.ispkg}


def test_every_provider_package_has_a_serializer_or_a_stated_reason():
    """The gap the serializer gate cannot see: a provider it never discovered.

    That gate walks the packages that *have* a serializer module. A provider shipping
    without one — or with the module named differently — is invisible to it and would
    pass by never being looked at.
    """
    without = set()
    for name in _provider_packages():
        try:
            importlib.import_module(f"agentevolver.model.{name}.serializer")
        except ModuleNotFoundError:
            without.add(name)

    unexplained = without - set(NO_SERIALIZER)
    assert not unexplained, (
        f"provider package(s) {sorted(unexplained)} have no serializer module, so no "
        f"gate examines them. Add one, or record why not in NO_SERIALIZER.")

    stale = set(NO_SERIALIZER) - without
    assert not stale, (
        f"{sorted(stale)} now has a serializer; remove it from NO_SERIALIZER so the "
        f"serializer gate covers it")


def test_every_gate_file_states_the_defect_it_guards():
    """A gate whose reason is lost is one the next person deletes as noise.

    The reason is not decoration: it is what tells a reader whether a failure means the
    invariant broke or the gate went stale. Requiring the docstring to name a concrete
    failure keeps "what would go wrong" in the file rather than in someone's memory.
    """
    gate_dir = ROOT / "tests" / "gates"
    thin = []
    for path in sorted(gate_dir.glob("test_*.py")):
        doc = path.read_text(encoding="utf-8").split('"""')
        body = doc[1] if len(doc) > 1 else ""
        if len(body.strip()) < 200:
            thin.append(path.name)
    assert not thin, (
        f"{thin} do not explain what breaks without them; a gate without a stated "
        f"failure is deleted as noise the first time it is inconvenient")


def test_the_serializer_gate_covers_every_serializer_that_exists():
    """Cross-check by a different route than the gate's own discovery.

    Counting the modules on disk and the subjects the walk collected must agree. If the
    gate's import-based walk starts silently skipping one — a renamed module, an import
    error swallowed somewhere — the two numbers diverge and this says so.
    """
    from tests.test_serializers_cover_every_message_type import SERIALIZERS

    on_disk = {p.parent.name
               for p in (ROOT / "agentevolver" / "model").glob("*/serializer.py")}
    covered = {label.split(".")[0] for label, _, _ in SERIALIZERS}
    assert on_disk == covered, (
        f"serializer.py exists for {sorted(on_disk)} but the walk collected "
        f"{sorted(covered)}; the difference is unguarded")
