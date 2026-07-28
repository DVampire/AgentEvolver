"""Science: the workstation half of a project — notebooks, and what runs them."""

from agentevolver.science.server import ScienceManagerServer, base_path, science_manager
from agentevolver.science.types import ComputeStatus, Notebook

__all__ = [
    "ComputeStatus",
    "Notebook",
    "ScienceManagerServer",
    "base_path",
    "science_manager",
]
