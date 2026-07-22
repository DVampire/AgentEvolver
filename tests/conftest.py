"""Shared pytest fixtures.

Every test is given a throwaway ``AGENTEVOLVER_HOME`` so user-level state — staging
manifests, caches, the deploy registry — is written into a temp directory instead of
the real project ``.agentevolver/``. Without this, promotion tests (which write a
staging manifest) leak files into the developer's checkout.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_agentevolver_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("agentevolver-home")
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(home))
