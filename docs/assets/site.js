/* Shared behaviour for the documentation pages: language switching and the reading rail.
 *
 * Each page declares its own `window.I18N = {en:{…}, zh:{…}}` before loading this file;
 * everything else is identical across pages and lives here once.
 *
 * The stored key is `ae_lang`, shared with index.html, so a reader who chose 中文 on the
 * homepage does not choose it again on every page they open. */

(function () {
  const DICTS = window.I18N || { en: {}, zh: {} };
  document.querySelectorAll('main table').forEach((table) => {
    const scroll = document.createElement('div');
    scroll.className = 'table-scroll';
    scroll.tabIndex = 0;
    scroll.setAttribute('role', 'region');
    table.before(scroll);
    scroll.append(table);
  });
  const authoredEnglish = new WeakMap();
  document.querySelectorAll('[data-i18n]').forEach((el) => authoredEnglish.set(el, el.innerHTML));

  function applyLang(lang) {
    const dict = DICTS[lang] || DICTS.en || {};
    document.querySelectorAll('.table-scroll').forEach((table) => {
      table.setAttribute('aria-label', lang === 'zh' ? '可横向滚动的表格' : 'Scrollable table');
    });
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
    document.querySelectorAll('[data-i18n]').forEach((el) => {
      const value = dict[el.dataset.i18n];
      if (value === undefined) {
        // New prose can use its authored English as the canonical fallback. This keeps
        // long reference pages maintainable: only the non-English variant must repeat it.
        if (lang === 'en' && authoredEnglish.has(el)) el.innerHTML = authoredEnglish.get(el);
        return;
      }
      // Values carry inline markup (<code>, <strong>) authored in the page itself, never
      // user input, so innerHTML is the correct sink. A page that took translations from
      // anywhere else would need to escape them.
      el.innerHTML = value;
    });
    document.querySelectorAll('.lang button').forEach((b) => {
      b.classList.toggle('active', b.dataset.lang === lang);
      b.setAttribute('aria-pressed', String(b.dataset.lang === lang));
    });
    if (dict.page_title) document.title = dict.page_title;
    const description = document.querySelector('meta[name="description"]');
    if (description && dict.page_description) description.content = dict.page_description;
    const diagram = lang === 'zh' ? 'assets/arch_zh' : 'assets/arch';
    document.querySelectorAll('[data-diagram-image]').forEach((img) => {
      img.src = diagram + '.svg';
      img.alt = lang === 'zh'
        ? 'AgentEvolver 系统架构：共享运行时、上下文、能力、环境、部署与基于证据的采纳'
        : 'AgentEvolver architecture: shared runtime, context, capabilities, environments, deployment and evidence-based adoption';
    });
    document.querySelectorAll('[data-diagram-link]').forEach((a) => { a.href = diagram + '.svg'; });
    document.querySelectorAll('[data-diagram-ppt]').forEach((a) => { a.href = diagram + '.pptx'; });
    try { localStorage.setItem('ae_lang', lang); } catch (e) { /* private mode */ }
  }

  document.querySelectorAll('.lang button').forEach((b) => {
    b.addEventListener('click', () => applyLang(b.dataset.lang));
  });

  let saved = null;
  try { saved = localStorage.getItem('ae_lang'); } catch (e) { /* private mode */ }
  // Parenthesised deliberately: `saved || cond ? a : b` groups as `(saved || cond) ? a : b`,
  // which turns a stored "en" — truthy — into 中文. The stored choice wins outright; the
  // browser is consulted only when there is none.
  const preferred = (navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en';
  const requested = new URLSearchParams(location.search).get('lang');
  applyLang(requested === 'zh' || requested === 'en' ? requested :
    (saved === 'zh' || saved === 'en' ? saved : preferred));

  // A two-pixel reading line makes long reference pages feel finite without adding
  // another widget to the chrome. CSS reads this custom property on nav::after.
  const updateProgress = () => {
    const max = document.documentElement.scrollHeight - window.innerHeight;
    const progress = max > 0 ? Math.min(100, Math.max(0, window.scrollY / max * 100)) : 0;
    document.documentElement.style.setProperty('--read-progress', progress.toFixed(2) + '%');
  };
  updateProgress();
  window.addEventListener('scroll', updateProgress, { passive: true });

  // The reading rail. The section a reader is "at" is the last heading already past the
  // top edge, not the nearest one — nearest flickers between two entries on a slow scroll.
  const links = [...document.querySelectorAll('aside a[href^="#"]')];
  const targets = links
    .map((a) => document.querySelector(a.getAttribute('href')))
    .filter(Boolean);

  if (targets.length) {
    const mark = () => {
      let current = targets[0];
      for (const t of targets) if (t.getBoundingClientRect().top <= 96) current = t;
      links.forEach((a) => a.classList.toggle('on', a.getAttribute('href') === '#' + current.id));
    };
    mark();
    window.addEventListener('scroll', mark, { passive: true });
  }
})();
