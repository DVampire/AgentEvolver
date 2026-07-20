import { defineConfig } from 'vite';

// Minimal config: raise the JS target so top-level await (used by @novnc/novnc,
// loaded lazily by the live environment view) is allowed. Everything else stays
// on Vite defaults.
export default defineConfig({
  esbuild: { target: 'es2022' },
  build: { target: 'es2022' },
  optimizeDeps: { esbuildOptions: { target: 'es2022' } },
});
