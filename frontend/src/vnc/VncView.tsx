import { useEffect, useRef, useState } from 'react';
// noVNC is heavy; this module is loaded lazily (see EnvironmentLive) so it stays
// out of the main bundle until a live VNC view actually appears.
import RFB from '@novnc/novnc';

// noVNC exposes these documented properties at runtime, but its bundled declarations
// currently omit them. Keep the compatibility cast local to this adapter.
type BrowserRFB = RFB & {
  focus?: () => void;
  resizeSession: boolean;
  qualityLevel: number;
  compressionLevel: number;
};
const focusRfb = (rfb: RFB | null | undefined) => {
  try { (rfb as BrowserRFB | null)?.focus?.(); } catch { /* canvas not ready */ }
};

/**
 * Render a live VNC stream (RFB over WebSocket) onto a canvas noVNC manages.
 * Frames flow browser ↔ gateway relay ↔ websockify.
 *
 * Two modes:
 *  - watch (default): viewOnly, so you only observe what the agent does;
 *  - interactive: mouse + keyboard are sent to the container, so you can drive it
 *    yourself. You and the agent share one cursor, so take over only when it is idle.
 *
 * Neither the toggle nor the connection state lives here any more: both belong to the
 * window around the stream, which is where a person looks for a window's controls. This
 * component renders pixels and reports what it knows.
 */
export default function VncView({ url, password, interactive = false, onStatus }: {
  url: string;
  password?: string | null;
  interactive?: boolean;
  onStatus?: (status: 'connecting' | 'connected' | 'disconnected') => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RFB | null>(null);
  const [status, setStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');
  useEffect(() => { onStatus?.(status); }, [status, onStatus]);

  useEffect(() => {
    const target = containerRef.current;
    if (!target) return;
    setStatus('connecting');
    let rfb: RFB | undefined;
    try {
      rfb = new RFB(target, url, password ? { credentials: { password } } : undefined);
      // Ask the far side to match the window instead of stretching its picture to fit.
      // A 1280x800 desktop shown in a 1600px window — wider still on a HiDPI screen — is
      // upscaled by nearly two, and every glyph goes soft. With RANDR on the X server and
      // a client that requests it, the remote reshapes to the viewport and the pixels are
      // 1:1. `scaleViewport` stays on as the fallback for servers that refuse.
      const browserRfb = rfb as BrowserRFB;
      browserRfb.resizeSession = true;
      rfb.scaleViewport = true;
      rfb.clipViewport = false;
      // Tight encoding's JPEG quality (0-9, default 6) and how hard it compresses. This
      // is a container on the same host reached over loopback, so the bandwidth those
      // defaults protect is not scarce — spending it on a legible screen is the trade
      // worth making here.
      browserRfb.qualityLevel = 9;
      browserRfb.compressionLevel = 2;
      rfb.viewOnly = !interactive;   // start read-only unless the user took over
      rfb.addEventListener('connect', () => setStatus('connected'));
      rfb.addEventListener('disconnect', () => setStatus('disconnected'));
      rfbRef.current = rfb;
    } catch {
      setStatus('disconnected');
    }
    return () => { rfbRef.current = null; try { rfb?.disconnect(); } catch { /* already gone */ } };
    // Reconnect only on url/password change — the mode toggle is applied live below.
  }, [url, password]);

  // Apply the mode to the live connection without reconnecting.
  useEffect(() => {
    const rfb = rfbRef.current;
    if (!rfb) return;
    rfb.viewOnly = !interactive;
    if (interactive && status === 'connected') {
      focusRfb(rfb);
    }
  }, [interactive, status]);

  return (
    <div className="vnc-view">
      <div
        className={`vnc-canvas${interactive ? ' interactive' : ''}`}
        ref={containerRef}
        onMouseEnter={() => { if (interactive) focusRfb(rfbRef.current); }}
      />
      {status !== 'connected'
        ? <div className="vnc-overlay">{status === 'connecting' ? 'Connecting to live view…' : 'Live view disconnected'}</div>
        : null}
    </div>
  );
}
