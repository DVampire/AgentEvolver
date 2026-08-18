"""The published tool catalog is what the registry holds, and it holds everything written.

Two failures live here, and both are quiet. A generated document that nobody regenerates
becomes a confident, wrong description of the code — worse than no document, because a
reader trusts it and a later agent is handed it as fact. And a tool class whose module
nothing imports is never registered at all: the agent is simply never offered it, and the
run looks like a model that chose not to use the tool rather than one that could not see
it. `tests/test_registration.py` catches the missing *export* by reading
`tool/default/__init__.py`; this file catches the same defect from the other end, by
importing the package and asking the registry what it actually got — which also reaches
the tools in `tool/default/search/` and `tool/other/` that a top-level glob never sees.

The parameter check reads the call schema the request carries: `__call__`'s signature is
what binds an argument, its `Args:` docstring is what describes one, and the schema is
built from both. An argument with no `Args:` line reaches the model unexplained, and one
the model was never told about is one it cannot send.

Every fact below comes from one subprocess, not from importing the tool package here. In
this process the registry holds whatever the whole test session imported, and a tool
module that only some *other* test file imports registers anyway — so an in-process
version of this file would pass alone and fail in a full run, or the reverse, for reasons
that have nothing to do with the catalog.
"""

from pathlib import Path
import importlib.util
import json
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gen_tool_catalog.py"


def _inventory() -> dict:
    """What one clean `import agentevolver.tool` registers, as data."""
    result = subprocess.run([sys.executable, str(SCRIPT), "--inventory"],
                            capture_output=True, text=True, cwd=ROOT, timeout=300)
    assert result.returncode == 0, (
        f"gen_tool_catalog.py --inventory failed:\n{result.stderr[-2000:]}")
    return json.loads(result.stdout)


INVENTORY = _inventory()
TOOLS = INVENTORY["tools"]
CATALOG = ROOT / "docs" / "tool-catalog.md"

# --------------------------------------------------------------------------- #
# The scan itself
# --------------------------------------------------------------------------- #
def test_the_registry_was_actually_populated():
    """Every check below compares against this set; empty, they all pass vacuously."""
    assert len(TOOLS) > 20, f"only found {sorted(TOOLS)}"


def test_the_source_scan_finds_tools_outside_the_top_level_package():
    """The subdirectories are the half a `tool/default/*.py` glob misses.

    Four search tools live in `tool/default/search/` and one in `tool/other/`. A guard
    that only looked at the top level would report full coverage while never having
    examined them — which is the state `tests/test_registration.py` is in.
    """
    modules = {entry["module"] for entry in TOOLS.values()}
    nested = {m for m in modules if m.count(".") > 3}
    assert nested, (
        f"no tool was found below agentevolver/tool/<pkg>/; the scan is looking in the "
        f"wrong place. Found: {sorted(modules)}")


# --------------------------------------------------------------------------- #
# Completeness
# --------------------------------------------------------------------------- #
def test_every_tool_class_in_the_tree_is_reachable_from_an_import():
    """Writing the class is not registering it; some module has to import it.

    This is the failure that produces no error anywhere. The class is decorated, the file
    is on disk, and `import agentevolver.tool` never touches it, so the registry has one
    fewer tool than the repository does and every agent config naming it silently gets
    nothing. The comparison is AST-against-registry: taking both sides from the registry
    would make the guard agree with itself.
    """
    assert not INVENTORY["gap"], "\n  ".join(
        ["tool classes that never reach the registry:"] + INVENTORY["gap"])


# --------------------------------------------------------------------------- #
# The committed document
# --------------------------------------------------------------------------- #
def test_the_committed_catalog_matches_what_the_registry_holds():
    """A generated file nobody verifies is a stale file with a generator attached.

    The failure names the first line that differs rather than only saying "regenerate",
    because the usual reason this goes red is a tool someone added minutes ago — and an
    instruction to regenerate, with no statement of what changed, invites regenerating
    without reading.
    """
    committed = CATALOG.read_text(encoding="utf-8")
    generated = INVENTORY["catalog"]
    if committed == generated:
        return

    import difflib
    diff = "".join(difflib.unified_diff(
        committed.splitlines(keepends=True), generated.splitlines(keepends=True),
        fromfile="committed", tofile="generated"))
    pytest.fail(f"docs/tool-catalog.md no longer matches the tool registry. Run "
                f"`python scripts/gen_tool_catalog.py` and commit the result.\n\n{diff}")


def test_a_stale_catalog_is_detected(tmp_path: Path, monkeypatch):
    """The comparison must fail on a difference it is not shown.

    A drift check reduces to `read the file, compare, pass` — and every way of getting
    that wrong (reading back the file it just wrote, normalising the content away before
    comparing, swallowing the mismatch) fails silently and forever. So the generator's own
    comparison is pointed at a doctored copy and required to notice, then at an exact copy
    and required not to.
    """
    spec = importlib.util.spec_from_file_location("gen_tool_catalog", SCRIPT)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    stale = tmp_path / "tool-catalog.md"
    stale.write_text(generator.render().replace("Permission mode:", "Permission:", 1),
                     encoding="utf-8")
    monkeypatch.setattr(generator, "CATALOG", stale)
    assert generator.drift(), "a doctored catalog was reported as up to date"

    stale.write_text(generator.render(), encoding="utf-8")
    assert not generator.drift(), "an exact copy was reported as drifted"


@pytest.mark.parametrize("class_name", sorted(TOOLS))
def test_every_registered_tool_appears_in_the_catalog(class_name):
    """Read back from the committed document, not from the renderer's own output.

    The renderer and the document agree with each other by construction — one produced the
    other. Asking the file on disk whether it names each registered tool is a different
    question, and it is the one a reader of the file actually has.
    """
    text = CATALOG.read_text(encoding="utf-8")
    name = TOOLS[class_name]["name"]
    assert f"## `{name}`" in text, (
        f"{name} ({class_name}) is registered but has no section in docs/tool-catalog.md")


def test_the_catalog_says_it_is_generated():
    """Without the banner the next person edits it by hand and loses the edit.

    They are not warned: the generator overwrites the file, it does not merge into it.
    """
    first_block = CATALOG.read_text(encoding="utf-8").split("\n\n")[0]
    assert "scripts/gen_tool_catalog.py" in first_block, (
        "docs/tool-catalog.md does not open with a banner naming its generator")


# --------------------------------------------------------------------------- #
# Signature against prose
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("class_name", sorted(TOOLS))
def test_every_parameter_the_model_can_send_is_documented(class_name):
    """An argument only `__call__` knows about is one the model never sends.

    It costs nothing visible: the parameter keeps its default, the call succeeds, and the
    capability is simply unreachable. `http_request_tool.timeout` sat like that — a real
    argument controlling how long a request may hang, absent from the only text the model
    is given about the tool.

    Checked against the call schema rather than against prose. A tool used to restate its
    arguments as a `## Parameters` block, and that block was a third spelling of one
    contract — the signature binds, the `Args:` docstring describes, and the prose was the
    copy that could disagree with both. So this reads the schema the request actually
    carries, which is stricter than the old check: a parameter named in passing used to
    satisfy it, and now it has to be described.
    """
    entry = TOOLS[class_name]
    if not entry["parameters"]:
        return

    documented = entry["documented"]
    missing = [name for name in entry["parameters"] if name not in documented]
    assert not missing, (
        f"{entry['name']} accepts {missing} but the call schema has no such property; "
        f"the model is being sent a contract that does not match the signature")

    undescribed = [name for name in entry["parameters"] if not documented.get(name, "").strip()]
    assert not undescribed, (
        f"{entry['name']} accepts {undescribed} with no description — add an `Args:` line "
        f"for each in `__call__`'s docstring, which is what the schema is built from")


@pytest.mark.parametrize("class_name", sorted(TOOLS))
def test_every_tool_declares_a_permission_mode_the_manager_accepts(class_name):
    """An unknown mode raises only when the tool is built, deep in agent startup.

    `permission_manager.register` converts the string to a `PermissionMode`, so a typo
    surfaces as "failed to create tool" during discovery, with the permission system
    nowhere in the message and the tool missing from the run.
    """
    from agentevolver.permission import PermissionMode

    mode = TOOLS[class_name]["permission_mode"]
    assert mode in {m.value for m in PermissionMode}, (
        f"{TOOLS[class_name]['name']} declares permission_mode={mode!r}, which is not "
        f"one of {sorted(m.value for m in PermissionMode)}")
