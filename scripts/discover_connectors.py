#!/usr/bin/env python3
"""Ask each connector's MCP server what it exposes, and write it into CONNECTOR.md.

An MCP server declares an `inputSchema` and a description for every tool it offers.
Both reach this framework already — `ConnectorContextManager.discover` opens the session
and gets them — but they live only in memory, so a fresh process starts knowing nothing
again, and every action goes to a model as `{"additionalProperties": true}`: it can see
that `biomart__get_data` exists and has no way to know what to pass it.

The arguments were documented in prose for 29% of actions and nowhere for the rest, so
this is not a formatting improvement. It is the difference between a model calling an
action and a model guessing at one.

Writing them into the manifest's frontmatter is what makes them survive: `_parse_connector_dir`
reads `actions`, `action_schemas` and `action_descriptions` from there, so a checkout that
has never opened a session still describes every action correctly.

    scripts/discover_connectors.py                 # every connector
    scripts/discover_connectors.py biomart pubmed  # only these
    scripts/discover_connectors.py --check         # report drift, write nothing

Needs `langchain-mcp-adapters`, and needs each server to actually start — they are ordinary
programs with their own dependencies. A server that cannot start is reported and skipped;
its manifest is left exactly as it was, because a connector whose contract could not be
read is not a connector with no contract.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MANIFEST = "CONNECTOR.md"
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
#: Keys this script owns. Everything else in the frontmatter is left untouched.
OWNED = ("actions", "action_schemas", "action_descriptions")


def _dump(value: Any, indent: int = 2) -> str:
    """Render one frontmatter value as YAML, block style, stable across runs."""
    import yaml

    text = yaml.safe_dump(value, sort_keys=True, allow_unicode=True, default_flow_style=False)
    pad = " " * indent
    return "".join(pad + line + "\n" for line in text.rstrip("\n").splitlines())


def _replace_block(front: str, key: str, value: Any) -> str:
    """Replace one top-level frontmatter key's block, or append it.

    Surgical on the raw text rather than a parse-and-dump of the whole thing. A
    round trip through the YAML loader rewrites every key it touches — folding a
    long description, escaping an em dash, alphabetising a mapping somebody wrote
    in a deliberate order — and a generator that reformats the lines it was not
    asked about makes every future diff unreadable.
    """
    block = f"{key}:\n" + _dump(value)
    pattern = re.compile(rf"(?ms)^{re.escape(key)}:[^\n]*\n(?:[ \t\-].*\n|\n)*")
    if pattern.search(front):
        return pattern.sub(lambda _: block, front, count=1)
    return front.rstrip("\n") + "\n" + block


def _rewrite(path: Path, contract: Dict[str, Any]) -> str:
    """The manifest with this script's keys refreshed and every other line as it was."""
    raw = path.read_text(encoding="utf-8")
    match = FRONTMATTER.match(raw)
    if not match:
        raise ValueError(f"{path} has no frontmatter")
    front, body = match.group(1) + "\n", match.group(2)
    for key in OWNED:
        if key in contract:
            front = _replace_block(front, key, contract[key])
    return "---\n" + front + "---\n" + body


async def _contract(name: str) -> Tuple[bool, Dict[str, Any], str]:
    """Open a session to one connector and read back what it exposes."""
    from agentevolver.connector.server import connector_manager

    info = await connector_manager.get_info(name)
    if info is None:
        return False, {}, "not registered"
    actions = await connector_manager.discover(name)
    if actions is None:
        return False, {}, "could not connect"
    fresh = await connector_manager.get_info(name)
    return True, {
        # The server's own order, not alphabetical: a connector lists its actions in
        # the order they are meant to be reached ("list_marts … Start here"), and
        # sorting them throws that away for a tidier diff.
        "actions": list(fresh.actions),
        "action_schemas": dict(sorted(fresh.action_schemas.items())),
        "action_descriptions": dict(sorted(fresh.action_descriptions.items())),
    }, ""


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="connector names (default: every registered one)")
    parser.add_argument("--check", action="store_true", help="report drift, write nothing")
    args = parser.parse_args()

    from agentevolver.connector.server import connector_manager

    await connector_manager.initialize(connector_names=None)
    names = args.names or await connector_manager.list()

    written, unchanged, failed = [], [], []
    for name in names:
        ok, contract, why = await _contract(name)
        if not ok:
            failed.append((name, why))
            continue
        info = await connector_manager.get_info(name)
        path = Path(info.connector_dir) / MANIFEST
        if not path.exists():
            failed.append((name, f"no {MANIFEST}"))
            continue
        expected = _rewrite(path, contract)
        if expected == path.read_text(encoding="utf-8"):
            unchanged.append(name)
            continue
        n_schemas = len(contract["action_schemas"])
        if args.check:
            written.append(f"{name} (out of date: {len(contract['actions'])} actions, "
                           f"{n_schemas} schemas)")
            continue
        path.write_text(expected, encoding="utf-8")
        written.append(f"{name} ({len(contract['actions'])} actions, {n_schemas} schemas)")

    for line in written:
        print(("would update " if args.check else "updated ") + line)
    if unchanged:
        print(f"{len(unchanged)} already current")
    for name, why in failed:
        print(f"!! {name}: {why}", file=sys.stderr)

    if args.check and written:
        print("\nCONNECTOR.md is behind its server; run scripts/discover_connectors.py",
              file=sys.stderr)
        return 1
    # A server that would not start is a bad run, not a bad manifest: exiting 0 here
    # would let a broken connector pass a CI check that exists to catch exactly that.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
