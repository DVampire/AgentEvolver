#!/usr/bin/env python3
"""Wikipedia search CLI via mediawiki-mcp-server (stdio MCP).

Usage:
    python wiki_search.py list-tools
    python wiki_search.py search "quantum computing" --limit 5
    python wiki_search.py page "Quantum_computing"
    python wiki_search.py summary "Quantum_computing"
    python wiki_search.py sections "Quantum_computing"
    python wiki_search.py search-and-read "quantum computing"
    python wiki_search.py call <tool_name> '{"key": "value"}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from io import StringIO
from pathlib import Path

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"
DEFAULT_CONFIG_PATH = RESOURCES_DIR / "config.json"

# Resolve project root to find the binary
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent.parent
DEFAULT_BINARY = str(PROJECT_ROOT / "bin" / "mediawiki-mcp-server")


def load_config(config_path: str | None = None) -> dict:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_server_params(cfg: dict):
    """Build MCP StdioServerParameters from config."""
    from mcp import StdioServerParameters

    server_path = cfg.get("server_path") or os.environ.get(
        "WIKI_MCP_SERVER_PATH", DEFAULT_BINARY
    )
    mediawiki_url = cfg.get("mediawiki_url") or os.environ.get(
        "MEDIAWIKI_URL", "https://en.wikipedia.org/w/api.php"
    )

    env = {**os.environ, "MEDIAWIKI_URL": mediawiki_url}

    return StdioServerParameters(command=server_path, args=[], env=env)


def _extract_text(result) -> str:
    """Extract text from MCP CallToolResult content blocks."""
    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
        else:
            parts.append(str(block))
    return "\n".join(parts)


async def _run_session(cfg: dict, callback):
    """Spawn MCP server, run callback(session), handle cleanup errors."""
    from mcp.client.stdio import stdio_client
    from mcp import ClientSession

    params = get_server_params(cfg)
    captured = None

    try:
        async with stdio_client(params, errlog=open(os.devnull, "w")) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                captured = await callback(session)
    except BaseException:
        # anyio TaskGroup may raise ExceptionGroup on stdio cleanup — safe to ignore
        # if we already captured the result
        if captured is None:
            raise
    return captured


async def run_mcp_call(cfg: dict, tool_name: str, arguments: dict) -> str:
    """Spawn MCP server via stdio, call a tool, return result text."""

    async def _call(session):
        result = await session.call_tool(tool_name, arguments=arguments)
        return _extract_text(result)

    return await _run_session(cfg, _call)


async def list_tools(cfg: dict) -> str:
    """List all available MCP tools."""

    async def _list(session):
        result = await session.list_tools()
        tools_info = []
        for tool in result.tools:
            tools_info.append({"name": tool.name, "description": tool.description[:120]})
        return json.dumps(tools_info, indent=2, ensure_ascii=False)

    return await _run_session(cfg, _list)


async def cmd_search(cfg: dict, query: str, limit: int = 5) -> str:
    return await run_mcp_call(cfg, "mediawiki_search", {"query": query, "limit": limit})


async def cmd_page(cfg: dict, title: str) -> str:
    return await run_mcp_call(cfg, "mediawiki_get_page", {"title": title})


async def cmd_summary(cfg: dict, title: str) -> str:
    return await run_mcp_call(cfg, "mediawiki_get_page_summary", {"title": title})


async def cmd_sections(cfg: dict, title: str) -> str:
    return await run_mcp_call(cfg, "mediawiki_get_sections", {"title": title})


async def cmd_search_and_read(cfg: dict, query: str) -> str:
    return await run_mcp_call(cfg, "mediawiki_search_and_read", {"query": query})


async def cmd_call(cfg: dict, tool_name: str, args_json: str) -> str:
    arguments = json.loads(args_json) if args_json else {}
    return await run_mcp_call(cfg, tool_name, arguments)


def main():
    parser = argparse.ArgumentParser(description="Wikipedia search via mediawiki-mcp-server (stdio)")
    parser.add_argument("--config", default=None, help="Path to config.json")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list-tools
    subparsers.add_parser("list-tools", help="List available MCP tools")

    # search
    sp = subparsers.add_parser("search", help="Search Wikipedia")
    sp.add_argument("query", help="Search query")
    sp.add_argument("--limit", type=int, default=5, help="Max results")

    # page
    sp = subparsers.add_parser("page", help="Get full page content")
    sp.add_argument("title", help="Page title (e.g. Quantum_computing)")

    # summary
    sp = subparsers.add_parser("summary", help="Get page summary")
    sp.add_argument("title", help="Page title")

    # sections
    sp = subparsers.add_parser("sections", help="Get page sections")
    sp.add_argument("title", help="Page title")

    # search-and-read
    sp = subparsers.add_parser("search-and-read", help="Search and read top result")
    sp.add_argument("query", help="Search query")

    # call (generic)
    sp = subparsers.add_parser("call", help="Call any MCP tool by name")
    sp.add_argument("tool_name", help="MCP tool name")
    sp.add_argument("args_json", nargs="?", default="{}", help='JSON args (e.g. \'{"title":"X"}\')')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cfg = load_config(args.config)

    dispatch = {
        "list-tools": lambda: list_tools(cfg),
        "search": lambda: cmd_search(cfg, args.query, args.limit),
        "page": lambda: cmd_page(cfg, args.title),
        "summary": lambda: cmd_summary(cfg, args.title),
        "sections": lambda: cmd_sections(cfg, args.title),
        "search-and-read": lambda: cmd_search_and_read(cfg, args.query),
        "call": lambda: cmd_call(cfg, args.tool_name, args.args_json),
    }

    try:
        result = asyncio.run(dispatch[args.command]())
        print(result)
    except BaseException as e:
        # anyio TaskGroup may raise ExceptionGroup on cleanup — ignore if result was already printed
        if "unhandled errors in a TaskGroup" in str(e):
            pass
        else:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
