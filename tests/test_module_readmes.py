"""Keep module documentation discoverable and machine-readable."""

from pathlib import Path
import re

import yaml


PACKAGE_ROOT = Path(__file__).parents[1] / "agentevolver"
PLUGIN_ROOT = PACKAGE_ROOT / "plugins" / "default"
REQUIRED_FRONTMATTER = {
    "name", "description", "version", "type", "category", "requirements", "metadata",
}
#: What a generated PLUGIN.md must declare. ``metadata`` is deliberately absent:
#: a plugin manifest carries concrete facts (tools, credentials) rather than a
#: free-form bag.
REQUIRED_PLUGIN_FRONTMATTER = {
    "id", "name", "category", "type", "tools", "implemented", "credentials",
    "requirements", "version",
}


def _frontmatter(path: Path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path} must start with YAML frontmatter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), f"{path} frontmatter must be a mapping"
    return data, text[match.end():]


def _is_plugin_package(path: Path) -> bool:
    """A plugin package (or its ``tools/``) documents itself with PLUGIN.md."""
    return path == PLUGIN_ROOT or PLUGIN_ROOT in path.parents


def _is_inside_a_skill(path: Path) -> bool:
    """True for anything under a directory that owns a SKILL.md.

    A skill's payload — schemas, renderers, references, examples — is content the
    skill ships, not an AgentEvolver module, and much of it is vendored from
    upstream (e.g. archify, MIT). Its documentation is versioned by the skill's own
    SKILL.md frontmatter, so requiring module frontmatter inside it would mean
    editing third-party files on every upstream sync. The skill *package* README
    (``agentevolver/skill/README.md``) sits above any SKILL.md and stays covered.
    """
    for parent in path.parents:
        if parent == PACKAGE_ROOT.parent:
            break
        if (parent / "SKILL.md").is_file():
            return True
    return False


def test_every_managed_python_module_has_a_readme():
    """Bundled Office Skill scripts are resources, not framework modules."""
    module_dirs = [PACKAGE_ROOT]
    for init_file in PACKAGE_ROOT.rglob("__init__.py"):
        directory = init_file.parent
        relative = directory.relative_to(PACKAGE_ROOT)
        if "skill" in relative.parts and "scripts" in relative.parts:
            continue
        if _is_plugin_package(directory):
            continue
        module_dirs.append(directory)

    missing = [str(path.relative_to(PACKAGE_ROOT)) for path in module_dirs
               if not (path / "README.md").is_file()]
    assert not missing, f"Modules missing README.md: {missing}"


def test_all_package_readmes_have_versioned_frontmatter():
    checked = 0
    for readme in PACKAGE_ROOT.rglob("README.md"):
        if _is_inside_a_skill(readme):
            continue
        checked += 1
        frontmatter, body = _frontmatter(readme)
        assert REQUIRED_FRONTMATTER <= frontmatter.keys(), readme
        assert re.fullmatch(r"\d+\.\d+\.\d+", str(frontmatter["version"])), readme
        assert all(frontmatter[key] for key in ("name", "description", "type", "category")), readme
        assert isinstance(frontmatter["requirements"], list), readme
        assert isinstance(frontmatter["metadata"], dict), readme
        assert re.search(r"(?im)^#\s+\S", body), f"{readme} needs a human-readable title"

        assert str(frontmatter["version"]) == "1.0.0", readme

    # An exemption that swallowed everything would make this test pass by
    # checking nothing.
    assert checked > 20, f"only {checked} module READMEs checked — exemption too broad?"


def test_every_plugin_package_has_a_manifest():
    packages = sorted(path for path in PLUGIN_ROOT.iterdir()
                      if path.is_dir() and (path / "__init__.py").is_file())
    assert packages, "no plugin packages found"

    missing = [path.name for path in packages if not (path / "PLUGIN.md").is_file()]
    assert not missing, f"Plugin packages missing PLUGIN.md: {missing}"

    for package in packages:
        manifest = package / "PLUGIN.md"
        frontmatter, body = _frontmatter(manifest)
        assert REQUIRED_PLUGIN_FRONTMATTER <= frontmatter.keys(), manifest
        assert frontmatter["id"] == package.name, manifest
        assert re.fullmatch(r"\d+\.\d+\.\d+", str(frontmatter["version"])), manifest
        assert isinstance(frontmatter["credentials"], list), manifest
        assert isinstance(frontmatter["requirements"], list), manifest
        assert re.search(r"(?im)^#\s+\S", body), f"{manifest} needs a human-readable title"


def test_plugin_manifests_match_the_code():
    """A manifest that drifts from its package is worse than none at all."""
    from agentevolver.plugins import plugin_manager  # noqa: PLC0415 — import cost
    import asyncio

    async def infos():
        await plugin_manager.initialize()
        try:
            return {config.name: (len(config.tools),
                                  sum(1 for tool in config.tools.values() if tool.implemented))
                    for config in await plugin_manager.list_infos()}
        finally:
            await plugin_manager.cleanup()

    live = asyncio.run(infos())
    mismatches = []
    for package in sorted(PLUGIN_ROOT.iterdir()):
        manifest = package / "PLUGIN.md"
        if not manifest.is_file():
            continue
        frontmatter, _ = _frontmatter(manifest)
        declared = (frontmatter["tools"], frontmatter["implemented"])
        actual = live.get(package.name)
        if actual is None:
            mismatches.append(f"{package.name}: has a manifest but does not register")
        elif declared != actual:
            mismatches.append(f"{package.name}: manifest says {declared}, code has {actual}")
    assert not mismatches, "PLUGIN.md is stale — regenerate it:\n  " + "\n  ".join(mismatches)


def test_the_module_reference_page_lists_exactly_the_modules_that_exist():
    """`docs/modules.html` is a hand-written card per module, so it drifts by omission.

    Nothing imports a documentation page, which is what makes this the failure mode it is:
    deleting a module leaves its card standing and every reader is told about something
    that is gone, while adding one leaves a reader who never learns it exists. Both look
    exactly like a correct page.

    It had already happened four times over — `docker`, `queue`, `scope` and `spill` all
    outlived their directories on that page, and the README counted 56 modules against 50
    on disk. The count is checked here too, since it is the same fact stated a third time.
    """
    import re

    page = (Path(__file__).parents[1] / "docs" / "modules.html").read_text(encoding="utf-8")
    listed = set(re.findall(r'class="mod-h"><b>([a-z_]+)</b>', page))
    on_disk = {p.name for p in PACKAGE_ROOT.iterdir()
               if p.is_dir() and p.name != "__pycache__"}

    assert not listed - on_disk, (
        f"documented but deleted: {sorted(listed - on_disk)} — remove the card and its "
        f"`m_<name>` entry from both translation tables"
    )
    assert not on_disk - listed, (
        f"on disk but undocumented: {sorted(on_disk - listed)} — add a card and both "
        f"`m_<name>` translations"
    )


def test_every_documented_module_is_translated_in_both_languages():
    """A card with no translation renders its i18n key to the reader.

    Separate from the listing check because it fails the other way round: removing a card
    and leaving its `m_<name>` strings behind is invisible, while adding a card and
    forgetting them shows `m_foo` on the page.
    """
    import re

    page = (Path(__file__).parents[1] / "docs" / "modules.html").read_text(encoding="utf-8")
    listed = set(re.findall(r'class="mod-h"><b>([a-z_]+)</b>', page))

    untranslated = sorted(name for name in listed
                          if len(re.findall(rf'"m_{name}"\s*:', page)) != 2)
    assert not untranslated, (
        f"modules without exactly one English and one Chinese string: {untranslated}"
    )

    orphaned = sorted({key for key in re.findall(r'"m_([a-z_]+)"\s*:', page)} - listed)
    assert not orphaned, f"translations for cards that no longer exist: {orphaned}"
