"""Prompts module for agent prompt management."""

from .template import *
from .server import prompt_manager
from .types import Prompt, PromptConfig

__all__ = [
    "prompt_manager",
    "Prompt",
    "PromptConfig",
]
