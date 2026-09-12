# AgentEvolver Web UI

React/Vite browser interface for the Python AgentEvolver Gateway. The main view provides a task composer, live agent activity timeline, task cancellation, event inspector, automatic reconnect, and event replay.

## Start in the sandbox (Model X)

Under Model X both the backend Gateway and this Vite dev server run **inside the base
sandbox** — one container, two processes — started together by `scripts/serve-ui.sh`.
From the repo root on the host:

```bash
scripts/run-in-sandbox.sh -- scripts/serve-ui.sh
```

Open `http://127.0.0.1:5173` in the host browser (the sandbox uses `--network host`, so
the container loopback is the host loopback). It connects to `ws://127.0.0.1:9876/ws` by
default. The first launch runs `npm install` inside the sandbox; later launches skip it.
Override ports with `GATEWAY_PORT` / `UI_PORT`; args after the script pass through to
`agentevolver serve`. Use **Connection** in the sidebar to change the endpoint or provide
`AGENTEVOLVER_GATEWAY_TOKEN` when the Gateway requires one.

## Start locally (no sandbox)

For quick local development without a container, run the two pieces by hand. In one
terminal, start the backend Gateway:

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

Every browser or terminal session gets its own initially empty
`<project_root>/<session>/workspace`. Only staged inputs and files produced during that
Session appear there; the host checkout is never copied into the Agent workspace.

## Terminal alternative

The original Ink terminal client remains available when needed:

```bash
npm run dev:terminal -- --workspace ..
```

## Visual design

`src/style/theme.css` owns the shared color, surface, typography and shadow tokens.
The default dark theme follows `agentevolver/visual` (deep green, mint, and the same
amber/red/blue/purple status colors); an explicit saved light preference is preserved.
Light mode uses green-tinted neutrals and darker accents for readable contrast.
Tailwind/Radix components, the app shell, canvas and Science panels all consume these
tokens, including dialogs rendered outside the app shell. Borders use opaque HSL
channels so Tailwind opacity modifiers remain valid. Monaco uses matching editor
palettes in `src/workspace/WorkspaceEditor.tsx` because its theme API needs hex colors.
Keep layout rules in each view's stylesheet and use semantic tokens when adding UI.
Embedded websites, remote desktops and the external IDE retain their own appearance.
