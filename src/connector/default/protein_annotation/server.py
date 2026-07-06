#!/usr/bin/env python3
"""Protein Annotation MCP server — protein domains and families over the PUBLIC
EBI InterPro API (https://www.ebi.ac.uk/interpro/api), no auth. Covers InterPro
entries and Pfam clans/families (Pfam is integrated into InterPro).

Run as a stdio MCP server:  python server.py
"""
from __future__ import annotations

import requests
from mcp.server.fastmcp import FastMCP

INTERPRO = "https://www.ebi.ac.uk/interpro/api"
HDRS = {"User-Agent": "AgentEvolver-protein/1.0", "Accept": "application/json"}
TIMEOUT = 30
MAX_ROWS = 40

mcp = FastMCP("protein_annotation")


def _get(url, **params):
    r = requests.get(url, params=params, headers=HDRS, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"InterPro {url} -> {r.status_code}: {r.text[:120]}")
    return r.json()


def _name(md: dict) -> str:
    n = md.get("name")
    if isinstance(n, dict):
        return n.get("name") or n.get("short") or ""
    return n or ""


def _cap(rows, scope):
    if len(rows) > MAX_ROWS:
        rows = rows[:MAX_ROWS] + [f"... ({len(rows) - MAX_ROWS} more {scope} truncated)"]
    return "\n".join(rows)


@mcp.tool()
def search_interpro_entries(query: str, limit: int = 15) -> str:
    """Search InterPro entries (domains/families/sites) by keyword.

    Args:
        query: search text (e.g. "kinase", "zinc finger").
        limit: max entries (default 15).
    Returns 'accession<TAB>name<TAB>type' rows.
    """
    j = _get(f"{INTERPRO}/entry/interpro", search=query, page_size=max(1, min(limit, MAX_ROWS)))
    res = j.get("results", [])
    if not res:
        return f"No InterPro entries for '{query}'."
    rows = [f"# {j.get('count','?')} entries match; showing {len(res)}", "accession\tname\ttype"]
    for x in res:
        m = x.get("metadata", {})
        rows.append(f"{m.get('accession','')}\t{_name(m)}\t{m.get('type','')}")
    return _cap(rows, "entries")


@mcp.tool()
def get_interpro_entry(interpro_id: str) -> str:
    """Get an InterPro entry's details (type, GO terms, member DBs, description).

    Args:
        interpro_id: InterPro accession (e.g. "IPR000719").
    """
    m = _get(f"{INTERPRO}/entry/interpro/{interpro_id}").get("metadata", {})
    if not m:
        return f"No InterPro entry {interpro_id}."
    go = ", ".join(g.get("identifier", "") for g in (m.get("go_terms") or [])[:8])
    members = ", ".join(f"{db}({len(ids)})" for db, ids in (m.get("member_databases") or {}).items())
    desc = ""
    d = m.get("description")
    if isinstance(d, list) and d:
        desc = (d[0].get("text", "") if isinstance(d[0], dict) else str(d[0]))
    return (f"accession: {m.get('accession','')}\nname: {_name(m)}\ntype: {m.get('type','')}\n"
            f"member_databases: {members}\ngo_terms: {go}\n"
            f"description: {' '.join(desc.replace(chr(10),' ').split())[:500]}")


@mcp.tool()
def get_domain_architecture(uniprot: str, limit: int = 30) -> str:
    """Get the domain architecture of a protein — all InterPro/member entries on it.

    Args:
        uniprot: UniProt accession (e.g. "P04637").
        limit: max entries (default 30).
    """
    j = _get(f"{INTERPRO}/entry/all/protein/uniprot/{uniprot}", page_size=max(1, min(limit, MAX_ROWS)))
    res = j.get("results", [])
    if not res:
        return f"No domain entries for {uniprot}."
    rows = [f"# {j.get('count','?')} entries on {uniprot}", "accession\tsource_db\tname\ttype"]
    for x in res:
        m = x.get("metadata", {})
        rows.append(f"{m.get('accession','')}\t{m.get('source_database','')}\t{_name(m)}\t{m.get('type','')}")
    return _cap(rows, "entries")


@mcp.tool()
def search_pfam_clans(query: str = "", limit: int = 20) -> str:
    """Search/list Pfam clans (superfamilies grouping related families).

    Args:
        query: substring to match against clan accession/name (optional).
        limit: max clans (default 20).
    """
    q = query.lower().strip()
    rows = ["accession\tname"]
    url = f"{INTERPRO}/set/pfam"
    fetched = 0
    while url and len(rows) <= limit:
        j = _get(url, page_size=100) if "?" not in url else _get(url)
        for x in j.get("results", []):
            m = x.get("metadata", {})
            acc, name = m.get("accession", ""), _name(m)
            if q and q not in acc.lower() and q not in name.lower():
                continue
            rows.append(f"{acc}\t{name}")
            if len(rows) > limit:
                break
        url = j.get("next")
        fetched += 1
        if fetched > 5:
            break
    return _cap(rows, "clans") if len(rows) > 1 else f"No Pfam clans matching '{query}'."


@mcp.tool()
def get_pfam_clan(clan_id: str) -> str:
    """Get a Pfam clan's details (name, description).

    Args:
        clan_id: Pfam clan accession (e.g. "CL0001").
    """
    m = _get(f"{INTERPRO}/set/pfam/{clan_id}").get("metadata", {})
    if not m:
        return f"No Pfam clan {clan_id}."
    d = m.get("description")
    desc = d.get("name") if isinstance(d, dict) else (d or "")
    return f"accession: {m.get('accession','')}\nname: {_name(m)}\ndescription: {' '.join(str(desc).split())[:600]}"


@mcp.tool()
def get_pfam_family_proteins(pfam_id: str, limit: int = 20) -> str:
    """List UniProt proteins that contain a given Pfam family.

    Args:
        pfam_id: Pfam family accession (e.g. "PF00069").
        limit: max proteins (default 20).
    """
    j = _get(f"{INTERPRO}/protein/UniProt/entry/pfam/{pfam_id}", page_size=max(1, min(limit, MAX_ROWS)))
    res = j.get("results", [])
    if not res:
        return f"No proteins for Pfam {pfam_id}."
    rows = [f"# {j.get('count','?')} proteins contain {pfam_id}; showing {len(res)}", "accession\tname\tsource"]
    for x in res:
        m = x.get("metadata", {})
        rows.append(f"{m.get('accession','')}\t{_name(m)}\t{m.get('source_database','')}")
    return _cap(rows, "proteins")


@mcp.tool()
def get_pfam_family_proteomes(pfam_id: str, limit: int = 20) -> str:
    """List proteomes (organisms) in which a Pfam family is found.

    Args:
        pfam_id: Pfam family accession (e.g. "PF00069").
        limit: max proteomes (default 20).
    """
    try:
        j = _get(f"{INTERPRO}/proteome/uniprot/entry/pfam/{pfam_id}", page_size=max(1, min(limit, MAX_ROWS)))
    except (RuntimeError, requests.RequestException):
        return (f"The InterPro proteome aggregation for {pfam_id} did not respond in time "
                f"(it can be slow for large families). Try again or reduce limit.")
    res = j.get("results", [])
    if not res:
        return f"No proteomes for Pfam {pfam_id}."
    rows = [f"# {j.get('count','?')} proteomes contain {pfam_id}; showing {len(res)}", "accession\tname"]
    for x in res:
        m = x.get("metadata", {})
        rows.append(f"{m.get('accession','')}\t{_name(m)}")
    return _cap(rows, "proteomes")


if __name__ == "__main__":
    mcp.run()
