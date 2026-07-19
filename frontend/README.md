# AgentEvolver Web UI

React/Vite browser interface for the Python AgentEvolver Gateway. The main view provides a task composer, live agent activity timeline, task cancellation, event inspector, automatic reconnect, and event replay.

## Start locally

In one terminal, start the backend Gateway:

```bash
conda activate agentos
cd /path/to/AgentEvolver
agentevolver serve --transport websocket --host 127.0.0.1 --port 9876
```

In a second terminal, start the Web UI:

```bash
cd /path/to/AgentEvolver/frontend
npm install
npm run dev
```

Open the URL printed by Vite (normally `http://127.0.0.1:5173`). It connects to `ws://127.0.0.1:9876/ws` by default. Use **Connection** in the sidebar to change the endpoint or provide `AGENTEVOLVER_GATEWAY_TOKEN` when the Gateway requires one.

The directory from which `agentevolver serve` is launched is the server-controlled
workspace source. Every browser or terminal session imports it into its own
`<project_root>/<session>/workspace` using copy-on-write semantics. Agents never receive
the host source path as a writable workspace; `.git` and source files are retained, while
`output`, dependency directories, virtual environments, and caches are excluded. This is
the same isolation model used by local CLI runs.

## Terminal alternative

The original Ink terminal client remains available when needed:

```bash
npm run dev:terminal -- --workspace ..
```
