document.addEventListener('DOMContentLoaded', () => {
  const buttons = Array.from(document.querySelectorAll('[data-tab-target]'));
  const panels = Array.from(document.querySelectorAll('[data-tab-panel]'));
  const activate = name => {
    buttons.forEach(button => button.classList.toggle('active', button.dataset.tabTarget === name));
    panels.forEach(panel => panel.classList.toggle('active', panel.dataset.tabPanel === name));
    if (panels.some(panel => panel.dataset.tabPanel === name)) history.replaceState(null, '', `#${name}`);
  };
  buttons.forEach(button => button.addEventListener('click', () => activate(button.dataset.tabTarget)));

  const search = document.querySelector('#message-search');
  const role = document.querySelector('#role-filter');
  const layer = document.querySelector('#layer-filter');
  const cache = document.querySelector('#cache-filter');
  const cards = Array.from(document.querySelectorAll('.message-card'));
  cards.forEach(card => { card._requestSearchText = card.textContent.toLowerCase(); });
  const noResults = document.querySelector('#no-results');
  const filter = () => {
    const query = (search?.value || '').trim().toLowerCase();
    const selectedRole = role?.value || 'all';
    const selectedLayer = layer?.value || 'all';
    const selectedCache = cache?.value || 'all';
    let visible = 0;
    cards.forEach(card => {
      const matchesText = !query || card._requestSearchText.includes(query);
      const matchesRole = selectedRole === 'all' || card.dataset.role === selectedRole;
      const matchesLayer = selectedLayer === 'all' || card.dataset.layer === selectedLayer;
      const matchesCache = selectedCache === 'all' || card.dataset.cache === selectedCache;
      const show = matchesText && matchesRole && matchesLayer && matchesCache;
      card.classList.toggle('hidden', !show);
      if (show) visible += 1;
    });
    noResults?.classList.toggle('hidden', visible !== 0);
  };
  search?.addEventListener('input', filter);
  role?.addEventListener('change', filter);
  layer?.addEventListener('change', filter);
  cache?.addEventListener('change', filter);

  document.querySelectorAll('[data-expand-message]').forEach(button => {
    button.addEventListener('click', () => {
      const card = button.closest('.message-card');
      const expanded = card?.classList.toggle('expanded');
      button.textContent = expanded ? 'Limit height' : 'Expand';
    });
  });

  document.querySelectorAll('[data-message-index]').forEach(node => {
    node.addEventListener('click', () => {
      const card = document.querySelector(`#message-${node.dataset.messageIndex}`);
      card?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card?.classList.remove('flash');
      window.requestAnimationFrame(() => card?.classList.add('flash'));
    });
  });

  const wrapButton = document.querySelector('#toggle-wrap');
  wrapButton?.addEventListener('click', () => {
    const noWrap = document.body.classList.toggle('no-wrap');
    wrapButton.textContent = noWrap ? 'Wrap text' : 'No wrap';
  });

  const collapseButton = document.querySelector('#collapse-details');
  collapseButton?.addEventListener('click', () => {
    const details = Array.from(document.querySelectorAll('details'));
    const shouldOpen = details.length > 0 && details.every(item => !item.open);
    details.forEach(item => { item.open = shouldOpen; });
    collapseButton.textContent = shouldOpen ? 'Collapse details' : 'Expand details';
  });

  const copyButton = document.querySelector('#copy-json');
  copyButton?.addEventListener('click', async () => {
    const text = document.querySelector('#canonical-json')?.textContent || '';
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const field = document.createElement('textarea');
        field.value = text;
        field.style.position = 'fixed';
        field.style.opacity = '0';
        document.body.appendChild(field);
        field.select();
        document.execCommand('copy');
        field.remove();
      }
      copyButton.textContent = 'Copied';
    } catch (_) {
      copyButton.textContent = 'Copy unavailable';
    }
    window.setTimeout(() => { copyButton.textContent = 'Copy JSON'; }, 1400);
  });

  document.addEventListener('keydown', event => {
    if (event.key === '/' && document.activeElement !== search) {
      event.preventDefault();
      activate('conversation');
      search?.focus();
    }
  });

  const initialTab = window.location.hash.slice(1);
  if (panels.some(panel => panel.dataset.tabPanel === initialTab)) activate(initialTab);
});
