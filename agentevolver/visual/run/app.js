'use strict';
const $ = id => document.getElementById(id);
const number = value => Number(value || 0).toLocaleString('en-US');
const money = value => '$' + Number(value || 0).toFixed(2);
const dateFormat = new Intl.DateTimeFormat('en-GB', {dateStyle: 'medium', timeStyle: 'long'});
const node = (tag, value, cls) => { const el = document.createElement(tag); if (value != null) el.textContent = value; if (cls) el.className = cls; return el; };
const empty = (id, text) => $(id).replaceChildren(node('p', text, 'muted'));
const link = (text, url) => {
  const el = node('a', text);
  try { const target = new URL(url, location.href); if (!['http:', 'https:'].includes(target.protocol)) return node('span', text); el.href = target.href; } catch { return node('span', text); }
  el.target = '_blank'; el.rel = 'noopener noreferrer'; return el;
};
function render(data) {
  $('title').textContent = data.title;
  $('identity').textContent = 'Session ' + data.session_id + ' · PID ' + data.launcher_pid;
  $('status').textContent = data.status;
  $('runtime').textContent = data.alive ? 'Launcher alive · ' + data.observation : 'Launcher is no longer alive';
  $('calls').textContent = number(data.usage.calls);
  $('requests').textContent = number(data.agents.reduce((n, a) => n + a.requests, 0)) + ' requests recorded (including retries)';
  $('cost').textContent = data.usage.costed_calls ? money(data.usage.cost) : 'Not reported';
  $('cost-note').textContent = number(data.usage.costed_calls) + ' / ' + number(data.usage.calls) + ' completed calls report cost';
  $('agent-count').textContent = data.agents.length + ' observed';
  $('observation').textContent = data.observation === 'trace only' ? 'Attached to an existing launcher. Phases are last trace observations; idle subscribers and parent relationships may not be visible until they run.' : 'Runtime lifecycle + last trace activity. Turn completion is not process completion.';
  $('agents').replaceChildren(...data.agents.map(a => {
    const card = node('div', null, 'slot');
    const phase = data.alive ? (a.state || a.phase || 'observed') : 'launcher stopped';
    card.append(node('span', phase, 'state'), node('h3', a.name, 'agent-name'));
    card.append(node('p', 'Step ' + (a.step == null ? '—' : a.step + 1) + ' · ' + number(a.usage.calls) + ' calls · ' + money(a.usage.cost), 'muted'));
    card.append(node('p', a.action || 'No activity yet', 'muted'));
    if (a.updated_at) {
      const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(a.updated_at)) / 1000));
      card.append(node('p', 'Last trace activity ' + (seconds < 60 ? seconds + 's' : Math.floor(seconds / 60) + 'm ' + seconds % 60 + 's') + ' ago', 'muted'));
    }
    if (a.pid) card.append(node('small', 'Process ' + a.pid + (a.parent ? ' ← ' + a.parent : ''), 'muted'));
    if (a.mode) card.append(node('p', a.mode + ' · ' + (a.turns || 0) + ' turns · ' + (a.queued || 0) + ' queued', 'muted'));
    if (a.topics?.length) card.append(node('p', 'Subscribed: ' + a.topics.join(', '), 'muted'));
    return card;
  }));
  if (!data.agents.length) empty('agents', 'No agent events yet.');
  const u = data.usage;
  const metrics = [['Uncached input', number(u.input_tokens)], ['Cache reads', number(u.cache_read_tokens)], ['Cache writes', number(u.cache_write_tokens)], ['Output tokens', number(u.output_tokens)], ['Cache hit ratio', u.cache_hit_ratio == null ? '—' : (u.cache_hit_ratio * 100).toFixed(1) + '%']];
  $('usage').replaceChildren(...metrics.map(([k, v]) => { const el = node('div', null, 'metric'); el.append(node('span', k), node('strong', v)); return el; }));
  $('deployments').replaceChildren(...data.deployments.map(d => {
    const el = node('article', null, 'slot'); el.append(node('h3', d.site_id, 'agent-name'), node('p', (d.status || 'unknown') + ' · release ' + (d.release_number || '—'), 'muted'));
    if (d.url) el.append(link('Open website ↗', d.url), node('p', d.url, 'muted'));
    const deployed = d.deployed_at ? new Date(d.deployed_at) : null;
    el.append(node('p', 'Deployed · ' + (deployed && !Number.isNaN(deployed.getTime()) ? dateFormat.format(deployed) : 'Not recorded'), 'muted'));
    if (d.source_revision) el.append(node('small', 'Revision ' + d.source_revision.slice(0, 12), 'muted'));
    if (d.versions?.length) {
      const history = node('details'); history.append(node('summary', 'Version history · ' + d.versions.length));
      [...d.versions].reverse().forEach(v => {
        const row = node('p', null, 'entry');
        row.append(v.url ? link('r' + v.number + ' ↗', v.url) : node('span', 'r' + v.number));
        row.append(node('small', v.deployed_at || 'Time not recorded', 'muted'));
        history.append(row);
      });
      el.append(history);
    }
    return el;
  }));
  if (!data.deployments.length) empty('deployments', 'No deployments attributed to this run yet.');
  const families = ['agent', 'tool', 'skill', 'workflow', 'connector', 'plugin', 'environment', 'memory'];
  const badges = node('div', null, 'families');
  families.forEach(f => badges.append(node('span', f + ' · ' + data.components.filter(c => c.module === f).length, 'pill muted')));
  $('components').replaceChildren(badges, ...data.components.map(c => node('p', c.module + ' / ' + c.name + ' @ ' + c.version, 'entry')));
  $('requests-list').replaceChildren(...data.requests.map(r => { const el = node('div', null, 'entry'); el.append(link(r.name, r.url), node('small', r.agent)); return el; }));
  if (!data.requests.length) empty('requests-list', 'No saved requests yet.');
  $('events').replaceChildren(...data.events.map(e => {
    const el = node('div', null, 'entry event');
    el.append(node('span', e.timestamp ? new Date(e.timestamp).toLocaleTimeString('en-GB') : '—', 'muted'), node('span', e.agent), node('span', (e.action || e.type) + (e.success === false ? ' · failed' : ''), e.success === false ? 'fail' : ''));
    return el;
  }));
}
async function refresh() {
  try {
    const response = await fetch(new URL('api/status', location.href), {cache: 'no-store', signal: AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json(); render(data);
    $('health').textContent = 'Connected'; $('health-dot').className = 'health-dot live';
    $('freshness').textContent = 'Updated ' + new Date(data.updated_at).toLocaleTimeString('en-GB');
  } catch (error) {
    $('health').textContent = 'Disconnected'; $('health-dot').className = 'health-dot';
    $('freshness').textContent = 'Last view retained · ' + error.message;
  } finally { setTimeout(refresh, 5000); }
}
refresh();
