"""Every tool that declares a permission can actually produce it.

`glob_search_tool`, `grep_search_tool` and `list_dir_tool` each declared
`permission_request` and referenced `PermissionRequest` and `Operation` without
importing them. The pipeline's permission guard fails closed, so all three tools failed
*every* call with `Tool policy guard failed closed: NameError`.

It survived 2613 tests because nothing ever called `permission_request` outside a
hand-written stub: the pipeline tests supply their own tool objects, and the workspace
tools' own tests exercise `__call__` directly. A declaration that is never executed is
exactly the shape that passes a suite and fails in production, so this walks the real
registry instead of naming the three that were broken — the next tool to declare one
gets the same check for free.
"""

import inspect

import pytest

from agentevolver.permission import Operation, PermissionRequest
from agentevolver.registry import TOOL
from agentevolver.tool.types import Tool


def _declaring_tools():
    """Registered tool classes that override `permission_request`."""
    found = []
    for name in TOOL.module_dict:
        cls = TOOL.get(name)
        if not (inspect.isclass(cls) and issubclass(cls, Tool)):
            continue
        if cls.permission_request is Tool.permission_request:
            continue
        found.append((name, cls))
    return sorted(found)


def test_the_registry_has_tools_that_declare_a_permission():
    """Guards the guard: an empty sweep would pass every assertion below."""
    assert len(_declaring_tools()) >= 5


@pytest.mark.parametrize("name,cls", _declaring_tools(), ids=lambda v: v if isinstance(v, str) else "")
def test_a_declared_permission_can_be_produced(name, cls):
    """Called the way the pipeline's guard calls it: real arguments, no stub.

    The permission layer runs this inside a fail-closed guard, so anything raised here
    is not a degraded check — it is the tool refusing every call it is ever given.
    """
    arguments = {
        "path": "/tmp/probe.txt", "root": "/tmp", "pattern": "*.py",
        "command": "true", "file_path": "/tmp/probe.txt", "content": "x",
    }
    try:
        request = cls(enable_evolving=False).permission_request(arguments, None)
    except (NameError, AttributeError, ImportError, TypeError) as error:
        # The class this file exists for: the declaration cannot run at all, so the
        # fail-closed guard turns it into a refusal of every call. A tool refusing a
        # specific input for a domain reason — `apply_patch` with no workspace — is a
        # different thing and is left to pass.
        pytest.fail(f"{name}.permission_request is broken: {type(error).__name__}: {error}")
    except Exception as error:  # noqa: BLE001 - a domain refusal, but it must say why
        assert str(error).strip(), f"{name} refused with an empty {type(error).__name__}"
        return

    assert request is None or isinstance(request, PermissionRequest), (
        f"{name} returned {type(request).__name__}, which the guard cannot read"
    )
    if request is not None:
        assert isinstance(request.op, Operation)
