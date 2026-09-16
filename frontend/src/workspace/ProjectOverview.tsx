import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Activity, ArrowUpRight, BookOpen, Brain, Cable, Check, ClipboardList, Code2, File, FlaskConical, Folder, GraduationCap, Layers, MessageSquare, Monitor, Package, RefreshCw, Sparkles, Waypoints, Workflow, Wrench } from 'lucide-react';
import type { RequestFn } from '../canvas/types';
import { MessageMarkdown } from '../components/common/Markdown';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../components/ui/dialog';
import '../style/overview.css';

export type WorkspaceView = 'overview' | 'chat' | 'canvas' | 'code' | 'science';
type EntityKind = 'tools' | 'skills' | 'agents' | 'workflows' | 'memories' | 'environments' | 'connectors' | 'plugins';
export interface OverviewCapability { kind: string; available: number; enabled: number; }
interface Document { path: string; exists: boolean; content: string; truncated: boolean; error?: string; }
interface OverviewDocuments {
  plan: { index: Document; document: Document };
  memory: { notes: Document[]; error?: string; truncated: boolean };
  plan_mode: { mode?: string; active: boolean };
  runtime: { active_tasks: number };
}
interface Stage { valid: boolean; error?: string; components: Array<{ module: string; relative_path: string }>; }
interface Entry { name: string; path: string; type: string; }
interface RuntimeAgent { name: string; status: string; jobId?: string; busy?: boolean; paused?: boolean; turns?: number; task?: string; }

const ENTITIES = [
  { kind: 'tools', label: 'Tool', icon: Wrench, description: 'Actions & utilities' },
  { kind: 'skills', label: 'Skill', icon: GraduationCap, description: 'Methods & expertise' },
  { kind: 'agents', label: 'Agent', icon: Sparkles, description: 'Roles & instructions' },
  { kind: 'workflows', label: 'Workflow', icon: Workflow, description: 'Reusable orchestration' },
  { kind: 'memories', label: 'Memory', icon: Brain, description: 'Knowledge & retrieval' },
  { kind: 'environments', label: 'Environment', icon: Monitor, description: 'Actions & observations' },
  { kind: 'connectors', label: 'Connector', icon: Cable, description: 'External connections' },
  { kind: 'plugins', label: 'Plugin', icon: Package, description: 'Packaged integrations' },
] as const;
const VIEWS = [
  { view: 'chat', label: 'Chat', description: 'Plan and work with an agent', icon: MessageSquare },
  { view: 'canvas', label: 'Canvas', description: 'Compose a visual workflow', icon: Waypoints },
  { view: 'code', label: 'Code', description: 'Open the project editor', icon: Code2 },
  { view: 'science', label: 'Science', description: 'Explore in a shared kernel', icon: FlaskConical },
] as const;

export function ProjectOverview({ request, sessionId, name, connected, activeTaskId, capabilities, agents, onView, onCapability, onAgent, onFile, onPromote }: {
  request: RequestFn; sessionId?: string; name: string; connected: boolean; activeTaskId?: string;
  capabilities: OverviewCapability[]; agents: RuntimeAgent[];
  onView: (view: WorkspaceView) => void;
  onCapability: (kind: Exclude<EntityKind, 'memories'>) => void;
  onAgent: (id: string) => void; onFile: (path: string) => void;
  onPromote: (stage: Stage) => Promise<void>;
}) {
  const [documents, setDocuments] = useState<OverviewDocuments>();
  const [stage, setStage] = useState<Stage>();
  const [entries, setEntries] = useState<Entry[]>();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [planTab, setPlanTab] = useState<'index' | 'document'>('index');
  const [showMemory, setShowMemory] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [openedDocument, setOpenedDocument] = useState<Document>();
  const [documentLoading, setDocumentLoading] = useState(false);
  const documentGeneration = useRef(0);
  const generation = useRef(0);
  const reload = useCallback(async () => {
    if (!connected || !sessionId) return;
    const current = ++generation.current;
    setLoading(true);
    const results = await Promise.allSettled([
      request('project.overview', { session_id: sessionId }),
      request('extension.stage.get', { session_id: sessionId }),
      request('workspace.tree', { session_id: sessionId }),
    ]);
    if (current !== generation.current) return;
    const nextErrors: Record<string, string> = {};
    results.forEach((response, index) => {
      const key = ['documents', 'stage', 'files'][index];
      if (response.status === 'rejected' || !response.value.ok) {
        nextErrors[key] = response.status === 'rejected' ? 'Could not reach the gateway.' : response.value.error?.message ?? 'Could not load this section.';
        if (index === 0) setDocuments(undefined);
        if (index === 1) setStage(undefined);
        if (index === 2) setEntries(undefined);
        return;
      }
      const result = response.value.result;
      if (index === 0) setDocuments(result as unknown as OverviewDocuments);
      if (index === 1) setStage(result.staging as Stage);
      if (index === 2) setEntries((result.entries ?? []) as Entry[]);
    });
    setErrors(nextErrors);
    setLoading(false);
  }, [request, sessionId, connected]);

  useEffect(() => {
    void reload();
    const timer = window.setInterval(() => { if (!document.hidden) void reload(); }, 15_000);
    return () => { generation.current++; window.clearInterval(timer); };
  }, [reload]);

  const runtimeAgents = agents.filter(agent => agent.status === 'running');
  const taskRunning = Boolean(activeTaskId) || Boolean(documents?.runtime?.active_tasks);
  const working = taskRunning || runtimeAgents.some(agent => agent.busy);
  const plan = documents?.plan;
  const selectedDocument = planTab === 'index' && !plan?.index.exists && !plan?.index.error ? plan?.document : plan?.[planTab];
  const mounted = capabilities.reduce((sum, item) => sum + item.enabled, 0);
  const notes = documents?.memory.notes ?? [];
  const runtimeStatus = !connected ? 'Disconnected' : working ? 'Working' : runtimeAgents.some(agent => agent.paused) ? 'Paused agents' : runtimeAgents.length ? 'Agents waiting' : documents ? 'No live task' : errors.documents ? 'Unavailable' : 'Loading…';

  const readLinkedDocument = async (href: string, source: string) => {
    const current = ++documentGeneration.current;
    setDocumentLoading(true);
    try {
      const path = decodeURIComponent(new URL(href, `https://project.invalid/${source}`).pathname.slice(1));
      const response = await request('project.document.read', { session_id: sessionId, path });
      if (current !== documentGeneration.current) return;
      setOpenedDocument(response.ok ? response.result as unknown as Document : { path, content: '', exists: false, truncated: false, error: response.error?.message || 'Could not read this document.' });
    } catch {
      if (current === documentGeneration.current) setOpenedDocument({ path: href, content: '', exists: false, truncated: false, error: 'Could not read this document.' });
    } finally {
      if (current === documentGeneration.current) setDocumentLoading(false);
    }
  };
  const planMarkdown = (file: Document) => <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a: ({ href, children }) => href && !/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(href)
      ? <button className="overview-document-link" onClick={() => void readLinkedDocument(href, file.path)}>{children}</button>
      : <a href={href} target="_blank" rel="noreferrer">{children}</a>,
  }}>{file.content || 'This document is empty.'}</ReactMarkdown>;

  return <div className="project-overview">
    <header className="overview-heading">
      <div><p className="eyebrow">Project overview</p><h1>{name || 'Your workspace'}</h1><p>Keep the plan, the work and the capabilities in view.</p></div>
      <div className="overview-actions"><button className="overview-icon-button" onClick={() => void reload()} disabled={!connected || loading} aria-label="Refresh project overview" title="Refresh project overview"><RefreshCw size={16} className={loading ? 'overview-spin' : ''} /></button><button className="overview-primary" onClick={() => onView('chat')}>Open Chat <ArrowUpRight size={15} /></button></div>
    </header>

    <div className="overview-stats" aria-label="Project status">
      <div><span><Activity size={14} /> Task execution</span><strong className={connected && working ? 'accent' : ''}>{runtimeStatus}</strong></div>
      <div><span><Layers size={14} /> Mounted capabilities</span><strong>{connected ? mounted : '—'}<small>in this session</small></strong></div>
      <div><span><Package size={14} /> Project candidates</span><strong>{connected && stage ? stage.components.length : '—'}<small>staged locally</small></strong></div>
      <div><span><ClipboardList size={14} /> Plan</span><strong>{!connected || !documents ? '—' : plan?.index.error || plan?.document.error ? 'Unreadable' : plan?.index.exists || plan?.document.exists ? 'Available' : 'Not created'}<small>{documents?.plan_mode.active ? 'approval mode' : 'task documents'}</small></strong></div>
    </div>

    <nav className="overview-views" aria-label="Project workspaces">{VIEWS.map(({ view, label, description, icon: Icon }) => <button key={view} onClick={() => onView(view)}><Icon size={20} strokeWidth={1.6} /><span><strong>{label}</strong><small>{description}</small></span><ArrowUpRight size={14} /></button>)}</nav>

    <div className="overview-work-grid">
      <section className="overview-panel overview-plan">
        <header><div><ClipboardList size={16} /><h2>Living plan</h2></div><span className="overview-label">{documents?.plan_mode.active ? 'Approval mode' : 'Plan & progress'}</span></header>
        <div className="overview-plan-tabs" role="tablist" aria-label="Plan documents"><button role="tab" id="plan-summary-tab" aria-controls="overview-plan-content" aria-selected={planTab === 'index'} onClick={() => setPlanTab('index')}>Summary</button><button role="tab" id="plan-details-tab" aria-controls="overview-plan-content" aria-selected={planTab === 'document'} onClick={() => setPlanTab('document')}>Detailed plan</button></div>
        <div className="overview-plan-body" id="overview-plan-content" role="tabpanel" aria-labelledby={planTab === 'index' ? 'plan-summary-tab' : 'plan-details-tab'} tabIndex={0}>
          {errors.documents ? <p className="overview-error">{errors.documents}</p> : selectedDocument?.error ? <p className="overview-error">{selectedDocument.error}</p> : selectedDocument?.exists ? <>{planMarkdown(selectedDocument)}{selectedDocument.truncated ? <p className="overview-muted">Showing a bounded preview of this document.</p> : null}</> : <div className="overview-empty"><ClipboardList size={24} strokeWidth={1.3} /><h3>{!connected ? 'Connect to read the plan' : !documents ? 'Loading plan…' : 'Give the work a direction'}</h3><p>The agent’s goal, progress and next steps appear here when it creates a plan.</p><button onClick={() => onView('chat')}>Plan a task in Chat <ArrowUpRight size={13} /></button></div>}
        </div>
        <footer><span>{selectedDocument?.exists ? selectedDocument.path : 'Summary · Progress · Evidence'}</span><span>Project document</span></footer>
      </section>

      <section className="overview-panel overview-runtime">
        <header><div><Activity size={16} /><h2>Runtime</h2></div><span className={`overview-label ${working && connected ? 'live' : ''}`}>{runtimeStatus}</span></header>
        <div className="overview-runtime-body">{!connected ? <p className="overview-muted">Connect to inspect agent activity.</p> : runtimeAgents.length ? runtimeAgents.map(agent => <button className="overview-agent" key={agent.jobId ?? agent.name} disabled={!agent.jobId} onClick={() => agent.jobId && onAgent(agent.jobId)}><span className={`overview-agent-dot ${agent.paused ? 'paused' : agent.busy ? 'busy' : ''}`} /><span><strong>{agent.name}</strong><small>{agent.paused ? 'Paused' : agent.busy ? 'Working' : 'Waiting'} · {agent.turns ?? 0} turns</small></span><ArrowUpRight size={13} /></button>) : <div className="overview-empty"><Activity size={24} strokeWidth={1.3} /><h3>{taskRunning ? 'Task in progress' : !documents ? 'Checking task activity' : 'Ready for the next task'}</h3><p>{taskRunning ? 'Open Chat to follow the current task. Delegated agents appear here when available.' : 'Live agent threads appear here as work is dispatched.'}</p></div>}</div>
        <footer><span>Messages · Pause · Resume</span><button onClick={() => onView('chat')}>View conversation <ArrowUpRight size={12} /></button></footer>
      </section>
    </div>

    <section className="overview-evolution" aria-labelledby="overview-evolution-title">
      <div className="overview-section-heading"><div><p className="eyebrow">Global evolution</p><h2 id="overview-evolution-title">Eight ways to grow the system.</h2><p>Browse the capability library. Inspect shared session notes through Memory.</p></div><span className="overview-eight">8 <span>entity types</span></span></div>
      <div className="overview-entities">{ENTITIES.map(({ kind, label, description, icon: Icon }) => {
        const capability = capabilities.find(item => item.kind === kind);
        return <button key={kind} onClick={() => kind === 'memories' ? setShowMemory(value => !value) : onCapability(kind)} aria-expanded={kind === 'memories' ? showMemory : undefined} aria-controls={kind === 'memories' ? 'overview-memory' : undefined}><div><Icon size={18} strokeWidth={1.6} /><ArrowUpRight size={12} /></div><strong>{label}</strong><span>{description}</span><small>{kind === 'memories' ? 'Inspect session notes' : connected && capability ? `${capability.available} available · ${capability.enabled} enabled` : 'Browse library'}</small></button>;
      })}</div>
      {showMemory ? <section className="overview-panel overview-memory" id="overview-memory"><header><div><Brain size={16} /><h2>Shared session notes</h2></div><button onClick={() => setShowMemory(false)} aria-label="Close session notes">×</button></header><p className="overview-muted">These notes persist when this session is reopened. Memory backends are configured by the agent; private actor notes remain separate.</p>{errors.documents || documents?.memory.error ? <p className="overview-error">{errors.documents || documents?.memory.error}</p> : !documents ? <p className="overview-muted">{connected ? 'Loading notes…' : 'Connect to read notes.'}</p> : !notes.length ? <p className="overview-muted">No shared notes have been written in this session.</p> : notes.map(note => <details key={note.path}><summary><BookOpen size={14} /> {note.path}</summary>{note.error ? <p className="overview-error">{note.error}</p> : <MessageMarkdown content={note.content} />}{note.truncated ? <p className="overview-muted">Preview truncated.</p> : null}</details>)}{documents?.memory.truncated ? <p className="overview-muted">Showing the first 30 notes.</p> : null}</section> : null}
    </section>

    <div className="overview-bottom-grid">
      <section className="overview-panel overview-candidates" id="project-candidates">
        <header><div><Package size={16} /><h2>Project candidates</h2></div><span className="overview-label">{stage ? `${stage.components.length} staged` : '—'}</span></header>
        <div className="overview-candidate-body">{errors.stage ? <p className="overview-error">{errors.stage}</p> : !stage ? <p className="overview-muted">{connected ? 'Loading candidates…' : 'Connect to inspect candidates.'}</p> : !stage.components.length ? <div className="overview-empty"><Package size={24} strokeWidth={1.3} /><h3>Room for the next improvement</h3><p>Capabilities developed in this project appear here before promotion to the shared library.</p></div> : <><div className="overview-candidate-list">{stage.components.map(item => <div key={item.relative_path}><span className="overview-kind">{item.module}</span><code>{item.relative_path}</code><span>Staged</span></div>)}</div><p className={stage.valid ? 'overview-check' : 'overview-error'}>{stage.valid ? <><Check size={13} /> Structural checks passed</> : stage.error || 'Structural checks failed'}</p><p className="overview-muted">Staging and structural checks do not establish functional improvement. Review evaluation evidence before sharing a candidate.</p><button className="overview-secondary" disabled={!connected || !stage.valid || promoting} onClick={async () => { setPromoting(true); try { await onPromote(stage); await reload(); } finally { setPromoting(false); } }}>{promoting ? 'Promoting…' : 'Promote to shared library'} <ArrowUpRight size={13} /></button></>}</div>
      </section>
      <section className="overview-panel overview-files">
        <header><div><Folder size={16} /><h2>Workspace</h2></div><button onClick={() => onView('code')}>Open Code <ArrowUpRight size={13} /></button></header>
        <div className="overview-files-body">{errors.files ? <p className="overview-error">{errors.files}</p> : !entries ? <p className="overview-muted">{connected ? 'Loading workspace…' : 'Connect to browse files.'}</p> : !entries.length ? <div className="overview-empty"><Folder size={24} strokeWidth={1.3} /><h3>Your work belongs here</h3><p>Project files and generated artifacts appear here as you work.</p></div> : <>{entries.slice(0, 6).map(entry => <button key={entry.path} onClick={() => entry.type === 'directory' ? onView('code') : onFile(entry.path)}>{entry.type === 'directory' ? <Folder size={14} /> : <File size={14} />}<span>{entry.name}</span><ArrowUpRight size={12} /></button>)}{entries.length > 6 ? <p className="overview-muted">Browse the full workspace in Code.</p> : null}</>}</div>
      </section>
    </div>
    <p className="overview-footnote">Project documents and staged candidates refresh every 15 seconds while this page is visible.</p>
    <Dialog open={Boolean(openedDocument || documentLoading)} onOpenChange={open => { if (!open) { documentGeneration.current++; setOpenedDocument(undefined); setDocumentLoading(false); } }}><DialogContent className="overview-document-dialog"><DialogTitle>{documentLoading ? 'Opening document…' : openedDocument?.path}</DialogTitle><DialogDescription>Project plan document</DialogDescription><div className="overview-document-content">{!documentLoading && openedDocument ? openedDocument.error ? <p className="overview-error">{openedDocument.error}</p> : openedDocument.exists ? <>{planMarkdown(openedDocument)}{openedDocument.truncated ? <p className="overview-muted">Preview truncated.</p> : null}</> : <p>This document has not been created.</p> : null}</div></DialogContent></Dialog>
  </div>;
}
