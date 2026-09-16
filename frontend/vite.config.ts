import { defineConfig } from 'vite';

import { containerProxy } from './container-proxy';

// The Vite dev server is the single reverse proxy: the browser only ever talks
// to it (one origin for remote access). It relays the app's control socket
// (/ws), the VNC live view (/env/vnc), the per-session containers
// (/ide/<session>/ and /science/<session>/ — see container-proxy.ts), and the
// health probe (/health) to the gateway on 9876. The
// gateway in turn relays /env/vnc to the sandbox's ephemeral websockify port and
// /env/term/<token>/ to the ttyd an ssh tunnel put on its loopback, so neither of
// those ports ever needs forwarding.
const GATEWAY = process.env.GATEWAY_PORT || '9876';

export default defineConfig({
  esbuild: { target: 'es2022' },
  build: { target: 'es2022' },
  optimizeDeps: { esbuildOptions: { target: 'es2022' } },
  plugins: [containerProxy(GATEWAY)],
  server: {
    // Vite rejects unknown Host headers; the IDE deliberately uses a
    // per-session host, so allow that suffix. Tailscale (serve / funnel) forwards
    // the tailnet hostname verbatim, so allow that suffix too — otherwise remote
    // access gets "Blocked request" instead of the app.
    allowedHosts: ['.ide.localhost', '.ts.net'],
    // This shared host's inotify instance limit (fs.inotify.max_user_instances,
    // 128) is exhausted by other containers, so native fs.watch throws EMFILE.
    // Poll instead (no inotify), and ignore trees the UI never imports so polling
    // stays cheap (only frontend/src is walked).
    watch: {
      usePolling: true,
      interval: 300,
      ignored: ['**/node_modules/**', '**/.git/**', '**/dist/**', '**/output/**', '**/__pycache__/**', '**/extension/**'],
    },
    proxy: {
      '/ws': { target: `ws://127.0.0.1:${GATEWAY}`, ws: true, changeOrigin: true },
      '/env/vnc': { target: `ws://127.0.0.1:${GATEWAY}`, ws: true, changeOrigin: true },
      // The SSH terminals. Both halves through one entry: ttyd serves its page over
      // HTTP and its stream over a WebSocket under the same prefix, and `ws: true`
      // makes this proxy handle the upgrade as well as the ordinary requests. Without
      // it the iframe's relative URL resolved against the Vite origin, hit the SPA's
      // index.html fallback, and rendered a terminal-shaped page that never connected.
      '/env/term': { target: `http://127.0.0.1:${GATEWAY}`, ws: true, changeOrigin: true },
      '/health': { target: `http://127.0.0.1:${GATEWAY}` },
    },
  },
});
