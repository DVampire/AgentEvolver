#!/usr/bin/env python3
"""Genomes MCP server — genome annotation over the PUBLIC Ensembl REST API
(rest.ensembl.org), no auth: gene lookup, xrefs, VEP, homology, sequence, overlap.

Run as a stdio MCP server:  python server.py
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.server.fastmcp import FastMCP

ENSEMBL = "https://rest.ensembl.org"
HDRS = {"User-Agent": "AgentEvolver-genomes/1.0", "Accept": "application/json"}
TIMEOUT = 30
MAX_ROWS = 60

_SPECIES = {"human": "homo_sapiens", "mouse": "mus_musculus", "rat": "rattus_norvegicus",
            "zebrafish": "danio_rerio", "fly": "drosophila_melanogaster"}

mcp = FastMCP("genomes")


def _ens(path, **params):
    r = requests.get(f"{ENSEMBL}{path}", params=params, headers=HDRS, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"Ensembl {path} -> {r.status_code}: {r.text[:200]}")
    return r.json()


def _sp(species: str) -> str:
    return _SPECIES.get(species.lower(), species)


def _cap(rows, scope):
    if len(rows) > MAX_ROWS:
        rows = rows[:MAX_ROWS] + [f"... ({len(rows) - MAX_ROWS} more {scope} truncated)"]
    return "\n".join(rows)


# ================================================================= Ensembl
@mcp.tool()
def ensembl_lookup(query: str, species: str = "human") -> str:
    """Look up a gene/transcript by Ensembl id or gene symbol.

    Args:
        query: Ensembl id (e.g. "ENSG00000012048") or gene symbol (e.g. "BRCA1").
        species: species for symbol lookup (default "human").
    """
    if query.upper().startswith("ENS"):
        d = _ens(f"/lookup/id/{query}", expand=0)
    else:
        d = _ens(f"/lookup/symbol/{_sp(species)}/{query}", expand=0)
    if not d:
        return f"No Ensembl record for '{query}'."
    keys = ["id", "display_name", "biotype", "seq_region_name", "start", "end", "strand", "description"]
    return "\n".join(f"{k}: {d.get(k)}" for k in keys if d.get(k) is not None)


@mcp.tool()
def ensembl_xrefs(query: str, species: str = "human") -> str:
    """Cross-references (external DB ids) for a gene via Ensembl.

    Args:
        query: Ensembl id or gene symbol (e.g. "BRCA1").
        species: species for symbol lookup (default "human").
    Returns 'db<TAB>primary_id<TAB>display_id' rows.
    """
    if query.upper().startswith("ENS"):
        ens_id = query
    else:
        hit = _ens(f"/lookup/symbol/{_sp(species)}/{query}", expand=0)
        ens_id = hit.get("id") if hit else None
    if not ens_id:
        return f"Could not resolve '{query}'."
    data = _ens(f"/xrefs/id/{ens_id}")
    rows = ["db\tprimary_id\tdisplay_id"]
    for x in (data if isinstance(data, list) else []):
        rows.append(f"{x.get('dbname','')}\t{x.get('primary_id','')}\t{x.get('display_id','')}")
    return _cap(rows, "xrefs") if len(rows) > 1 else f"No xrefs for {ens_id}."


@mcp.tool()
def ensembl_vep_variant(variant: str, species: str = "human") -> str:
    """Predict variant effects (VEP) for an rsID, HGVS notation, or region/allele.

    Args:
        variant: dbSNP rsID (e.g. "rs699"), HGVS (e.g. "ENST00000269305.4:c.215C>G"),
            or region:allele (e.g. "17:43044295:A").
        species: species (default "human").
    """
    v = variant.strip()
    if v.lower().startswith("rs"):
        data = _ens(f"/vep/{species}/id/{v}")
    else:
        data = _ens(f"/vep/{species}/hgvs/{v}")
    if not data:
        return f"No VEP result for '{variant}'."
    d = data[0]
    out = [f"input: {d.get('input', variant)}", f"most_severe_consequence: {d.get('most_severe_consequence')}",
           f"location: {d.get('seq_region_name')}:{d.get('start')}-{d.get('end')}"]
    tcs = d.get("transcript_consequences") or []
    if tcs:
        out.append("transcript_consequences (sample):")
        for tc in tcs[:8]:
            out.append(f"  {tc.get('gene_symbol','')}\t{tc.get('transcript_id','')}\t"
                       f"{','.join(tc.get('consequence_terms', []))}\t{tc.get('impact','')}")
    return "\n".join(out)


@mcp.tool()
def ensembl_homology(gene: str, species: str = "human", target_species: str = "") -> str:
    """Orthologues of a gene across species via Ensembl Compara.

    Args:
        gene: gene symbol (e.g. "BRCA1").
        species: source species (default "human").
        target_species: optional single target species to restrict to (e.g. "mouse").
    """
    params = {"type": "orthologues", "format": "condensed"}
    if target_species.strip():
        params["target_species"] = _sp(target_species)
    data = _ens(f"/homology/symbol/{_sp(species)}/{gene}", **params).get("data", [])
    homs = data[0].get("homologies", []) if data else []
    if not homs:
        return f"No orthologues for {gene}."
    rows = [f"# orthologues of {gene}", "homolog_id\tspecies\ttype"]
    for h in homs:
        rows.append(f"{h.get('id','')}\t{h.get('species','')}\t{h.get('type','')}")
    return _cap(rows, "orthologues")


@mcp.tool()
def ensembl_sequence(ensembl_id: str, seq_type: str = "genomic", max_len: int = 1000) -> str:
    """Fetch the sequence for an Ensembl id.

    Args:
        ensembl_id: gene/transcript/protein id (e.g. "ENST00000357654").
        seq_type: "genomic", "cds", "cdna", or "protein" (default "genomic").
        max_len: max sequence characters to return (default 1000; full length reported).
    """
    d = _ens(f"/sequence/id/{ensembl_id}", type=seq_type)
    seq = d.get("seq", "") if isinstance(d, dict) else ""
    if not seq:
        return f"No {seq_type} sequence for {ensembl_id}."
    body = seq[:max(1, max_len)]
    more = f"\n... (total {len(seq)} bp/aa; showing {len(body)})" if len(seq) > len(body) else ""
    return f"# {ensembl_id} ({seq_type}), length {len(seq)}\n{body}{more}"


@mcp.tool()
def ensembl_overlap_region(region: str, species: str = "human", feature: str = "gene") -> str:
    """List features overlapping a genomic region via Ensembl.

    Args:
        region: "chrom:start-end" (e.g. "17:43044295-43125483").
        species: species (default "human").
        feature: feature type — gene, transcript, exon, variation, regulatory (default "gene").
    """
    data = _ens(f"/overlap/region/{_sp(species)}/{region}", feature=feature)
    if not isinstance(data, list) or not data:
        return f"No {feature} features in {region}."
    rows = [f"# {feature} features in {region}", "name\tid\tbiotype\tstart\tend"]
    for f in data:
        rows.append(f"{f.get('external_name', f.get('id',''))}\t{f.get('id','')}\t"
                    f"{f.get('biotype','')}\t{f.get('start','')}\t{f.get('end','')}")
    return _cap(rows, "features")


if __name__ == "__main__":
    mcp.run()
