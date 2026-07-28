"""Science: one GPU-backed JupyterLab workstation per project."""

from agentevolver.science.server import ScienceManagerServer, base_path, science_manager
from agentevolver.science.types import ComputeStatus, Notebook, ScienceInstance

__all__ = [
    "ComputeStatus",
    "Notebook",
    "ScienceInstance",
    "ScienceManagerServer",
    "base_path",
    "science_manager",
]
