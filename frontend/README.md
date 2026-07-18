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

## Terminal alternative

The original Ink terminal client remains available when needed:

```bash
npm run dev:terminal -- --workspace ..
```
