"""Kernel: one Jupyter Server per project, and one kernel everything shares.

Everything that runs code in a project goes through it — the agent's
``code_interpreter_tool``, the Science view's REPL, and JupyterLab — so there is
one set of variables and nothing to keep in sync.

`notebooks` and `compute` are the workstation's furniture: the ``.ipynb`` files
in the project, and what the machine is running on. Both were their own module
once; both only ever answered by asking this one, so they are imported lazily
here rather than as a layer of their own.
"""

from .types import (
    ComputeStatus,
    Execution,
    KernelOutput,
    KernelResult,
    KernelStatus,
    Notebook,
    RICH_MIME,
)
from .server import KernelManagerServer, kernel_manager

__all__ = [
    "ComputeStatus",
    "Execution",
    "KernelManagerServer",
    "KernelOutput",
    "KernelResult",
    "KernelStatus",
    "Notebook",
    "RICH_MIME",
    "kernel_manager",
]
