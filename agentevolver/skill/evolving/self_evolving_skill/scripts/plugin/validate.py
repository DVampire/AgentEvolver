#!/usr/bin/env python3
"""Quick structural validation for a plugin package (PLUGIN.md + plugin.py + tools/).

Checks what a plugin's own loader will later depend on, without importing the service
code: the package layout, the manifest fields, and whether the declared tool counts agree
with the tool classes actually present. Import errors from third-party requirements are
reported as unverified rather than as failures — a plugin whose dependency is not installed
here is not thereby malformed.
"""

import ast
import re
import sys
from pathlib import Path

import yaml

REQUIRED_FRONTMATTER = ('id', 'name', 'category', 'type', 'tools', 'implemented', 'version')
KNOWN_FRONTMATTER = set(REQUIRED_FRONTMATTER) | {
    'icon', 'credentials', 'requirements', 'featured', 'description', 'metadata',
}


def _frontmatter(md_path):
    raw = md_path.read_text(encoding='utf-8')
    match = re.match(r'\A---\n(.*?)\n---\n', raw, re.S)
    if not match:
        return None, "No YAML frontmatter found in PLUGIN.md"
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        return None, f"Invalid YAML in PLUGIN.md frontmatter: {e}"
    if not isinstance(data, dict):
        return None, "PLUGIN.md frontmatter must be a YAML mapping"
    return data, raw


def _declared_tool_classes(plugin_py):
    """The names in `tools = (...)`, read from the source rather than by importing it."""
    try:
        tree = ast.parse(plugin_py.read_text(encoding='utf-8'))
    except SyntaxError as e:
        return None, f"plugin.py does not parse: {e}"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == 'tools' for t in node.targets):
            continue
        value = node.value
        if isinstance(value, (ast.Tuple, ast.List)):
            return [n.id for n in value.elts if isinstance(n, ast.Name)], None
        return None, "`tools` is assigned something other than a tuple/list of classes"
    return None, "plugin.py declares no `tools = (...)` tuple"


def validate_plugin(plugin_path):
    plugin_path = Path(plugin_path)
    if not plugin_path.is_dir():
        return False, f"Not a directory: {plugin_path}"

    for required in ('__init__.py', 'plugin.py', 'PLUGIN.md'):
        if not (plugin_path / required).is_file():
            return False, f"Missing required file: {required}"
    tools_dir = plugin_path / 'tools'
    if not tools_dir.is_dir():
        return False, "Missing required directory: tools/"
    if not (tools_dir / '__init__.py').is_file():
        return False, "Missing tools/__init__.py — tools/ must be an importable package"

    data, raw = _frontmatter(plugin_path / 'PLUGIN.md')
    if data is None:
        return False, raw

    for field in REQUIRED_FRONTMATTER:
        if field not in data:
            return False, f"Missing '{field}' in PLUGIN.md frontmatter"
    unknown = set(data) - KNOWN_FRONTMATTER
    if unknown:
        return False, f"Unknown frontmatter field(s): {sorted(unknown)}"

    plugin_id = str(data['id'])
    if plugin_id != plugin_path.name:
        return False, f"'id' is {plugin_id!r} but the directory is {plugin_path.name!r}"
    if not re.fullmatch(r'[a-z][a-z0-9_]*', plugin_id):
        return False, f"'id' {plugin_id!r} should be snake_case"

    declared, error = _declared_tool_classes(plugin_path / 'plugin.py')
    if declared is None:
        return False, error

    tool_files = sorted(p.name for p in tools_dir.glob('*.py') if p.name != '__init__.py')
    try:
        count, implemented = int(data['tools']), int(data['implemented'])
    except (TypeError, ValueError):
        return False, "'tools' and 'implemented' must be integers"
    if count != len(declared):
        return False, (
            f"frontmatter says tools: {count}, but plugin.py declares {len(declared)} "
            f"({', '.join(declared) or 'none'})"
        )
    if implemented > count:
        return False, f"'implemented' ({implemented}) exceeds 'tools' ({count})"
    if not tool_files:
        return False, "tools/ contains no tool modules"

    icon = data.get('icon')
    if icon and not (plugin_path / str(icon)).is_file():
        return False, f"'icon' points at a missing file: {icon}"

    credentials = data.get('credentials', [])
    if not isinstance(credentials, list):
        return False, "'credentials' must be a list of env var names (use [] for none)"
    if credentials and '## Credentials' not in raw:
        return False, "declares credentials but PLUGIN.md has no '## Credentials' section"

    notes = []
    if implemented < count:
        notes.append(f"{count - implemented} of {count} tool(s) not yet implemented")
    if len(tool_files) != len(declared):
        notes.append(
            f"{len(tool_files)} file(s) under tools/ against {len(declared)} declared "
            "class(es) — a file that is not in the tuple stays invisible"
        )
    suffix = f" Note: {'; '.join(notes)}." if notes else ""
    return True, f"Plugin is valid! ({count} tool(s), id={plugin_id}).{suffix}"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python validate.py <plugin_directory>")
        sys.exit(1)
    valid, message = validate_plugin(sys.argv[1])
    print(message)
    sys.exit(0 if valid else 1)
