import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, RefreshCw, SquareTerminal } from 'lucide-react';

import { Button } from '../components/ui/button';
import type { RequestFn } from '../canvas/types';

/** Keep-alive cadence. The manager also refreshes the idle clock on every
 *  proxied request, so this only matters while the IDE sits untouched. */
const HEARTBEAT_MS = 60_000;

interface IdeStatus { running: boolean; origin?: string }

/** Full VS Code for the current session, embedded in an iframe.
 *
 * The IDE is served at the ROOT of its own per-session host
 * (`<session>.ide.localhost:<ui port>`) because VS Code emits absolute asset
 * paths — see agentevolver/ide/README.md. The container starts lazily on first
 * open and is reaped once idle, so mounting this view is what boots it. */
export function IdeView({ request, sessionId, connected }: {
  request: RequestFn;
  sessionId?: string;
  connected: boolean;
}) {
  const [origin, setOrigin] = useState<string>();
  const [error, setError] = useState<string>();
  const [starting, setStarting] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const start = useCallback(async () => {
    if (!sessionId || !connected) return;
    setStarting(true);
    setError(undefined);
    try {
      const response = await request('ide.start', { session_id: sessionId });
      if (!response.ok) throw new Error(response.error?.message ?? 'Could not start the IDE');
      const status = response.result as unknown as IdeStatus;
      if (!status.origin) throw new Error('The gateway did not return an IDE address');
      setOrigin(status.origin);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setStarting(false);
    }
  }, [request, sessionId, connected]);

  // Boot on mount / session switch. The first call pulls or builds a ~600MB
  // image, so this can take a while before the iframe appears.
  useEffect(() => { setOrigin(undefined); void start(); }, [start]);

  // Heartbeat so an open-but-idle IDE is not reaped underneath the user.
  const originRef = useRef<string | undefined>(undefined);
  originRef.current = origin;
  useEffect(() => {
    if (!sessionId || !connected) return;
    const timer = window.setInterval(() => {
      if (originRef.current) void request('ide.status', { session_id: sessionId });
    }, HEARTBEAT_MS);
    return () => window.clearInterval(timer);
  }, [request, sessionId, connected]);

  if (!sessionId) return <IdeNotice title="No session" detail="Open or create a session to use the editor." />;
  if (error) {
    return (
      <IdeNotice title="The IDE could not start" detail={error}>
        <Button size="md" className="font-normal" onClick={() => void start()}>Try again</Button>
      </IdeNotice>
    );
  }
  if (!origin || starting) {
    return (
      <IdeNotice
        title="Starting VS Code…"
        detail="Booting this session's editor container. The first launch also builds the image, which can take a few minutes."
        spinning
      />
    );
  }

  // ?folder= opens the mounted workspace — the same files the agent edits.
  const src = `${window.location.protocol}//${origin}/?folder=/workspace`;
  return (
    <div className="ide-view">
      <header className="ide-toolbar">
        <SquareTerminal size={15} strokeWidth={1.9} />
        <strong>VS Code</strong>
        <code className="ide-origin" title="This session's IDE host">{origin}</code>
        <span className="ide-toolbar-spacer" />
        <Button variant="ghost" size="md" className="font-normal" onClick={() => setReloadKey((key) => key + 1)}>
          <RefreshCw /> Reload
        </Button>
      </header>
      <iframe
        key={reloadKey}
        className="ide-frame"
        title="VS Code"
        src={src}
        // No sandbox attribute: VS Code's webviews and service worker need
        // same-origin scripting, and this frame is our own trusted container.
        allow="clipboard-read; clipboard-write"
      />
    </div>
  );
}

function IdeNotice({ title, detail, spinning, children }: {
  title: string; detail: string; spinning?: boolean; children?: React.ReactNode;
}) {
  return (
    <div className="ide-notice">
      {spinning ? <Loader2 className="ide-spinner" /> : null}
      <strong>{title}</strong>
      <p>{detail}</p>
      {children}
    </div>
  );
}
