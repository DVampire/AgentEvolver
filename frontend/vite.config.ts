import { defineConfig } from 'vite';

// The Vite dev server is the single reverse proxy: the browser only ever talks
// to it (one forwarded port for remote access). It relays the app's control
// socket (/ws), the VNC live view (/env/vnc), and the health probe (/health) to
// the gateway on 9876. The gateway in turn relays /env/vnc to the sandbox's
// ephemeral websockify port, so that port never needs forwarding.
const GATEWAY = process.env.GATEWAY_PORT || '9876';

export default defineConfig({
  esbuild: { target: 'es2022' },
  build: { target: 'es2022' },
  optimizeDeps: { esbuildOptions: { target: 'es2022' } },
  server: {
    proxy: {
      '/ws': { target: `ws://127.0.0.1:${GATEWAY}`, ws: true, changeOrigin: true },
      '/env/vnc': { target: `ws://127.0.0.1:${GATEWAY}`, ws: true, changeOrigin: true },
      '/health': { target: `http://127.0.0.1:${GATEWAY}` },
    },
  },
});
