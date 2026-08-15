"""The public documentation has one global header, footer, and brand mark.

These pages used to carry three copied navigation implementations. They drifted in
their icons, column order, active state, and responsive breakpoints. The chrome is now a
shared asset; this gate prevents a new page or later copy/paste from splitting it again.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PUBLIC_PAGES = {
    "index.html": "home",
    "tutorial.html": "tutorial",
    "architecture.html": "architecture",
    "modules.html": "modules",
    "development.html": "development",
    "ui.html": "ui",
}
GLOBAL_KEYS = {"nav_home", "nav_tut", "nav_arch", "nav_mod", "nav_dev", "nav_ui"}


def test_every_public_page_uses_the_shared_chrome_and_favicon():
    problems = []
    for filename, current in PUBLIC_PAGES.items():
        body = (DOCS / filename).read_text(encoding="utf-8")
        expected = f'<nav data-ae-nav="{current}"></nav>'
        for requirement in (expected, 'assets/chrome.css', 'assets/chrome.js',
                            'assets/favicon.svg', '<ae-footer></ae-footer>'):
            if body.count(requirement) != 1:
                problems.append(f"{filename}: expected exactly one {requirement!r}")
        if "🧬" in body or ">◆<" in body or "&#9670;" in body:
            problems.append(f"{filename}: contains a retired page-local brand icon")
    assert not problems, "site chrome drift:\n  " + "\n  ".join(problems)


def test_every_page_can_translate_every_global_column():
    problems = []
    for filename in PUBLIC_PAGES:
        body = (DOCS / filename).read_text(encoding="utf-8")
        for key in GLOBAL_KEYS:
            declarations = re.findall(rf'["\']?{key}["\']?\s*:', body)
            if len(declarations) < 2:
                problems.append(f"{filename}: {key} is not declared in both languages")
    assert not problems, "global navigation translation drift:\n  " + "\n  ".join(problems)


def test_shared_navigation_targets_are_real_public_pages():
    source = (DOCS / "assets" / "chrome.js").read_text(encoding="utf-8")
    items = re.findall(
        r"\['(home|tutorial|architecture|modules|development|ui)', "
        r"'([^']+)', '(nav_[^']+)', '([^']+)'\]",
        source,
    )
    assert len(items) == len(PUBLIC_PAGES), items
    assert {href for _, href, _, _ in items} == set(PUBLIC_PAGES)
    assert {key for _, _, key, _ in items} == GLOBAL_KEYS
    assert 'aria-current="page"' in source
