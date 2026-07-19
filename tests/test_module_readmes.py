"""Keep module documentation discoverable and machine-readable."""

from pathlib import Path
import re

import yaml


PACKAGE_ROOT = Path(__file__).parents[1] / "agentevolver"
REQUIRED_FRONTMATTER = {
    "name", "description", "version", "type", "category", "requirements", "metadata",
}


def _frontmatter(path: Path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path} must start with YAML frontmatter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), f"{path} frontmatter must be a mapping"
    return data, text[match.end():]


def test_every_managed_python_module_has_a_readme():
    """Bundled Office Skill scripts are resources, not framework modules."""
    module_dirs = [PACKAGE_ROOT]
    for init_file in PACKAGE_ROOT.rglob("__init__.py"):
        directory = init_file.parent
        relative = directory.relative_to(PACKAGE_ROOT)
        if "skill" in relative.parts and "scripts" in relative.parts:
            continue
        module_dirs.append(directory)

    missing = [str(path.relative_to(PACKAGE_ROOT)) for path in module_dirs
               if not (path / "README.md").is_file()]
    assert not missing, f"Modules missing README.md: {missing}"


def test_all_package_readmes_have_versioned_frontmatter():
    for readme in PACKAGE_ROOT.rglob("README.md"):
        frontmatter, body = _frontmatter(readme)
        assert REQUIRED_FRONTMATTER <= frontmatter.keys(), readme
        assert re.fullmatch(r"\d+\.\d+\.\d+", str(frontmatter["version"])), readme
        assert all(frontmatter[key] for key in ("name", "description", "type", "category")), readme
        assert isinstance(frontmatter["requirements"], list), readme
        assert isinstance(frontmatter["metadata"], dict), readme
        assert re.search(r"(?im)^#\s+\S", body), f"{readme} needs a human-readable title"

        assert str(frontmatter["version"]) == "1.0.0", readme
