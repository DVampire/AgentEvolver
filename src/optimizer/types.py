"""
Base optimizer module.
"""

from typing import List, Optional, Any, TYPE_CHECKING
from pydantic import BaseModel, Field, ConfigDict

if TYPE_CHECKING:
    from src.session import SessionContext


class Optimizer(BaseModel):
    """Base class for all optimizers."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def __init__(self,
                 workdir: str,
                 model_name: Optional[str] = None,
                 prompt_name: Optional[str] = None,
                 memory_name: Optional[str] = None,
                 max_steps: int = 3,
                 use_memory: bool = True,
                 **kwargs
                 ):
        super().__init__(**kwargs)

        self.workdir = workdir
        self.prompt_name = prompt_name
        self.memory_name = memory_name
        self.model_name = model_name
        self.max_steps = max_steps if max_steps > 0 else int(1e8)
        self.use_memory = use_memory and (memory_name is not None)

    async def optimize(
        self,
        task: str,
        files: Optional[List[str]] = None,
        ctx: "SessionContext" = None,
        **kwargs
    ):
        raise NotImplementedError(f"``optimize`` function for {type(self).__name__} is not implemented!")

    async def close(self):
        raise NotImplementedError(f"``close`` function for {type(self).__name__} is not implemented!")
