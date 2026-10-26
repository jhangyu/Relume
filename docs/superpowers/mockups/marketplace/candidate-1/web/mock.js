// Shared mock behaviour: drawer toggle, toggle pills, tabs, tile colours. Not production code.
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const tileClass = s => 'c' + (1 + [...s].reduce((a, c) => a + c.charCodeAt(0), 0) % 10);
const initials = n => n.replace(/[^A-Za-z0-9 ]/g, '').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();
const ico = (n, cls = '') => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
const ORIGIN = { official: 'Official catalog', builtin: 'Built-in', custom: 'Custom' };
const org = o => `<span class="org ${o}" title="Source: ${ORIGIN[o]}">${ORIGIN[o]}</span>`;
document.addEventListener('click', e => {
  const h = e.target.closest('.hamb'); if (h) { $('.side').classList.toggle('open'); h.setAttribute('aria-expanded', $('.side').classList.contains('open')); }
  const p = e.target.closest('.pill[aria-pressed]'); if (p && !p.dataset.radio) p.setAttribute('aria-pressed', p.getAttribute('aria-pressed') !== 'true');
  const t = e.target.closest('.tab'); if (t) { $$('.tab', t.parentElement).forEach(x => x.setAttribute('aria-selected', x === t)); }
});
document.addEventListener('keydown', e => {
  if (e.key === '/' && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName) && $('#q')) { e.preventDefault(); $('#q').focus(); }
});
