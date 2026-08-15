import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { BookOpen, FileText, Hash, Search, X } from 'lucide-react';

import type { RequestFn } from '../canvas/types';
import { CodeBlock, MARKDOWN_REHYPE_PLUGINS, reactNodeText } from '../components/common/Markdown';
import '../style/docs.css';

export interface DocEntry { path: string; section: string; title: string; }
interface DocContent extends DocEntry { content: string; }
interface Heading { id: string; text: string; depth: number; }

/** Slug used for a heading's anchor and its entry in the on-this-page rail. */
function slugify(text: string): string {
  return text.toLowerCase().replace(/[^\w一-鿿]+/g, '-').replace(/^-|-$/g, '') || 'section';
}

/**
 * Resolve a link written inside `from` against the repository root.
 *
 * Documents cross-reference each other with ordinary relative paths, because that is what
 * makes them readable on disk and checkable by `test_doc_links.py`. Rendered in a browser
 * those same paths mean nothing, so they are resolved back to repo-relative form here and
 * matched against the index — the alternative is a viewer whose every internal link is
 * broken, which is most of the value of a decision record.
 */
function resolveRelative(from: string, href: string): string {
  const base = from.split('/').slice(0, -1);
  for (const part of href.split('/')) {
    if (part === '.' || part === '') continue;
    if (part === '..') base.pop();
    else base.push(part);
  }
  return base.join('/');
}

export function DocsView({ request, endpoint }: { request: RequestFn; endpoint?: string }) {
  const [entries, setEntries] = useState<DocEntry[]>([]);
  const [active, setActive] = useState<string>();
  const [doc, setDoc] = useState<DocContent>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [filter, setFilter] = useState('');
  const [currentHeading, setCurrentHeading] = useState<string>();
  const articleRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    request('docs.list')
      .then((response) => {
        if (cancelled) return;
        if (!response.ok) throw new Error(response.error?.message ?? 'Could not list documents');
        const list = (response.result.documents ?? []) as DocEntry[];
        setEntries(list);
        setActive((current) => current ?? list[0]?.path);
      })
      .catch((cause: Error) => { if (!cancelled) setError(cause.message); });
    return () => { cancelled = true; };
  }, [request]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setLoading(true);
    setError(undefined);
    request('docs.get', { path: active })
      .then((response) => {
        if (cancelled) return;
        if (!response.ok) throw new Error(response.error?.message ?? 'Could not open this document');
        setDoc(response.result as unknown as DocContent);
      })
      .catch((cause: Error) => { if (!cancelled) { setError(cause.message); setDoc(undefined); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [active, request]);

  // The article scrolls, not the page, so the rail follows this element rather than window.
  useEffect(() => {
    const article = articleRef.current;
    if (!article || !doc) return;
    const onScroll = () => {
      const marks = Array.from(article.querySelectorAll<HTMLElement>('[data-heading]'));
      // The heading a reader is "at" is the last one already past the top edge, not the
      // nearest one — nearest flickers between two headings on a slow scroll.
      const passed = marks.filter((mark) => mark.getBoundingClientRect().top - article.getBoundingClientRect().top <= 12);
      setCurrentHeading((passed[passed.length - 1] ?? marks[0])?.dataset.heading);
    };
    onScroll();
    article.addEventListener('scroll', onScroll, { passive: true });
    return () => article.removeEventListener('scroll', onScroll);
  }, [doc]);

  useEffect(() => { articleRef.current?.scrollTo({ top: 0 }); }, [active]);

  const sections = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const matching = needle
      ? entries.filter((entry) => entry.title.toLowerCase().includes(needle) || entry.path.toLowerCase().includes(needle))
      : entries;
    const grouped: { section: string; items: DocEntry[] }[] = [];
    for (const entry of matching) {
      const last = grouped[grouped.length - 1];
      if (last?.section === entry.section) last.items.push(entry);
      else grouped.push({ section: entry.section, items: [entry] });
    }
    return grouped;
  }, [entries, filter]);

  const headings = useMemo<Heading[]>(() => {
    if (!doc) return [];
    const found: Heading[] = [];
    const seen = new Map<string, number>();
    let fenced = false;
    for (const line of doc.content.split('\n')) {
      if (line.startsWith('```')) { fenced = !fenced; continue; }
      if (fenced) continue;                       // `# comment` inside a shell block is not a heading
      const match = /^(#{2,3})\s+(.+?)\s*$/.exec(line);
      if (!match) continue;
      const text = match[2].replace(/[`*_]/g, '');
      const base = slugify(text);
      const count = (seen.get(base) ?? 0) + 1;
      seen.set(base, count);
      found.push({ id: count > 1 ? `${base}-${count}` : base, text, depth: match[1].length });
    }
    return found;
  }, [doc]);

  const known = useMemo(() => new Set(entries.map((entry) => entry.path)), [entries]);

  const jumpTo = useCallback((id: string) => {
    const target = articleRef.current?.querySelector<HTMLElement>(`[data-heading="${CSS.escape(id)}"]`);
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const headingCounter = useRef(new Map<string, number>());
  headingCounter.current = new Map();

  const components = useMemo(() => ({
    // `pre`, not `code`. react-markdown gives `code` to inline spans as well as to fenced
    // blocks, so mapping it here wrapped every `like this` in the full block chrome —
    // header, language label, Copy button — inside the paragraph containing it. React
    // reported it as a stream of validateDOMNesting warnings and the prose rendered as a
    // column of code cards.
    pre: ({ children }: { children?: React.ReactNode }) => <CodeBlock>{children}</CodeBlock>,
    h2: (props: { children?: React.ReactNode }) => renderHeading('h2', props.children),
    h3: (props: { children?: React.ReactNode }) => renderHeading('h3', props.children),
    a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
      if (!href) return <>{children}</>;
      if (/^(https?:|mailto:)/.test(href)) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
      const target = href.startsWith('#') ? undefined : resolveRelative(doc?.path ?? '', href.split('#')[0]);
      if (target && known.has(target)) {
        return <button className="docs-xref" onClick={() => setActive(target)}>{children}</button>;
      }
      if (href.startsWith('#')) {
        return <button className="docs-xref" onClick={() => jumpTo(href.slice(1))}>{children}</button>;
      }
      // A link to source that this browser does not serve. Rendered as the path it is
      // rather than as a link that goes nowhere — a dead link teaches the reader to stop
      // trusting the working ones.
      return <code className="docs-path" title={target}>{children}</code>;
    },
  }), [doc?.path, known, jumpTo]);

  function renderHeading(tag: 'h2' | 'h3', children: React.ReactNode) {
    const text = reactNodeText(children);
    const base = slugify(text.replace(/[`*_]/g, ''));
    const count = (headingCounter.current.get(base) ?? 0) + 1;
    headingCounter.current.set(base, count);
    const id = count > 1 ? `${base}-${count}` : base;
    const Tag = tag;
    return <Tag data-heading={id} id={id}>{children}</Tag>;
  }

  return (
    <div className="docs-view">
      <nav className="docs-nav" aria-label="Documents">
        <div className="docs-search">
          <Search size={13} strokeWidth={2} />
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter documents"
            aria-label="Filter documents"
          />
          {filter ? <button onClick={() => setFilter('')} aria-label="Clear filter"><X size={12} /></button> : null}
        </div>
        <div className="docs-nav-scroll">
          {sections.map((group) => (
            <div className="docs-group" key={group.section}>
              <p className="eyebrow">{group.section}</p>
              {group.items.map((entry) => (
                <button
                  key={entry.path}
                  className={entry.path === active ? 'docs-link docs-link-active' : 'docs-link'}
                  onClick={() => setActive(entry.path)}
                  title={entry.path}
                >
                  <FileText size={13} strokeWidth={1.9} />
                  <span>{entry.title}</span>
                </button>
              ))}
            </div>
          ))}
          {sections.length === 0 ? <p className="docs-empty-nav">Nothing matches “{filter}”.</p> : null}
        </div>
      </nav>

      <div className="docs-article" ref={articleRef}>
        {error ? (
          <div className="docs-placeholder">
            <strong>Could not load</strong>
            <p>{error}</p>
            {/* The address, because this failure is almost never about the documents.
                `docs.list` is newer than most gateways anyone has running, so "unknown
                method" means the page is talking to an older one — and the endpoint is
                stored, so it survives moving the app to a different port. Without the
                address on screen, that reads as a broken feature and the search starts in
                the wrong layer entirely. It did once. */}
            {endpoint ? (
              <p className="docs-endpoint">
                connected to <code>{endpoint}</code>
                {endpoint !== `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
                  ? <> — not this page’s origin. Open <strong>Connection</strong> in the sidebar
                      and clear it to use <code>{`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`}</code>.</>
                  : null}
              </p>
            ) : null}
          </div>
        ) : loading && !doc ? (
          <div className="docs-placeholder"><p>Loading…</p></div>
        ) : doc ? (
          <article className="docs-body markdown-document">
            <header className="docs-head">
              <p className="eyebrow"><BookOpen size={11} strokeWidth={2.2} /> {doc.section}</p>
              <h1>{doc.title}</h1>
              <code className="docs-source">{doc.path}</code>
            </header>
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={MARKDOWN_REHYPE_PLUGINS} components={components}>
              {doc.content}
            </ReactMarkdown>
          </article>
        ) : (
          <div className="docs-placeholder"><strong>No document selected</strong></div>
        )}
      </div>

      <aside className="docs-toc" aria-label="On this page">
        {headings.length > 1 ? (
          <>
            <p className="eyebrow"><Hash size={11} strokeWidth={2.2} /> On this page</p>
            <div className="docs-toc-scroll">
              {headings.map((heading) => (
                <button
                  key={heading.id}
                  className={`docs-toc-item docs-toc-d${heading.depth}${heading.id === currentHeading ? ' docs-toc-current' : ''}`}
                  onClick={() => jumpTo(heading.id)}
                >
                  {heading.text}
                </button>
              ))}
            </div>
          </>
        ) : null}
      </aside>
    </div>
  );
}

export default DocsView;
