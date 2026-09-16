/* Shared global navigation. Keeping the markup here means a new documentation
   page cannot silently invent a different logo, column order, or mobile menu. */
(function () {
  const nav = document.querySelector('[data-ae-nav]');
  if (!nav) return;
  const current = nav.dataset.aeNav || '';
  const items = [
    ['home', 'index.html', 'nav_home', 'Home'],
    ['tutorial', 'tutorial.html', 'nav_tut', 'Tutorial'],
    ['architecture', 'architecture.html', 'nav_arch', 'Architecture'],
    ['modules', 'modules.html', 'nav_mod', 'Modules'],
    ['development', 'development.html', 'nav_dev', 'Development'],
    ['ui', 'ui.html', 'nav_ui', 'Web UI'],
  ];
  const links = items.map(([id, href, key, label]) =>
    `<a class="ae-link${id === current ? ' current' : ''}" href="${href}" data-i18n="${key}"${id === current ? ' aria-current="page"' : ''}>${label}</a>`
  ).join('');
  nav.className = 'ae-nav';
  nav.innerHTML = `
    <div class="ae-nav-inner">
      <a class="ae-brand" href="index.html" aria-label="AgentEvolver home">
        <span class="ae-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15.5 6.2L3 16"/><path d="M3 21v-5h5"/></svg></span>
        <span>AgentEvolver</span>
      </a>
      <button class="ae-menu-toggle" type="button" aria-label="Navigation menu" aria-expanded="false" aria-controls="ae-global-links"><span></span><span></span><span></span></button>
      <div class="ae-links" id="ae-global-links">${links}</div>
      <div class="ae-actions">
        <div class="lang ae-lang" role="group" aria-label="Language">
          <button type="button" data-lang="en" class="active">EN</button><button type="button" data-lang="zh">中文</button>
        </div>
        <a class="ae-github" href="https://github.com/DVampire/AgentEvolver" target="_blank" rel="noopener"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.87c-2.78.6-3.37-1.18-3.37-1.18-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.61.07-.61 1 .07 1.53 1.03 1.53 1.03.9 1.53 2.35 1.09 2.92.83.09-.65.35-1.09.64-1.34-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02A9.6 9.6 0 0 1 12 6.82a9.6 9.6 0 0 1 2.5.34c1.91-1.29 2.75-1.02 2.75-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.69-4.57 4.93.36.31.68.92.68 1.86v2.76c0 .27.18.58.69.48A10 10 0 0 0 12 2z"/></svg>GitHub</a>
      </div>
    </div>`;

  const toggle = nav.querySelector('.ae-menu-toggle');
  const menu = nav.querySelector('.ae-links');
  const close = () => { menu.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); };
  toggle.addEventListener('click', () => {
    const open = !menu.classList.contains('open');
    menu.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
  });
  menu.addEventListener('click', (event) => { if (event.target.closest('a')) close(); });
  document.addEventListener('click', (event) => { if (!nav.contains(event.target)) close(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });

  // The footer uses the same information architecture and brand mark. A custom
  // element renders when the parser reaches it, before each page's i18n pass.
  if (!customElements.get('ae-footer')) {
    customElements.define('ae-footer', class extends HTMLElement {
      connectedCallback() {
        this.innerHTML = `<footer class="ae-footer"><div class="ae-footer-inner">
          <div class="ae-footer-copy">
            <a class="ae-footer-brand" href="index.html"><span class="ae-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15.5 6.2L3 16"/><path d="M3 21v-5h5"/></svg></span><span>AgentEvolver</span></a>
            <span class="ae-footer-tag" data-i18n="foot_tag">A self-evolving multi-agent framework · MIT License</span>
          </div>
          <div class="ae-footer-links">
            <a href="index.html" data-i18n="nav_home">Home</a><a href="tutorial.html" data-i18n="nav_tut">Tutorial</a><a href="architecture.html" data-i18n="nav_arch">Architecture</a><a href="modules.html" data-i18n="nav_mod">Modules</a><a href="development.html" data-i18n="nav_dev">Development</a><a href="ui.html" data-i18n="nav_ui">Web UI</a><a href="https://github.com/DVampire/AgentEvolver" target="_blank" rel="noopener">GitHub</a>
          </div>
        </div></footer>`;
      }
    });
  }
})();
