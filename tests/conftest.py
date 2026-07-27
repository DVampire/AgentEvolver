"""Shared pytest fixtures.

Every test is given a throwaway ``AGENTEVOLVER_HOME``, which relocates the whole
tree, so generated state — staging manifests, caches, the deploy registry, run
checkpoints — lands in a temp directory instead of the developer's checkout.
Without this, promotion tests (which write a staging manifest) leak files into it.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_agentevolver_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("agentevolver-home")
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(home))
