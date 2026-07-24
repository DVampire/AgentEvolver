import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, GripVertical, Search } from 'lucide-react';

import { Input } from '../components/ui/input';
import { useDebounce } from '../hooks/use-debounce';
import { CategoryIcon } from '../icons';
import { CATEGORY_LABELS, CATEGORY_ORDER, DND_MIME, type NodeSpec } from './types';

/** Langflow-style component sidebar: search, collapsible category sections,
 * draggable rows (double-click also places the node). */
export function Palette({ specs, connected, onAdd }: { specs: NodeSpec[]; connected: boolean; onAdd: (spec: NodeSpec) => void }) {
  const [search, setSearch] = useState('');
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(['agent', 'workflow']));
  const query = useDebounce(search, 150).trim().toLowerCase();

  const grouped = useMemo(() => {
    const filtered = specs.filter((spec) => !query || spec.label.toLowerCase().includes(query) || spec.id.toLowerCase().includes(query));
    return CATEGORY_ORDER.map((category) => ({ category, items: filtered.filter((spec) => spec.category === category) })).filter((group) => group.items.length);
  }, [specs, query]);

  const toggle = (category: string) => setCollapsed((current) => {
    const next = new Set(current);
    next.has(category) ? next.delete(category) : next.add(category);
    return next;
  });

  return (
    <aside className="canvas-catalog">
      <div className="relative mx-3 mb-2.5 mt-3.5">
        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <Input className="h-9 pl-9 text-sm" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search" />
      </div>
      <p className="canvas-catalog-title">Components</p>
      <div className="canvas-catalog-list">
        {grouped.map((group) => {
          const isCollapsed = collapsed.has(group.category) && !query;
          return (
            <section key={group.category}>
              <button className="canvas-cat-head" onClick={() => toggle(group.category)} aria-expanded={!isCollapsed}>
                <CategoryIcon category={group.category} />
                <strong>{CATEGORY_LABELS[group.category]}</strong>
                <em>{isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}</em>
              </button>
              {!isCollapsed ? group.items.map((spec) => (
                <div className="canvas-palette-item" key={spec.id} title={spec.description} draggable
                  onDragStart={(event) => { event.dataTransfer.setData(DND_MIME, spec.id); event.dataTransfer.effectAllowed = 'copy'; }}
                  onDoubleClick={() => onAdd(spec)}>
                  <CategoryIcon category={spec.category} />
                  <strong>{spec.label}</strong>
                  <i className="drag-dots"><GripVertical size={13} /></i>
                </div>
              )) : null}
            </section>
          );
        })}
        {!grouped.length ? <p className="empty">{connected ? 'No components match this search.' : 'Connecting…'}</p> : null}
      </div>
    </aside>
  );
}
