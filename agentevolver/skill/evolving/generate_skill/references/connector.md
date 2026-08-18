# Writing a connector

## What it is

A connector is a directory, `{extension_root}/connector/{name}/`, holding `CONNECTOR.md`:
frontmatter declaring how to reach an MCP server and which of its actions are exposed.

**The contract**: the declared `actions` match what the live server actually offers. An action
listed but absent, or present but undocumented, is the defect this type has.

A single skill for the full lifecycle of **connectors** — the framework's bridge to MCP (Model Context Protocol) servers. A connector is a `CONNECTOR.md` that points at an MCP server and declares which of its actions (tools) the framework exposes. This skill covers **creating**, **improving**, and **evaluating** connectors, and — for the case where no server exists yet — **building** an MCP server to connect to (adapted from Anthropic's `mcp-builder`).

Two directions, don't confuse them:
- **A connector CONSUMES an MCP server** — a client-side config (`CONNECTOR.md`: a URL/transport + a list of actions). This is the common case (e.g. connecting to a hosted server like PubMed).
- **An MCP server EXPOSES tools** — server-side code. You only build one when you need to wrap your own API as MCP; then you still write a `CONNECTOR.md` to connect to it.

## Framework conventions (read once)

- Connectors live in `{extension_root}/connector/{connector_name}/` (generated) or `agentevolver/connector/default/{connector_name}/` (defaults).
- A connector directory:
  ```
  {connector_name}/
  ├── CONNECTOR.md    # REQUIRED — YAML frontmatter (connection + actions) + markdown body (module intro + per-action docs)
  └── references/       # optional — extra docs the agent READs as needed
  ```
- **Naming**: the frontmatter `name` (registry key) follows the `<directory>_connector` convention — directory `pubmed` → `name: pubmed_connector`. Keep it snake_case.
- **Portable stdio connections**: never hard-code machine-specific absolute paths. Use `command: python` and a **relative** script path in `args` (e.g. `server.py`, relative to the connector directory). The connector manager resolves these at load time — `command` → the running interpreter (`sys.executable`), and the relative `*.py` → an absolute path under the connector directory — so the same `CONNECTOR.md` works on any machine/checkout/env. (`streamable_http`/`sse` connectors carry only a `url` and need no paths.)
- **CONNECTOR.md frontmatter** — required: `name`, `description`, `version`, `type`, plus `connection` and `actions`:
  ```yaml
  ---
  name: pubmed_connector
  description: PubMed — search biomedical literature, fetch article metadata and full text.
  version: 1.0.0
  type: worker
  permission_mode: read_only
  connection:
    transport: streamable_http        # or: stdio | sse
    url: https://pubmed.mcp.claude.com/mcp
    # for a local stdio server instead of url:
    # transport: stdio
    # command: python                 # resolved to sys.executable
    # args:
    #   - server.py                    # relative to this connector dir; resolved to an absolute path
  actions:
    - search_articles
    - get_article_metadata
    - get_full_text_article
  ---
  ```
  The **body** below the frontmatter is a short module intro plus a per-action section documenting what each action does and its arguments — this is what an agent reads to call the connector.
- **Registration is automatic via a hook**: after writing/editing the files, include the connector directory path in your `done_tool` reasoning — the registration hook picks it up.

---

## Creating a connector

**Start from the template**: read `references/connector/connector_md_template.md`, copy it to the connector directory, and fill in the connection + actions.

The common case: an MCP server already exists (hosted, or someone gives you a URL/command) and you write a `CONNECTOR.md` for it.

### 1. Identify the server and how to reach it

From the task, determine the connection: `transport` (`streamable_http` / `sse` for a URL endpoint, `stdio` for a local command) and the `url` (or `command` + `args` for stdio).

### 2. Discover the server's actions

Connect to the server and list the tools it exposes — don't guess. Use the bundled probe:
```bash
python {skill_dir}/scripts/connector/connections.py <transport> <url-or-command>
```
`scripts/connector/connections.py` is a lightweight MCP client (stdio/sse/streamable_http) that opens a session and lets you enumerate the server's tools and their input schemas. Record the action names and argument schemas — these become the `actions` list and the per-action docs.

### 3. Design the action surface

Follow the MCP tool-design principles in `references/connector/mcp_best_practices.md`:
- Prefer clear, action-oriented names; keep descriptions concise.
- Expose the actions that let an agent accomplish real tasks; you don't have to surface every raw endpoint.
- Note filtering/pagination so agents can keep results focused.

### 4. Write CONNECTOR.md

Fill the frontmatter (`connection` + `actions`) and write the body: a one-paragraph module intro, then a section per action with **what it does**, **when to use it**, and **arguments** (from the discovered schema). Keep the description (frontmatter) both what-it-does and when-to-use, a little pushy so agents reach for it.

Then put the connector directory path in your `done_tool` reasoning so the registration hook installs it.

### If the server doesn't exist yet

If the task requires wrapping an API that has no MCP server, first **Build an MCP server** (next section), host/run it, then come back and write the `CONNECTOR.md` pointing at it.

---

## Building an MCP server

*(Adapted from Anthropic's mcp-builder. Only needed when no server exists to connect to.)*

Creating a high-quality MCP server is a four-phase process. The quality of a server is measured by how well it lets an agent accomplish real-world tasks.

### Phase 1 — Research & planning
- Understand modern MCP design: balance comprehensive API coverage with focused workflow tools; use clear, prefixed, action-oriented tool names; return concise, filterable results; write actionable error messages. See `references/connector/mcp_best_practices.md`.
- Study the MCP spec (start at `https://modelcontextprotocol.io/sitemap.xml`; fetch pages with a `.md` suffix) and the framework docs for your language.
- Plan the tools before writing code.

### Phase 2 — Implementation
- **Python (FastMCP)**: follow `references/connector/python_mcp_server.md`.
- **Node/TypeScript (MCP SDK)**: follow `references/connector/node_mcp_server.md`.
- Set up the project, core infrastructure, then implement the tools with clear schemas and error handling.

### Phase 3 — Review & test
- Check code quality; build and run the server; connect to it with `scripts/connector/connections.py` and confirm the tools list and behave as intended.

### Phase 4 — Evaluations
- Create ~10 evaluation questions and measure the server (see **Evaluating a connector** and `references/connector/evaluation.md`).

Once the server runs, write a `CONNECTOR.md` for it (see **Creating a connector**).

---
