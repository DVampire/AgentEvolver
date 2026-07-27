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
