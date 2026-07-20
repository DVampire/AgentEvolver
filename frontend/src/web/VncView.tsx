import { useEffect, useRef, useState } from 'react';
// noVNC is heavy; this module is loaded lazily (see EnvironmentLive) so it stays
// out of the main bundle until a live VNC view actually appears.
import RFB from '@novnc/novnc';

/**
 * Render a live VNC stream (RFB over WebSocket) onto a canvas noVNC manages.
 * Pixels flow browser ↔ websockify directly — never through the agent/gateway.
 */
export default function VncView({ url, password }: { url: string; password?: string | null }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');

  useEffect(() => {
    const target = containerRef.current;
    if (!target) return;
    setStatus('connecting');
    let rfb: RFB | undefined;
    try {
      rfb = new RFB(target, url, password ? { credentials: { password } } : undefined);
      rfb.scaleViewport = true;      // fit the stream to the card
      rfb.clipViewport = false;
      rfb.viewOnly = true;           // a live view, not a control surface
      rfb.addEventListener('connect', () => setStatus('connected'));
      rfb.addEventListener('disconnect', () => setStatus('disconnected'));
    } catch {
      setStatus('disconnected');
    }
    return () => { try { rfb?.disconnect(); } catch { /* already gone */ } };
  }, [url, password]);

  return (
    <div className="vnc-view">
      <div className="vnc-canvas" ref={containerRef} />
      {status !== 'connected'
        ? <div className="vnc-overlay">{status === 'connecting' ? 'Connecting to live view…' : 'Live view disconnected'}</div>
        : null}
    </div>
  );
}
