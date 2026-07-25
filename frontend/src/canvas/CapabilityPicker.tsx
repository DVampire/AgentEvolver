import { createContext, useContext, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Plus, Search, X } from 'lucide-react';

import { Popover, PopoverContent, PopoverTrigger } from '../components/ui/popover';
import { MOUNT_LABELS, type MountRosters } from './types';

/** Global capability rosters (from canvas.catalog) shared with every agent node. */
export const MountRosterContext = createContext<MountRosters>({});

/** One mount section on an agent node: chips for the current selection plus a
 * searchable add-popover over the roster. Empty selection = agent defaults. */
function MountSection({ kind, selected, onChange }: { kind: string; selected: string[]; onChange: (next: string[]) => void }) {
  const rosters = useContext(MountRosterContext);
  const roster = rosters[kind] ?? [];
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return roster.filter((item) => !q || item.name.toLowerCase().includes(q) || item.description.toLowerCase().includes(q));
  }, [roster, query]);

  const toggle = (name: string) => {
    onChange(selected.includes(name) ? selected.filter((item) => item !== name) : [...selected, name]);
  };

  if (!roster.length) return null;

  return (
    <div className="lf-mount-section">
      <div className="lf-mount-head">
        <span>{MOUNT_LABELS[kind] ?? kind}</span>
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <button className="lf-mount-add" title={`Add ${MOUNT_LABELS[kind] ?? kind}`}><Plus size={12} /></button>
          </PopoverTrigger>
          <PopoverContent className="w-64 p-0 nodrag nowheel" align="start">
            <div className="flex items-center gap-2 border-b border-border px-2.5 py-2">
              <Search size={13} className="text-muted-foreground" />
              <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)}
                placeholder={`Search ${(MOUNT_LABELS[kind] ?? kind).toLowerCase()}…`}
                className="w-full bg-transparent text-xs outline-none" />
            </div>
            <div className="max-h-56 overflow-y-auto py-1">
              {filtered.map((item) => (
                <button key={item.name} onClick={() => toggle(item.name)}
                  className={`flex w-full items-start gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-muted ${selected.includes(item.name) ? 'text-foreground' : 'text-muted-foreground'}`}>
                  <span className={`mt-0.5 grid h-3.5 w-3.5 flex-none place-items-center rounded border ${selected.includes(item.name) ? 'border-primary bg-primary text-primary-foreground' : 'border-border'}`}>
                    {selected.includes(item.name) ? '✓' : ''}
                  </span>
                  <span className="min-w-0"><strong className="block truncate font-medium text-foreground">{item.name}</strong>{item.description ? <span className="block truncate">{item.description}</span> : null}</span>
                </button>
              ))}
              {!filtered.length ? <p className="px-2.5 py-3 text-center text-xs text-muted-foreground">No match.</p> : null}
            </div>
          </PopoverContent>
        </Popover>
      </div>
      {selected.length ? (
        <div className="lf-mount-chips">
          {selected.map((name) => (
            <span className="lf-chip" key={name}>{name}<button onClick={() => toggle(name)} aria-label={`Remove ${name}`}><X size={11} /></button></span>
          ))}
        </div>
      ) : <p className="lf-mount-empty">Defaults (all)</p>}
    </div>
  );
}

/** The capability mount block shown inside an agent node — one collapsible
 * "Capabilities" row (defaults collapsed) that expands to the per-kind pickers.
 * Keeps the node compact while the common "use defaults" case is a single row. */
export function AgentMounts({ kinds, mounts, onChange }: { kinds: string[]; mounts: Record<string, string[]>; onChange: (kind: string, next: string[]) => void }) {
  const total = kinds.reduce((sum, kind) => sum + (mounts[kind]?.length ?? 0), 0);
  const [open, setOpen] = useState(total > 0);
  return (
    <div className="lf-mounts nodrag nowheel">
      <button className="lf-mounts-head" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span>Capabilities</span>
        <em>{total ? `${total} mounted` : 'Defaults'}</em>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
      </button>
      {open ? kinds.map((kind) => (
        <MountSection key={kind} kind={kind} selected={mounts[kind] ?? []} onChange={(next) => onChange(kind, next)} />
      )) : null}
    </div>
  );
}
