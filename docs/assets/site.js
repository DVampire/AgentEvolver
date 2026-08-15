/* Shared behaviour for the documentation pages: language switching and the reading rail.
 *
 * Each page declares its own `window.I18N = {en:{…}, zh:{…}}` before loading this file;
 * everything else is identical across pages and lives here once.
 *
 * The stored key is `ae_lang`, shared with index.html, so a reader who chose 中文 on the
 * homepage does not choose it again on every page they open. */

(function () {
  const DICTS = window.I18N || { en: {}, zh: {} };

  function applyLang(lang) {
    const dict = DICTS[lang] || DICTS.en || {};
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
    document.querySelectorAll('[data-i18n]').forEach((el) => {
      const value = dict[el.dataset.i18n];
      if (value === undefined) return;
      // Values carry inline markup (<code>, <strong>) authored in the page itself, never
      // user input, so innerHTML is the correct sink. A page that took translations from
      // anywhere else would need to escape them.
      el.innerHTML = value;
    });
    document.querySelectorAll('.lang button').forEach((b) => {
      b.classList.toggle('active', b.dataset.lang === lang);
    });
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
  applyLang(saved === 'zh' || saved === 'en' ? saved : preferred);

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
