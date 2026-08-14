"""Declaring a thing and making it reachable are separate acts, and both must happen.

A tool class, its export, its optional config stub, and its name in each agent's list. A
model named by a config and the catalog a provider actually registers. A provider package
and the checks that walk it. Each is several places that nothing ties together, and the
failure is always quiet: the component exists, nothing references it, and the run looks
like a model that chose not to use it.

The last check here is the one that keeps the rest honest — a provider with no serializer
is invisible to a check that walks serializers, so it must be named with a reason.
Covered and not-covered are both explicit; neither is reachable by omission.
"""

from pathlib import Path
import ast
import importlib
import pkgutil
import pytest
import re

from agentevolver.model import config as model_config
import agentevolver.model as model_package


# ---------------------------------------------------------------------------
# from test_tool_registration_is_consistent.py
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "agentevolver" / "tool" / "default"
CONFIG_DIR = ROOT / "configs"


def _registered_tools() -> dict:
    """`name` of every `@TOOL.register_module` class, mapped to (module, class).

    Read from the AST rather than by importing: an import error anywhere in the package
    would empty this mapping and turn every check below green by accident.
    """
    found = {}
    for path in sorted(TOOL_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any("register_module" in ast.dump(d) for d in node.decorator_list):
                continue
            for stmt in node.body:
                if (isinstance(stmt, ast.AnnAssign)
                        and getattr(stmt.target, "id", None) == "name"
                        and isinstance(stmt.value, ast.Constant)):
                    found[stmt.value.value] = (path.name, node.name)
    return found


REGISTERED = _registered_tools()


def test_the_tools_were_actually_found():
    """A scan that finds nothing passes every check below vacuously."""
    assert len(REGISTERED) > 20, f"only found {sorted(REGISTERED)}"


@pytest.mark.parametrize("tool_name", sorted(REGISTERED))
def test_every_registered_tool_is_exported(tool_name):
    """An unexported class is never imported, so the registry never sees it."""
    module, cls = REGISTERED[tool_name]
    exports = (TOOL_DIR / "__init__.py").read_text(encoding="utf-8")
    assert cls in exports, (
        f"{cls} ({tool_name}) is not exported from tool/default/__init__.py; nothing "
        f"imports it, so it is never registered")


def test_no_config_lists_a_tool_that_does_not_exist():
    """A typo here is silent — the agent simply never gets the tool.

    The run then looks like a model that chose not to use it, which is indistinguishable
    from a model that could not.
    """
    unknown = []
    for path in sorted(CONFIG_DIR.glob("*.py")):
        for name in re.findall(r'^\s*"([a-z0-9_]+_tool)",',
                               path.read_text(encoding="utf-8"), re.M):
            if name not in REGISTERED:
                unknown.append(f"{path.name} -> {name}")
    assert not unknown, "configs name tools that are not registered:\n  " + "\n  ".join(unknown)


def test_every_imported_stub_exists():
    """The direction that actually holds. A missing stub fails at import, not at use."""
    missing = []
    for path in sorted(CONFIG_DIR.glob("*.py")):
        for stub in re.findall(r"^\s*from \.tools\.(\w+) import ",
                               path.read_text(encoding="utf-8"), re.M):
            if not (CONFIG_DIR / "tools" / f"{stub}.py").exists():
                missing.append(f"{path.name} imports configs/tools/{stub}.py, which is absent")
    assert not missing, "\n  ".join(missing)


def test_every_agent_with_bash_can_collect_a_background_job():
    """`run_in_background` without the `job_*` tools starts work nothing can read.

    The agent is handed a job id and no way to use it — worse than not being able to
    background at all, because the capability looks available and silently drops results.
    """
    stranded = []
    for path in sorted(CONFIG_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if '"bash_tool",' not in text:
            continue
        missing = [t for t in ("job_list_tool", "job_output_tool", "job_kill_tool")
                   if f'"{t}",' not in text]
        if missing:
            stranded.append(f"{path.name} lacks {missing}")
    assert not stranded, ("these agents can start background work but not collect it:\n  "
                          + "\n  ".join(stranded))

# ---------------------------------------------------------------------------
# from test_config_models_are_registered.py
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]

CATALOGS = {
    "openai": model_config.openai_models,
    "llm_hub": model_config.llm_hub_models,
    "anthropic": model_config.anthropic_models,
    "openrouter": model_config.openrouter_models,
    "google": model_config.google_models,
}

KWARGS = dict(max_tokens=4096, default_temperature=0.7, default_timeout=600,
              default_plugins=[], default_reasoning={})


def _registered() -> set:
    """Every `model_name` the providers register, across their chat/response catalogs."""
    names = set()
    for provider, build in CATALOGS.items():
        import inspect
        accepted = set(inspect.signature(build).parameters)
        specs = build(**{k: v for k, v in KWARGS.items() if k in accepted})
        for entries in specs.values():
            for entry in entries:
                names.add(entry["model_name"])
    return names


def _referenced():
    """Every `model_name = "..."` a config assigns, with where it said it."""
    pattern = re.compile(r'model_name\s*=\s*"([^"]+)"')
    for path in sorted((ROOT / "configs").rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in pattern.finditer(line):
                yield f"{path.relative_to(ROOT)}:{lineno}", match.group(1)


def test_every_config_model_is_registered():
    registered = _registered()
    unknown = [(where, name) for where, name in _referenced() if name not in registered]
    assert not unknown, (
        "these configs name a model no provider registers:\n  "
        + "\n  ".join(f"{where} -> {name}" for where, name in unknown)
        + f"\n\nregistered: {sorted(registered)}")


def test_the_check_can_actually_fail():
    """A guard that cannot fail is not a guard.

    Two ways this one could pass while checking nothing: the catalog could come back
    empty (making every name "registered" is not the failure — making the comparison
    vacuous is), or the scan could match no configs at all.
    """
    registered = _registered()
    assert "llm_hub/claude-opus-5" in registered, "a name in use must be found"
    assert "openrouter/claude-opus-5-nonexistent" not in registered, \
        "an invented name must not be"

    referenced = list(_referenced())
    assert referenced, "the scan found no configs — it would pass on an empty repo"
    assert all("/" in name for _, name in referenced), \
        "a model_name is provider/model_id; one without a slash cannot resolve"


@pytest.mark.parametrize("provider", sorted(CATALOGS))
def test_each_catalog_builds_and_is_not_empty(provider):
    """An empty catalog would make every name in it "unregistered" at once."""
    import inspect
    build = CATALOGS[provider]
    accepted = set(inspect.signature(build).parameters)
    specs = build(**{k: v for k, v in KWARGS.items() if k in accepted})
    assert any(entries for entries in specs.values()), f"{provider} registers nothing"

# ---------------------------------------------------------------------------
# from test_nothing_escapes_the_checks.py
# ---------------------------------------------------------------------------
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
    from tests.test_serializer import SERIALIZERS

    on_disk = {p.parent.name
               for p in (ROOT / "agentevolver" / "model").glob("*/serializer.py")}
    covered = {label.split(".")[0] for label, _, _ in SERIALIZERS}
    assert on_disk == covered, (
        f"serializer.py exists for {sorted(on_disk)} but the walk collected "
        f"{sorted(covered)}; the difference is unguarded")
