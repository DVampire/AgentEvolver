"""Prompt Context Protocol (PCP) Types

Core type definitions for the Prompt Context Protocol.
Prompts are loaded from .md files; each file defines one agent prompt (system + user).
"""
import re
import yaml
from typing import Any, Dict, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field, ConfigDict, PrivateAttr

from src.logger import logger
from src.message import Message, SystemMessage, HumanMessage, ContentPartText

if TYPE_CHECKING:
    from src.optimizer.types import Variable

try:
    from jinja2 import Template as JinjaTemplate
    _JINJA2_AVAILABLE = True
except ImportError:
    _JINJA2_AVAILABLE = False


def _render_template(template_str: str, modules: Dict[str, Any]) -> str:
    if not _JINJA2_AVAILABLE:
        return template_str
    return JinjaTemplate(template_str).render(**modules)


def parse_prompt_text(text: str) -> "PromptConfig":
    """Parse a full md file text into a PromptConfig."""
    fm_match = re.match(r'^---\n(.*?)\n---\n(.*)', text, re.DOTALL)
    if not fm_match:
        raise ValueError("Invalid md format: missing YAML frontmatter delimiters")

    yaml_block = fm_match.group(1)
    body = fm_match.group(2)

    fm = yaml.safe_load(yaml_block) or {}
    name = fm.get("name", "")
    description = fm.get("description", "")
    version = str(fm.get("version", "1.0.0"))
    require_grad = bool(fm.get("require_grad", False))

    parts = re.split(r'<!--\s*role:\s*(system|user)\s*-->', body)
    system_template = ""
    user_template = ""
    i = 0
    while i < len(parts):
        if parts[i].strip().lower() == "system" and i + 1 < len(parts):
            # Strip trailing --- separator if present before <!-- role: user -->
            system_template = re.sub(r'\n---\s*$', '', parts[i + 1]).strip()
            i += 2
        elif parts[i].strip().lower() == "user" and i + 1 < len(parts):
            user_template = parts[i + 1].strip()
            i += 2
        else:
            i += 1

    return PromptConfig(
        name=name,
        description=description,
        version=version,
        require_grad=require_grad,
        system_template=system_template,
        user_template=user_template,
    )


def parse_prompt_file(path: str) -> "PromptConfig":
    """Read and parse an .md file into a PromptConfig."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        return parse_prompt_text(text)
    except Exception as e:
        raise ValueError(f"Failed to parse md file {path}: {e}") from e


def reconstruct_prompt_text(prompt: "Prompt") -> str:
    """Rebuild the canonical md file text from a Prompt instance's stored fields."""
    fm = {
        "name": prompt.name,
        "description": prompt.description,
        "version": prompt.version,
        "require_grad": prompt.require_grad,
    }
    yaml_block = yaml.dump(fm, default_flow_style=False, allow_unicode=True).rstrip()
    return (
        f"---\n{yaml_block}\n---\n\n"
        f"<!-- role: system -->\n{prompt.system_template}\n\n"
        f"---\n\n"
        f"<!-- role: user -->\n{prompt.user_template}\n"
    )


class Prompt(BaseModel):
    """A prompt loaded from an .md file, with system and user templates."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="Prompt name, from md frontmatter")
    description: str = Field(default="", description="Short description of the agent")
    version: str = Field(default="1.0.0", description="Version string")
    require_grad: bool = Field(default=False, description="Whether this prompt is a trainable variable")
    system_template: str = Field(default="", description="System prompt text (Jinja2)")
    user_template: str = Field(default="", description="User/agent message text (Jinja2)")
    variables: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Static Jinja2 render-context defaults")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Miscellaneous metadata")

    _system_message_cache: Optional[SystemMessage] = PrivateAttr(default=None)

    def _merged_modules(self, modules: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged = dict(self.variables or {})
        if modules:
            merged.update(modules)
        return merged

    async def get_system_message(self, modules: Optional[Dict[str, Any]] = None, reload: bool = False) -> SystemMessage:
        if self._system_message_cache is not None and not reload:
            return self._system_message_cache
        rendered = _render_template(self.system_template, self._merged_modules(modules))
        self._system_message_cache = SystemMessage(content=rendered)
        return self._system_message_cache

    async def get_user_message(self, modules: Optional[Dict[str, Any]] = None, reload: bool = True) -> HumanMessage:
        rendered = _render_template(self.user_template, self._merged_modules(modules))
        return HumanMessage(content=[ContentPartText(text=rendered)])

    async def get_trainable_variable(self) -> Optional["Variable"]:
        """Returns a single Variable representing the whole md file, if require_grad=True."""
        if not self.require_grad:
            return None
        from src.optimizer.types import Variable
        return Variable(
            name=self.name,
            type="prompt",
            description=self.description,
            require_grad=True,
            template=None,
            variables=reconstruct_prompt_text(self),
        )

    def __str__(self):
        return f"Prompt(name={self.name}, version={self.version})"

    def __repr__(self):
        return self.__str__()


class PromptConfig(BaseModel):
    """Prompt configuration — parsed from an .md file or loaded from JSON."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="Prompt name")
    description: str = Field(default="", description="Short description")
    version: str = Field(default="1.0.0", description="Version string")
    require_grad: bool = Field(default=False, description="Whether the whole prompt is a trainable variable")
    system_template: str = Field(default="", description="System prompt text")
    user_template: str = Field(default="", description="User/agent message text")
    variables: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Static Jinja2 render-context defaults")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Miscellaneous metadata")

    def to_prompt(self) -> Prompt:
        return Prompt(
            name=self.name,
            description=self.description,
            version=self.version,
            require_grad=self.require_grad,
            system_template=self.system_template,
            user_template=self.user_template,
            variables=self.variables or {},
            metadata=self.metadata or {},
        )

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "require_grad": self.require_grad,
            "system_template": self.system_template,
            "user_template": self.user_template,
            "variables": self.variables or {},
            "metadata": self.metadata or {},
        }

    @classmethod
    def model_validate(cls, data: Dict[str, Any]) -> "PromptConfig":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            require_grad=data.get("require_grad", False),
            system_template=data.get("system_template", ""),
            user_template=data.get("user_template", ""),
            variables=data.get("variables", {}),
            metadata=data.get("metadata", {}),
        )

    def __str__(self):
        return f"PromptConfig(name={self.name}, version={self.version})"

    def __repr__(self):
        return self.__str__()
