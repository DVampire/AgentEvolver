# Adapting imported skills that need credentials

Most imported science skills are self-contained (a Python lib + bundled scripts) and need no
env adaptation. ~24 declare external-service or API-key needs (an `openclaw` block in their
`metadata`). Those follow one of two patterns. Three worked templates:
`infographics_skill` (LLM), `exa_search_skill` and `benchling_integration_skill` (external
service).

## Pattern A — LLM-powered steps (route through the project relay)

Skills whose scripts call an LLM (were pointed at OpenRouter/Anthropic directly). This
project already runs an **OpenRouter-compatible relay**, configured by `OPENROUTER_API_KEY` +
`OPENROUTER_API_BASE` in `.env` (the same pair the `openrouter` model backend uses). Adapt:

1. In the script, replace any hardcoded `base_url = "https://openrouter.ai/api/v1"` with
   `os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1").rstrip("/")` so
   calls go through the relay. Keep `OPENROUTER_API_KEY` — it is already our convention.
2. Add an `## Environment (AgentEvolver)` note under the title pointing at the `.env` pair and
   warning that the named models must be reachable on the relay (`/v1/models`; note the relay
   blocks some model families).

Applies to: `infographics` (done), `generate_image`, `latex_posters`, `scientific_slides`,
`scientific_schematics`, `scientific_critical_thinking`, `research_grants`, and
`neuropixels_analysis` (uses `ANTHROPIC_API_KEY` — route to the relay the same way, or the
framework's `model_manager`).

## Pattern B — a third-party service with its own key

Skills that talk to an external SaaS (Benchling, Exa, Zotero, protocols.io, Parallel, Rowan,
Tamarind, HuggingFace, …). The key is that service's own credential — no relay applies. Adapt:

1. Confirm the script reads the key from the environment (they already do) — do not hardcode it.
2. Add the `## Environment (AgentEvolver)` note listing the required var(s) and saying to put
   them in `.env`. The `openclaw` metadata block stays as the machine-readable declaration.

Applies to: `exa_search` (done), `benchling_integration` (done), `genomic_intelligence`,
`protocolsio_integration`, `parallel_web`, `research_lookup`, `paperclip`, `pyzotero`, `rowan`,
`tamarind`, `waypoint_bio`, `citation_management`, `open_notebook`, `omero_integration`,
`modal`, `autoskill`, `bioservices`, `biopython` (the last few have only optional/soft keys).
