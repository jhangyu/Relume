// Palette switcher for the phone mockups. Not app code.
const PALETTES = [
  ['sunset', 'Dynamic colour · warm wallpaper', ['#9a4521', '#ffdbcd', '#f2e2a7']],
  ['lagoon', 'Dynamic colour · cool wallpaper', ['#006a63', '#9ef2e7', '#cee5ff']],
  ['fallback', 'Fallback · API 29–30 (no dynamic colour)', ['#2559bd', '#dae2ff', '#ffd6fe']],
];
const ROLES = ['primary', 'primaryContainer', 'secondaryContainer', 'tertiaryContainer', 'surface', 'surfaceContainer', 'surfaceContainerHighest', 'errorContainer', 'warningContainer'];
const ON = { primary: 'onPrimary', primaryContainer: 'onPrimaryContainer', secondaryContainer: 'onSecondaryContainer', tertiaryContainer: 'onTertiaryContainer', errorContainer: 'onErrorContainer', warningContainer: 'onWarningContainer' };
function setPalette(p) {
  document.body.dataset.palette = p;
  localStorage.setItem('relume-c1-palette', p);
  document.querySelectorAll('#pal button').forEach(b => b.setAttribute('aria-pressed', b.dataset.p === p));
}
document.querySelector('#pal').innerHTML = '<span style="font:500 13px var(--font);color:#47464f">Palette</span>' + PALETTES.map(([k, l, c]) =>
  `<button data-p="${k}" aria-pressed="false"><span class="sw3">${c.map(x => `<i style="background:${x}"></i>`).join('')}</span>${l}</button>`).join('');
document.querySelector('.roles').innerHTML = ROLES.map(r => `<span style="background:var(--${r});color:var(--${ON[r] || 'onSurface'});border:1px solid var(--outlineVariant)">${r}</span>`).join('');
document.querySelector('#pal').addEventListener('click', e => { const b = e.target.closest('button[data-p]'); if (b) setPalette(b.dataset.p); });
setPalette(new URLSearchParams(location.search).get('p') || localStorage.getItem('relume-c1-palette') || 'sunset');
