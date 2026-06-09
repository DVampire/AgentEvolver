"""Prompt Context Protocol (PCP) Types

Core type definitions for the Prompt Context Protocol.
Prompts are loaded from .html files; each file defines one agent prompt (system + user).
"""
import re
import html as html_module
from html.parser import HTMLParser
from typing import Any, Dict, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field, ConfigDict, PrivateAttr

from src.logger import logger
from src.message import Message, SystemMessage, HumanMessage, ContentPartText
from src.session import BaseContext


try:
    from jinja2 import Template as JinjaTemplate
    _JINJA2_AVAILABLE = True
except ImportError:
    _JINJA2_AVAILABLE = False


def _render_template(template_str: str, modules: Dict[str, Any]) -> str:
    if not _JINJA2_AVAILABLE:
        return template_str
    return JinjaTemplate(template_str).render(**modules)


class _PromptHTMLParser(HTMLParser):
    """SAX-style parser that extracts meta attributes and div.system/div.user contents."""

    def __init__(self):
        super().__init__()
        self.meta: Dict[str, str] = {}
        self._in_section: Optional[str] = None
        self._depth: int = 0  # nesting depth inside the top-level section div
        self._buf: list = []
        self.sections: Dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "meta":
            name = attr_dict.get("name", "")
            content = attr_dict.get("content", "")
            if name:
                self.meta[name] = content
            return

        if tag == "div" and self._in_section is None:
            cls = attr_dict.get("class", "")
            if cls in ("system", "user"):
                self._in_section = cls
                self._depth = 1
                self._buf = []
                return

        if self._in_section is not None:
            # Reconstruct opening tag verbatim so inner HTML is preserved
            attrs_str = ""
            for k, v in attrs:
                attrs_str += f' {k}="{v}"' if v is not None else f' {k}'
            self._buf.append(f"<{tag}{attrs_str}>")
            if tag == "div":
                self._depth += 1

    def handle_endtag(self, tag):
        if self._in_section is None:
            return
        if tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self.sections[self._in_section] = "".join(self._buf).strip()
                self._in_section = None
                return
            # inner closing div — write to buf and fall through
        self._buf.append(f"</{tag}>")

    def handle_data(self, data):
        if self._in_section is not None:
            self._buf.append(data)

    def handle_entityref(self, name):
        if self._in_section is not None:
            self._buf.append(f"&{name};")

    def handle_charref(self, name):
        if self._in_section is not None:
            self._buf.append(f"&#{name};")


def parse_prompt_text(text: str) -> "PromptConfig":
    """Parse a full HTML file text into a PromptConfig."""
    parser = _PromptHTMLParser()
    parser.feed(text)

    meta = parser.meta
    name = meta.get("name", "")
    description = meta.get("description", "")
    version = str(meta.get("version", "1.0.0"))
    require_grad = meta.get("require_grad", "false").lower() == "true"

    system_template = parser.sections.get("system", "")
    user_template = parser.sections.get("user", "")

    return PromptConfig(
        name=name,
        description=description,
        version=version,
        require_grad=require_grad,
        system_template=system_template,
        user_template=user_template,
    )


def parse_prompt_file(path: str) -> "PromptConfig":
    """Read and parse an .html file into a PromptConfig."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        return parse_prompt_text(text)
    except Exception as e:
        raise ValueError(f"Failed to parse html file {path}: {e}") from e


def reconstruct_prompt_text(prompt: "Prompt") -> str:
    """Rebuild the canonical HTML file text from a Prompt instance's stored fields."""
    req_grad = "true" if prompt.require_grad else "false"
    desc = html_module.escape(prompt.description, quote=True)
    return (
        f'<!DOCTYPE html>\n'
        f'<html lang="en">\n'
        f'<head>\n'
        f'  <meta charset="UTF-8">\n'
        f'  <meta name="name" content="{prompt.name}">\n'
        f'  <meta name="description" content="{desc}">\n'
        f'  <meta name="version" content="{prompt.version}">\n'
        f'  <meta name="require_grad" content="{req_grad}">\n'
        f'</head>\n'
        f'<body>\n\n'
        f'<div class="system">\n{prompt.system_template}\n</div>\n\n'
        f'<div class="user">\n{prompt.user_template}\n</div>\n\n'
        f'</body>\n'
        f'</html>\n'
    )


class Prompt(BaseModel):
    """A prompt loaded from an .md file, with system and user templates."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="Prompt name, from md frontmatter")
    description: str = Field(default="", description="Short description of the agent")
    version: str = Field(default="1.0.0", description="Version string")
    require_grad: bool = Field(default=False, description="Whether this prompt is a trainable variable")
    permission_mode: str = Field(default="workspace_write", description="Permission mode: read_only / workspace_write / danger_full_access")
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
    permission_mode: str = Field(default="workspace_write", description="Permission mode: read_only / workspace_write / danger_full_access")
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
            permission_mode=self.permission_mode,
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
            "permission_mode": self.permission_mode,
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
            permission_mode=data.get("permission_mode", "workspace_write"),
            system_template=data.get("system_template", ""),
            user_template=data.get("user_template", ""),
            variables=data.get("variables", {}),
            metadata=data.get("metadata", {}),
        )

    def __str__(self):
        return f"PromptConfig(name={self.name}, version={self.version})"

    def __repr__(self):
        return self.__str__()


class PromptContext(BaseContext):
    """Context passed into the prompt manager when rendering messages."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(default="", description="Unique identifier for this prompt render.")
    name: str = Field(default="", description="Name of the prompt being rendered.")
    work_dir: Optional[str] = Field(default=None, description="Working directory available to the caller.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload passed to the prompt.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this prompt context.")


__all__ = [
    "Prompt",
    "PromptConfig",
    "PromptContext",
    "parse_prompt_file",
]
