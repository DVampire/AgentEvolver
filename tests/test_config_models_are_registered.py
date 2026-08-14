"""Every model a config names must be one some provider actually registers.

Three configs pointed at `openrouter/claude-opus-5` while `OPENROUTER_API_BASE` had been
repointed at a relay whose catalog stops at opus-4.8. Nothing failed until a run reached
the model and came back "no available channel" — by which time the failure looks like an
outage rather than a name that was never going to resolve.

The name is checkable without a network, so it is checked here: a `model_name` is
`<provider>/<model_id>`, and the provider's catalog function is the list of ids it
serves. This says nothing about whether the relay is up or the key has quota — only that
the name could resolve at all, which is the part that was silently wrong.
"""

import re
from pathlib import Path

import pytest

from agentevolver.model import config as model_config

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
