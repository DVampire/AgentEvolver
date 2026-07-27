import http from 'node:http';
import net from 'node:net';
import type { Plugin } from 'vite';

// Host-based routing for the browser IDE.
//
// VS Code emits ABSOLUTE asset paths (/stable-<commit>/static/...), and
// openvscode-server has no base-path option, so it cannot live under a
// sub-path. It gets the ROOT of its own host instead:
//
//   localhost:5173               -> the AgentEvolver SPA
//   <session>.ide.localhost:5173 -> that session's IDE, at "/"
//
// Matching on Host rather than path means every absolute asset URL hits this
// rule for free. `*.localhost` resolves to 127.0.0.1 with no DNS or /etc/hosts
// entry, so this costs no extra forwarded port — remote access still forwards
// only the UI port.
//
// Requests are forwarded to `<upstream>/proxy/3000<path>`; the opensandbox
// proxy strips that prefix again, so VS Code only ever sees root paths and
// never learns it is proxied.

const HOST_RE = /^([A-Za-z0-9_-]+)\.ide\.localhost(?::\d+)?$/;
const UPSTREAM_TTL_MS = 10_000;

const cache = new Map<string, { upstream: string; expires: number }>();

function sessionOf(host: string | undefined): string | null {
  const match = HOST_RE.exec(host ?? '');
  return match ? match[1] : null;
}

/** Ask the gateway which container serves this session, memoised briefly so a
 *  page load of ~100 assets does not trigger ~100 lookups. */
async function resolveUpstream(gatewayPort: string, sessionId: string): Promise<string | null> {
  const hit = cache.get(sessionId);
  if (hit && hit.expires > Date.now()) return hit.upstream;
  try {
    const response = await fetch(`http://127.0.0.1:${gatewayPort}/ide/resolve/${sessionId}`);
    if (!response.ok) { cache.delete(sessionId); return null; }
    const body = await response.json() as { upstream?: string };
    if (!body.upstream) return null;
    cache.set(sessionId, { upstream: body.upstream, expires: Date.now() + UPSTREAM_TTL_MS });
    return body.upstream;
  } catch {
    return null;
  }
}

/** Split `http://host:port/proxy/3000` into the connection target plus the
 *  path prefix every forwarded request must carry. */
function parseUpstream(upstream: string): { host: string; port: number; prefix: string } {
  const url = new URL(upstream);
  return {
    host: url.hostname,
    port: Number(url.port || 80),
    prefix: url.pathname.replace(/\/$/, ''),
  };
}

export function ideHostProxy(gatewayPort: string): Plugin {
  return {
    name: 'agentevolver-ide-host-proxy',
    configureServer(server) {
      // Registered directly (not in a returned thunk) so it runs BEFORE Vite's
      // own middlewares — including the host check, which would otherwise
      // reject these hosts before we ever see them.
      server.middlewares.use((req, res, next) => {
        const sessionId = sessionOf(req.headers.host);
        if (!sessionId) return next();
        void (async () => {
          const upstream = await resolveUpstream(gatewayPort, sessionId);
          if (!upstream) {
            res.statusCode = 503;
            res.setHeader('content-type', 'text/plain');
            res.end('IDE is not running for this session.');
            return;
          }
          const { host, port, prefix } = parseUpstream(upstream);
          const proxyReq = http.request({
            host, port, method: req.method,
            path: prefix + (req.url ?? '/'),
            headers: { ...req.headers, host: `${host}:${port}` },
          }, (proxyRes) => {
            res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
            proxyRes.pipe(res);
          });
          proxyReq.on('error', () => {
            if (!res.headersSent) res.statusCode = 502;
            res.end('IDE upstream unreachable.');
          });
          req.pipe(proxyReq);
        })();
      });

      // The workbench rides a WebSocket on the same host and port, so the
      // upgrade needs the same routing. Vite has its own HMR upgrade listener,
      // so only claim sockets whose Host is an IDE host and leave the rest.
      server.httpServer?.on('upgrade', (req, socket: net.Socket, head) => {
        const sessionId = sessionOf(req.headers.host);
        if (!sessionId) return;
        void (async () => {
          const upstream = await resolveUpstream(gatewayPort, sessionId);
          if (!upstream) { socket.destroy(); return; }
          const { host, port, prefix } = parseUpstream(upstream);
          const proxyReq = http.request({
            host, port, method: 'GET',
            path: prefix + (req.url ?? '/'),
            headers: { ...req.headers, host: `${host}:${port}` },
          });
          proxyReq.on('upgrade', (proxyRes, upstreamSocket, upstreamHead) => {
            const headers = Object.entries(proxyRes.headers)
              .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : value}`)
              .join('\r\n');
            socket.write(`HTTP/1.1 101 Switching Protocols\r\n${headers}\r\n\r\n`);
            if (upstreamHead?.length) socket.unshift(upstreamHead);
            upstreamSocket.pipe(socket).pipe(upstreamSocket);
          });
          proxyReq.on('error', () => socket.destroy());
          if (head?.length) proxyReq.write(head);
          proxyReq.end();
        })();
      });
    },
  };
}
