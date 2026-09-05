'use strict';
const el = (tag, text, cls) => { const node = document.createElement(tag); node.textContent = text; if (cls) node.className = cls; return node; };
const dateFormat = new Intl.DateTimeFormat('en-GB', {dateStyle: 'medium', timeStyle: 'long'});
function timestamp(label, value) {
  const row = el('div', '', 'page-time');
  row.append(el('dt', label));
  const detail = el('dd', 'Not recorded');
  const date = value ? new Date(value) : null;
  if (date && !Number.isNaN(date.getTime())) {
    const time = el('time', dateFormat.format(date));
    time.dateTime = date.toISOString(); time.title = time.dateTime;
    detail.replaceChildren(time);
  }
  row.append(detail); return row;
}
async function refresh() {
  try {
    const response = await fetch('/_sites/api/pages', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const pages = await response.json();
    pages.sort((a, b) => (b.status === 'running') - (a.status === 'running') || (b.updated_at || '').localeCompare(a.updated_at || ''));
    document.getElementById('pages').replaceChildren(...pages.map(page => {
      const card = el('article', '', 'slot');
      card.append(el('p', page.kind + ' · ' + page.status, 'eyebrow'), el('h2', page.title));
      const link = el(page.status === 'running' ? 'a' : 'span', page.url, 'run-name');
      if (page.status === 'running') { link.href = page.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
      const times = el('dl', '', 'page-times');
      times.append(timestamp('Deployed', page.deployed_at));
      if (!page.deployed_at) times.append(timestamp('Created', page.created_at));
      times.append(timestamp('Status updated', page.updated_at));
      card.append(link, times); return card;
    }));
    document.getElementById('count').textContent = pages.filter(p => p.status === 'running').length + ' running';
    document.getElementById('health').textContent = 'Connected';
  } catch (error) { document.getElementById('health').textContent = 'Disconnected · ' + error.message; }
  finally { setTimeout(refresh, 5000); }
}
refresh();
