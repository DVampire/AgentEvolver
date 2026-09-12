# Connector

This reference defines the type's artifact contract and specific checks. Follow the
[shared lifecycle](../../SKILL.md) and [conventions](../conventions.md) for inspection,
staging, registration, failure repair, evaluation, adoption and actual consumer use.

## What it is

A connector is a directory, `{extension_root}/connector/{name}/`, holding `CONNECTOR.md`:
frontmatter declaring how to reach an MCP server and which of its actions are exposed.

**The contract**: the declared `actions` match what the live server actually offers. An action
listed but absent, or present but undocumented, is the defect this type has.

Connectors are the framework's bridge to MCP (Model Context Protocol) servers. A connector is a `CONNECTOR.md` that points at an MCP server and declares which of its actions (tools) the framework exposes. This skill covers **creating**, **improving**, and **evaluating** connectors, and — for the case where no server exists yet — **building** an MCP server to connect to (adapted from Anthropic's `mcp-builder`).

Two directions, don't confuse them:
- **A connector CONSUMES an MCP server** — a client-side config (`CONNECTOR.md`: a URL/transport + a list of actions). This is the common case (e.g. connecting to a hosted server like PubMed).
- **An MCP server EXPOSES tools** — server-side code. You only build one when you need to wrap your own API as MCP; then you still write a `CONNECTOR.md` to connect to it.

## Layout

- Connectors live in `{extension_root}/connector/{connector_name}/` (generated) or `agentevolver/connector/default/{connector_name}/` (defaults).
- A connector directory:
  ```
  {connector_name}/
  ├── CONNECTOR.md    # REQUIRED — YAML frontmatter (connection + actions) + markdown body (module intro + per-action docs)
  ├── server.py       # for a local, agent-authored Python MCP server
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
- **Registration is a call you make**: after writing/editing the files, call `adoption_tool` with `action="register"`, `module="connector"`, the connector name, and its directory as `artifact_path`.

`permission_mode: read_only` restricts execution; it does not declare each action read-only.
Check the server's MCP `ToolAnnotations` during discovery. Declare actual `readOnlyHint`,
`destructiveHint`, `idempotentHint` and `openWorldHint` in the server, or document verified
effects under frontmatter `action_annotations` when authoring a connector for a known service.
Inspect the loaded contract and exercise a native action. Missing effects are not read-only;
a downloader that writes snapshot files has a write effect even if its HTTP request is GET.
Use the appropriate permitted workspace scope and accurate declarations, not false read-only
hints or broader permissions to silence an error.

### Large read results saved locally

For a read-only remote query that returns a dataset, set `result_mode: artifact` in
CONNECTOR.md (the default is `inline`). Return a JSON object from the MCP method. The
framework saves the complete result atomically under the session's connector log directory
and returns a compact receipt: `artifact_path`, `sha256`, `bytes`, `result_key: result`.
The saved JSON envelope contains `connector`, `version`, `action` and `result`; a text-only
response remains a string in `result`. Read that artifact using Bash, verify the hash and
normalize/copy it into the workspace if needed. The log root is available to workspace tools.
No provider-chosen filesystem destination is accepted by this persistence mode. Errors
remain failed calls and do not produce successful dataset receipts.

The server must actually perform reads only for `readOnlyHint: true`: no arbitrary
`output_dir`, cache writes or remote mutations. A public HTTP GET has `openWorldHint: true`;
that does not make it a remote write. Framework result persistence is separate from the
server operation, like trace logging. Never relabel a file-writing server as read-only.
For mixed or uncertain effects, use an approval-capable entry point and correct declarations.
The interactive CLI can request one-call approval; detached CLI runs have no live terminal
approval channel and return an explicit failure rather than silently waiting for input.

---

## Writing a new one

### Creating a connector

**Start from the template**: read `references/connector/template-manifest.md`, copy it to the connector directory, and fill in the connection + actions.

The common case: an MCP server already exists (hosted, or someone gives you a URL/command) and you write a `CONNECTOR.md` for it.

#### 1. Identify the server and how to reach it

From the task, determine the connection: `transport` (`streamable_http` / `sse` for a URL endpoint, `stdio` for a local command) and the `url` (or `command` + `args` for stdio).

#### 2. Discover the server's actions

Connect to the server and list the tools it exposes — don't guess. Use the bundled probe:
```bash
python {skill_dir}/scripts/connector/probe.py <transport> <url-or-command>
# For a local server written by the agent:
python {skill_dir}/scripts/connector/probe.py stdio python /absolute/path/server.py
```
`scripts/connector/probe.py` is a lightweight MCP client (stdio/sse/streamable_http) that opens a session and lets you enumerate the server's tools and their input schemas. Record the action names and argument schemas — these become the `actions` list and the per-action docs.

#### 3. Design the action surface

Follow the MCP tool-design principles in `references/connector/mcp-best-practices.md`:
- Prefer clear, action-oriented names; keep descriptions concise.
- Expose the actions that let an agent accomplish real tasks; you don't have to surface every raw endpoint.
- Note filtering/pagination so agents can keep results focused.

#### 4. Write CONNECTOR.md

Fill the frontmatter (`connection` + `actions`) and write the body: a one-paragraph module intro, then a section per action with **what it does**, **when to use it**, and **arguments** (from the discovered schema). Keep the description (frontmatter) both what-it-does and when-to-use, a little pushy so agents reach for it.

Then register it: `adoption_tool` with `action="register"`, `module="connector"` and the connector directory as `artifact_path`.

#### If the server doesn't exist yet

If the task requires an operation that has no MCP server, **Build an MCP server** (next section)
in the connector directory and use stdio. The connector manager starts the process on demand;
the user does not need to supply or deploy a remote MCP service. It can wrap a suitable SDK,
public-data client or authenticated API. Upstream access and credentials depend on that source,
not on MCP itself. Probe the local server, register the directory and exercise its native actions.

For execution failures, raise a server tool error or return `CallToolResult(isError=True, ...)`.
A normal text block containing an error message or JSON `ok:false` still declares MCP success.
Test the successful operation and a failed request through the registered connector; an
inspection that reports missing credentials cannot substitute for a working data download.

---

### Building an MCP server

*(Adapted from Anthropic's mcp-builder. Only needed when no server exists to connect to.)*

Creating a high-quality MCP server is a four-phase process. The quality of a server is measured by how well it lets an agent accomplish real-world tasks.

#### Phase 1 — Research & planning
- Understand modern MCP design: balance comprehensive API coverage with focused workflow tools; use clear, prefixed, action-oriented tool names; return concise, filterable results; write actionable error messages. See `references/connector/mcp-best-practices.md`.
- Study the MCP spec (start at `https://modelcontextprotocol.io/sitemap.xml`; fetch pages with a `.md` suffix) and the framework docs for your language.
- Plan the tools before writing code.

#### Phase 2 — Implementation
- **Python (FastMCP)**: follow `references/connector/python-server.md`.
- **Node/TypeScript (MCP SDK)**: follow `references/connector/node-server.md`.
- Set up the project, core infrastructure, then implement the tools with clear schemas and error handling.

#### Phase 3 — Review & test
- Check code quality; build and run the server; connect to it with `scripts/connector/probe.py` and confirm the tools list and behave as intended.

#### Phase 4 — Evaluations
- Apply the common evaluation scope and the native-action checks below. Use
  [evaluation.md](evaluation.md) for connector-specific cases; there is no fixed question
  quota or required multi-agent evaluation harness.

Once the server runs, write a `CONNECTOR.md` for it (see **Creating a connector**).

---

## Improving an existing one

Read the manifest, discovered schemas/effects and failed native calls. For an authored local
server, also read `server.py` and its dependencies; fixing a Connector can require code,
not just documentation. Diagnose transport/startup, parsing, provider access, action coverage
or response semantics from the concrete result. Keep connection paths portable and manifests
aligned with the live server. Stage and register the whole directory through the shared loop,
then repeat discovery and the failed action for the returned candidate version.

When invocation misuse is the defect, clarify the action's arguments and purpose. Keep the
description specific enough for appropriate selection; adding every provider endpoint or
broad trigger wording is not automatically an improvement.

## Evaluating one

Goal: measure whether the connector's actions actually let an agent accomplish tasks — empirically, not just by reading the docs.

### Static check (always)

Read the `CONNECTOR.md` and score it: are the required frontmatter fields present (`connection`, `actions`); is each action documented with purpose + arguments; does the description state what-it-does and when-to-use. Use `inspect_tool` (`capability_type="connector"`) to confirm the connector is registered and to get its connection, actions, and directory. Validate structure with:
```bash
python {skill_dir}/scripts/connector/validate.py <connector_dir>
```

### Connection check

Confirm the server is reachable and exposes the declared actions — connect with `scripts/connector/probe.py` and compare the live tool list against the `actions` in the frontmatter. Flag missing or undocumented actions.

### Empirical check (with-connector vs baseline)

Invoke the registered connector's native actions on a valid request and an expected failure,
then a different reuse/regression case. Compare outputs, provenance, schema consistency and
cost with the prior version or existing method. Discovery alone is not execution, and a
successful metadata/credential inspection cannot replace the required data operation.

The current agent can execute this comparison directly. If the claim concerns model tool
selection or reasoning, use the common fresh-consumer rules; no particular MetaAgent or
`general_agent` is assumed. See [evaluation.md](evaluation.md) for transport and service
checks. Submit the common version-scoped decision and retry repaired candidates through the
same lifecycle.
