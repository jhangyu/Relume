import { $, $$, act, api, ask, catSlug, fmt, html, icon, iconTile, shell, toast } from '/static/app.js';

// Marketplace (spec §7.3): catalog gallery, expand-in-place enable panel, custom sources.
const PER_PAGE = 48;
const ORIGIN = { official: 'Official catalog', builtin: 'Built-in', custom: 'Custom' };
const ENGINE = { morphe: 'Morphe', lspatch: 'Xposed · LSPatch', npatch: 'Xposed · NPatch' };
const AUTHOR_COLORS = ['#0f6cbd', '#c239b3', '#8764b8', '#03787c', '#5c2e91', '#ca5010'];
// download.state (spec §5.4) -> [msgbar class, icon, title, hint]
const DL = {
  pending: ['info', 'info', 'APKMirror lookup pending', 'Relume looks the app up on APKMirror after you enable it. Paste a listing URL to skip the search.'],
  found: ['ok', 'check', 'APKMirror page found', ''],
  manual: ['ok', 'check', 'Using your APKMirror listing URL', ''],
  not_found: ['warn', 'warning', 'APKMirror page not found', 'Paste the APKMirror listing URL to continue.'],
  ambiguous: ['warn', 'warning', 'Several APKMirror pages match', 'Pick one in Apps after enabling, or paste the listing URL here.'],
  failed: ['err', 'error', 'APKMirror lookup failed', ''],
  blocked: ['warn', 'warning', 'APKMirror lookup is blocked for now', ''],
};
const DL_NONE = ['info', 'info', 'Download source', 'Relume looks the app up on APKMirror after you enable it. Paste a listing URL to skip the search.'];

const S = { sort: 'bundles', origin: '', q: '', category: '', page: 1, apps: [], total: 0, categories: [], feed: null, universe: null, open: null };
let D = null;          // the open panel: { pkg, meta, bundleId, flips, am, am0, ap, ap0, filter }
let seq = 0;           // drops list responses that a newer request has overtaken
const featured = new Map();
const gallery = $('#gallery'), detail = $('#detail');

const stars = n => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const starBadge = n => (n ? html`<span class="row" style="gap:3px">${icon('star')}${stars(n)}</span>` : '');
const authorColor = s => AUTHOR_COLORS[[...s].reduce((h, c) => h + c.charCodeAt(0), 0) % AUTHOR_COLORS.length];

// ---------- header, notices, stats ----------
function renderNotice() {
  const feed = S.feed;
  const errs = [...feed.feeds.filter(f => f.last_error).map(f => `Catalog feed ${f.feed}: ${f.last_error}`),
    ...feed.sources.filter(s => s.last_error).map(s => `Source ${s.repo}: ${s.last_error}`)];
  const skipped = feed.feeds.flatMap(f => f.skipped.map(s => `${f.feed} skipped ${s.where}${s.repo ? ` (${s.repo})` : ''}: ${s.error}`));
  $('#notice').innerHTML = html`${errs.length ? html`<div class="msgbar err">${icon('error')}<div class="grow"><div class="strong">The last catalog refresh failed</div>${errs.map(e => html`<div>${e}</div>`)}</div></div>` : ''}${skipped.length ? html`<div class="msgbar warn">${icon('warning')}<div class="grow"><details><summary>${plural(skipped.length, 'catalog entry', 'catalog entries')} skipped as invalid</summary>${skipped.map(e => html`<div class="mono">${e}</div>`)}</details></div></div>` : ''}`;
}

function renderStats() {
  const feed = S.feed, official = feed.feeds.filter(f => f.origin === 'official');
  const okAt = official.map(f => f.last_ok_at).filter(Boolean).sort().pop();
  const builtin = feed.sources.find(s => s.origin === 'builtin');
  const custom = feed.sources.filter(s => s.origin === 'custom').length;
  $('#stats').innerHTML = html`<span><b>${S.universe.toLocaleString('en')}</b> apps</span><span><b>${official.reduce((n, f) => n + f.bundle_count, 0).toLocaleString('en')}</b> community bundles</span><span>${okAt ? html`Official catalog refreshed <b>${fmt.rel(okAt)}</b>` : 'Official catalog never refreshed'}</span>${builtin ? html`<span>Built-in <span class="mono">${builtin.repo}</span></span>` : ''}<span><b>${custom}</b> custom source${custom === 1 ? '' : 's'}</span>`;
}

// ponytail: the list API carries no description, so the blurb falls back to bundle count and category
function featureCard(a, kicker, big) {
  featured.set(a.package, a);
  const blurb = a.description || `${plural(a.bundle_count, 'bundle')}${a.category ? ` for ${a.category.toLowerCase()}` : ''}.`;
  return html`<button class="feature" type="button" data-feature="${a.package}" data-cat="${catSlug(a.category)}">
${big ? html`<div class="row" style="justify-content:space-between;align-items:flex-start;position:relative"><div style="display:grid;gap:10px"><span class="kicker">${icon('sparkle', 'i-16')}${kicker}</span><h2 class="title2">${a.name || a.package}</h2><p>${blurb}</p></div>${iconTile(a, 's96')}</div>`
  : html`<div style="display:grid;gap:10px;position:relative"><span class="kicker">${kicker}</span><div class="row" style="gap:14px">${iconTile(a, 's72')}<h2 class="subtitle1">${a.name || a.package}</h2></div><p>${blurb}</p></div>`}
<div class="row wrap" style="position:relative"><span class="chip-w">${plural(a.bundle_count, 'bundle')}</span>${big && a.stars ? html`<span class="chip-w">${icon('star', 'i-16')}${stars(a.stars)} top bundle</span>` : ''}${a.enabled ? html`<span class="chip-w">${icon('check', 'i-16')}Enabled</span>` : ''}</div></button>`;
}

// Spec §2.7: top 3 by bundle count, the built-in apps, the 3 most recently updated (each app once, first label wins).
async function renderHero() {
  const top = async qs => (await api('GET', `/api/marketplace?${qs}`)).apps;
  const [most, builtin, recent] = await Promise.all([top('sort=bundles&per_page=3'), top('origin=builtin&sort=bundles&per_page=100'), top('sort=updated&per_page=3')]);
  featured.clear();
  const seen = new Set(), cards = [];
  [[most, 'Most bundles'], [builtin, 'Built-in'], [recent, 'Recently updated']].forEach(([apps, kicker]) => apps.forEach(a => {
    if (seen.has(a.package)) return;
    seen.add(a.package);
    cards.push(featureCard(a, cards.length ? kicker : 'Featured · most bundles', !cards.length));
  }));
  $('#hero').hidden = !cards.length;
  $('#hero').innerHTML = html`${cards}`;
}

// ---------- gallery ----------
function renderCats() {
  $('#cats').innerHTML = html`<button class="pill" type="button" aria-pressed="${S.category === '' ? 'true' : 'false'}" data-f="">All <span class="n">${S.universe.toLocaleString('en')}</span></button>${S.categories.map(c => html`<button class="pill" type="button" aria-pressed="${S.category === c.name ? 'true' : 'false'}" data-f="${c.name}" data-cat="${catSlug(c.name)}"><span class="dot"></span>${c.name} <span class="n">${c.count}</span></button>`)}`;
}

function card(a) {
  const builtinOnly = a.origins.length === 1 && a.origins[0] === 'builtin';
  return html`<button class="app" type="button" data-pkg="${a.package}" data-cat="${catSlug(a.category)}" aria-expanded="${S.open === a.package ? 'true' : 'false'}">
${a.enabled ? html`<span class="corner badge ok">${icon('check')}Enabled</span>` : ''}${iconTile(a)}
<div><div class="name">${a.name || a.package}</div><div class="cat">${a.category || ''}</div></div>
<div class="meta">${builtinOnly ? html`<span class="origin builtin">Built-in</span>` : html`<span>${plural(a.bundle_count, 'bundle')}</span>${a.origins.includes('custom') ? html`<span class="origin custom">Custom</span>` : ''}`}${starBadge(a.stars)}</div></button>`;
}

function renderGallery() {
  $$('.app', gallery).forEach(c => c.remove());
  detail.insertAdjacentHTML('beforebegin', html`${S.apps.map(card)}`);
  const none = !S.apps.length;
  const e = $('#empty');
  e.hidden = !none;
  if (none) {
    e.innerHTML = html`${S.universe === 0
      ? html`${icon('store', 'i-48')}<h2 class="subtitle1">The Marketplace is empty</h2><p class="subtle-text">The app catalog has not been downloaded yet. Refresh it to list the community apps and bundles.</p><button class="btn primary" type="button" data-act="refresh">${icon('sync')}Refresh catalog</button>`
      : html`${icon('search', 'i-48')}<h2 class="subtitle1">No apps match</h2><p class="subtle-text">Change the search, category or origin filter.</p>`}`;
  }
  $('#count').textContent = none ? '' : `Showing ${S.apps.length} of ${S.total.toLocaleString('en')}`;
  $('#more').hidden = S.apps.length >= S.total;
  placeDetail();
}

const cols = () => getComputedStyle(gallery).gridTemplateColumns.split(' ').length;
function placeDetail() {
  const cards = $$('.app', gallery), i = cards.findIndex(c => c.dataset.pkg === S.open);
  if (i < 0) { detail.hidden = true; return; }
  const n = cols();
  cards[Math.min(cards.length - 1, Math.floor(i / n) * n + n - 1)].after(detail);
  detail.hidden = false;
}

async function loadList(reset) {
  if (reset) { S.page = 1; }
  const mine = ++seq;
  const p = new URLSearchParams({ sort: S.sort, page: S.page, per_page: PER_PAGE });
  for (const k of ['origin', 'q', 'category']) if (S[k]) p.set(k, S[k]);
  const d = await api('GET', `/api/marketplace?${p}`);
  if (mine !== seq) return;
  S.apps = reset ? d.apps : [...S.apps, ...d.apps];
  S.total = d.total;
  S.categories = d.categories;
  S.feed = d.feed;
  if (!S.origin && !S.q && !S.category) S.universe = d.total;
  else if (S.universe === null) S.universe = (await api('GET', '/api/marketplace?per_page=1')).total;
  renderNotice();
  renderStats();
  renderCats();
  renderGallery();
}

async function loadSources() {
  const { sources } = await api('GET', '/api/sources');
  $('#sources').hidden = !sources.length;
  $('#source-list').innerHTML = html`${sources.map(s => html`<div class="src-row"><div class="grow"><div class="strong">${s.name || s.repo}</div><div class="mono subtle-text">${s.repo}</div>${s.packages.length ? html`<div class="caption">For ${s.packages.join(', ')}</div>` : ''}</div>
<span class="origin custom">${ENGINE[s.engine] || s.engine}</span>${s.list_tag ? html`<span class="badge">${s.list_tag}</span>` : ''}${s.list_error ? html`<span class="badge err" title="${s.list_error}">${icon('error')}${s.list_error}</span>` : ''}
<button class="icon-btn" type="button" data-del="${s.bundle_id}" data-repo="${s.repo}" aria-label="Delete source ${s.repo}" title="Delete source">${icon('delete')}</button></div>`)}`;
}

async function loadAll() {
  await Promise.all([renderHero(), loadList(true), loadSources()]);
}

// ---------- expand-in-place panel ----------
const cur = () => D.meta.bundles.find(b => b.bundle_id === D.bundleId);
const isOn = p => (p.name in D.flips ? D.flips[p.name] : p.default_enabled === true);

function bundleCard(b) {
  const ok = b.selectable;
  return html`<label class="bundle"><input type="radio" name="bundle" value="${b.bundle_id}" data-bundle ${b.bundle_id === D.bundleId ? html`checked` : ''} ${ok ? '' : html`disabled`}>
<div class="row"><span class="au" style="background:${authorColor(b.author || b.name)}">${(b.author || b.name || '?')[0].toUpperCase()}</span><div class="grow"><div class="strong">${b.name}</div><div class="mono subtle-text">${b.repo}</div></div></div>
<div class="row wrap"><span class="origin ${b.origin}">${ORIGIN[b.origin] || b.origin}</span>${b.engine === 'morphe' ? '' : html`<span class="caption">${ENGINE[b.engine] || b.engine}</span>`}</div>
${ok ? '' : html`<div class="caption">${b.not_selectable_reason}</div>`}
<div class="stat">${b.stars != null ? html`<span>${icon('star')}${stars(b.stars)}</span>` : ''}<span>${b.patches.length ? plural(b.patches.length, 'patch', 'patches') : 'Module, no patch list'}</span>${b.updated_at ? html`<span>${fmt.rel(b.updated_at)}</span>` : ''}</div></label>`;
}

function patchRow(p) {
  return html`<div class="patch" data-n="${p.name}"><div class="grow"><div class="pn">${p.name} ${p.default_enabled === false ? html`<span class="badge" style="height:18px">Default off</span>` : ''}${p.default_enabled === null ? html`<span class="badge" style="height:18px">Default unknown</span>` : ''} <span class="badge lav" style="height:18px" data-chg hidden>Changed by you</span></div><div class="pd">${p.description || ''}</div></div>
<label class="switch"><input type="checkbox" data-patch="${p.name}" aria-label="${p.name}" ${isOn(p) ? html`checked` : ''}><span class="track"></span></label></div>`;
}

function patchesSection() {
  const b = cur();
  if (!b) return html`<div class="msgbar info">${icon('info')}<div class="grow">Choose a bundle to see its patches.</div></div>`;
  if (!b.patches.length) return html`<div class="msgbar info">${icon('info')}<div class="grow">${b.name} has no patch list for this app. It is applied as it is.</div></div>`;
  return html`<div class="patches"><div class="patches-bar">
<label class="input grow" style="min-width:180px;max-width:320px">${icon('search', 'i-16')}<span class="sr-only">Filter patches</span><input type="search" data-r="filter" placeholder="Filter ${b.patches.length} patches" value="${D.filter}"></label>
<span class="badge brand" data-r="on"></span><span class="badge" data-r="off"></span><span class="badge lav" data-r="chg" hidden></span><div class="grow"></div>
<button class="btn subtle small" type="button" data-act="allon">All on</button><button class="btn subtle small" type="button" data-act="reset">Reset to defaults</button></div>
<div class="patch-list">${b.patches.map(patchRow)}</div></div>`;
}

function marks() {
  const b = cur();
  if (!b || !b.patches.length) { updateBar(); return; }
  const on = b.patches.filter(isOn).length, chg = Object.keys(D.flips).length;
  $('[data-r=on]', detail).textContent = `${on} on`;
  $('[data-r=off]', detail).textContent = `${b.patches.length - on} off`;
  const c = $('[data-r=chg]', detail);
  c.hidden = !chg;
  c.textContent = `${chg} changed by you`;
  $$('.patch', detail).forEach(r => {
    const changed = r.dataset.n in D.flips;
    r.classList.toggle('changed', changed);
    $('[data-chg]', r).hidden = !changed;
  });
  updateBar();
}

function updateBar() {
  const b = cur(), btn = $('[data-r=enable]', detail);
  if (!btn) return;
  btn.disabled = !b;
  const on = b ? b.patches.filter(isOn).length : 0;
  $('[data-r=sum]', detail).textContent = b ? `${b.name}${b.patches.length ? ` · ${plural(on, 'patch', 'patches')} on` : ''} · version Auto (newest the bundle supports)` : 'Choose a patch bundle';
}

function factsHtml() {
  const m = D.meta, b = cur(), counts = {};
  m.bundles.forEach(x => { counts[x.origin] = (counts[x.origin] || 0) + 1; });
  const dl = DL[m.download && m.download.state] || DL_NONE;
  return html`<dl class="facts"><dt>Category</dt><dd>${m.category || '—'}</dd>
<dt>Bundles</dt><dd>${Object.entries(counts).map(([o, n]) => `${n} ${ORIGIN[o].toLowerCase()}`).join(', ')}</dd>
${b && b.versions.length ? html`<dt>Versions</dt><dd class="mono">${b.versions.length > 1 ? `${b.versions[b.versions.length - 1]} – ${b.versions[0]}` : b.versions[0]}</dd>` : ''}
<dt>APKMirror</dt><dd><span class="badge ${dl[0] === 'info' ? '' : dl[0]}">${icon(dl[1])}${dl[2]}</span></dd></dl>`;
}

function sourceStep() {
  const dl = D.meta.download, st = DL[dl && dl.state] || DL_NONE;
  return html`<div class="src-box"><div class="msgbar ${st[0]}" role="status">${icon(st[1])}<div class="grow"><div class="strong">${st[2]}</div>${st[3] ? html`<div>${st[3]}</div>` : ''}${dl && dl.error ? html`<div>${dl.error}</div>` : ''}${dl && dl.retry_at ? html`<div>Next try ${fmt.abs(dl.retry_at)}</div>` : ''}</div></div>
<div class="field"><label for="u-am">APKMirror listing URL <span class="subtle-text" style="font-weight:400">(optional)</span></label>
<div class="row"><span class="input grow">${icon('link', 'i-16')}<input id="u-am" data-r="am" value="${D.am}" placeholder="https://www.apkmirror.com/apk/org/app/" autocomplete="off"></span><button class="btn" type="button" data-act="paste" aria-label="Paste from clipboard">${icon('paste')}Paste</button></div><div class="hint">The page that lists all versions of the app, not a single release.</div></div>
<div class="field"><label for="u-ap">APKPure URL <span class="subtle-text" style="font-weight:400">(optional fallback)</span></label>
<span class="input">${icon('link', 'i-16')}<input id="u-ap" data-r="ap" value="${D.ap}" placeholder="https://apkpure.com/…/${D.pkg}" autocomplete="off"></span></div></div>`;
}

const patchTitle = () => `Patches in ${cur() ? cur().name : 'the bundle'} for ${D.meta.name || D.meta.package}`;

function renderDetail() {
  const m = D.meta;
  const aside = html`<aside>${iconTile(m, 's96')}<div><h2 class="title3" id="d-name">${m.name || m.package}</h2><div class="mono subtle-text" style="margin-top:4px">${m.package}</div></div>
${m.description ? html`<p class="subtle-text">${m.description}</p>` : ''}<div data-r="facts">${factsHtml()}</div>
<button class="btn subtle" type="button" data-act="close" style="justify-content:flex-start;min-width:0;padding:0" aria-label="Close app details">${icon('close')}Close</button></aside>`;
  const work = m.enabled
    ? html`<div class="work"><div class="msgbar ok">${icon('check')}<div class="grow"><div class="strong">${m.name || m.package} is enabled</div><div>Change its bundle, patches or download source in <a href="/apps.html?app=${m.app_id}">Apps</a>.</div></div></div></div>`
    : html`<div class="work">
<div><div class="step-h"><span class="step-n">1</span><h3 class="subtitle2">Choose one patch bundle</h3><span class="caption">One bundle per app · you can switch later in Apps</span></div><div class="bundles" role="radiogroup" aria-label="Patch bundle">${m.bundles.map(bundleCard)}</div></div>
<div><div class="step-h"><span class="step-n">2</span><h3 class="subtitle2" data-r="ptitle">${patchTitle()}</h3><span class="caption">Defaults come from the bundle · your changes stay across versions</span></div><div data-r="patchsec">${patchesSection()}</div></div>
<div><div class="step-h"><span class="step-n">3</span><h3 class="subtitle2">Download source</h3></div>${sourceStep()}</div>
<div class="enable-bar"><div class="grow"><div class="strong" data-r="sum"></div><div class="caption">Enabling builds the app right away. A download URL is optional.</div></div>
<button class="btn primary large" type="button" data-r="enable" data-act="enable">${icon('check')}Enable and build</button></div></div>`;
  detail.dataset.cat = catSlug(m.category);
  detail.setAttribute('aria-labelledby', 'd-name');
  detail.innerHTML = html`${aside}${work}`;
  if (!m.enabled) marks();
}

async function openApp(pkg) {
  if (S.open === pkg) { closeApp(); return; }
  const meta = await act('GET', `/api/marketplace/${encodeURIComponent(pkg)}`);
  const sel = meta.bundles.find(b => b.selectable);
  const dl = meta.download || {};
  D = { pkg, meta, bundleId: sel ? sel.bundle_id : null, flips: {}, filter: '', am: dl.apkmirror_list_url || '', ap: dl.apkpure_url || '' };
  D.am0 = D.am;
  D.ap0 = D.ap;
  S.open = pkg;
  $$('.app', gallery).forEach(c => c.setAttribute('aria-expanded', c.dataset.pkg === pkg ? 'true' : 'false'));
  renderDetail();
  placeDetail();
  detail.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function closeApp() {
  S.open = null;
  D = null;
  $$('.app', gallery).forEach(c => c.setAttribute('aria-expanded', 'false'));
  detail.hidden = true;
}

function setFlip(name, on) {
  const p = cur().patches.find(x => x.name === name);
  if (on === (p.default_enabled === true)) delete D.flips[name];
  else D.flips[name] = on;
}

async function enable() {
  const body = { package: D.pkg, bundle_id: D.bundleId };
  if (Object.keys(D.flips).length) body.patches = D.flips;
  const download = {};
  if (D.am.trim() !== D.am0) download.apkmirror_list_url = D.am.trim();
  if (D.ap.trim() !== D.ap0) download.apkpure_url = D.ap.trim();
  if (Object.keys(download).length) body.download = download;
  const btn = $('[data-r=enable]', detail), name = D.meta.name || D.pkg;
  btn.disabled = true;
  try {
    await act('POST', '/api/apps', body, `Enabled ${name}, build queued`);
  } catch (e) {
    btn.disabled = false;       // act() already showed the server message
    return;
  }
  closeApp();
  await Promise.all([renderHero(), loadList(true)]);
}

// ---------- events ----------
gallery.addEventListener('click', e => {
  const c = e.target.closest('.app');
  if (c) openApp(c.dataset.pkg).catch(() => {});   // act() toasted the failure
});
detail.addEventListener('click', e => {
  const a = e.target.closest('[data-act]');
  if (!a) return;
  const b = cur();
  if (a.dataset.act === 'paste') {
    navigator.clipboard.readText().then(t => { const i = $('[data-r=am]', detail); i.value = t.trim(); D.am = i.value; }).catch(err => toast(`Cannot read the clipboard: ${err.message}`, 'error'));
    return;
  }
  if (a.dataset.act === 'close') closeApp();
  else if (a.dataset.act === 'enable') enable();
  else if (a.dataset.act === 'allon' || a.dataset.act === 'reset') {
    b.patches.forEach(p => setFlip(p.name, a.dataset.act === 'allon' ? true : p.default_enabled === true));
    $$('input[data-patch]', detail).forEach(i => { i.checked = isOn(b.patches.find(p => p.name === i.dataset.patch)); });
    marks();
  }
});
detail.addEventListener('change', e => {
  const t = e.target;
  if (t.matches('[data-bundle]')) {
    D.bundleId = Number(t.value);
    D.flips = {};
    D.filter = '';
    $('[data-r=patchsec]', detail).innerHTML = html`${patchesSection()}`;
    $('[data-r=facts]', detail).innerHTML = html`${factsHtml()}`;
    $('[data-r=ptitle]', detail).textContent = patchTitle();
    marks();
  } else if (t.matches('[data-patch]')) {
    setFlip(t.dataset.patch, t.checked);
    marks();
  }
});
detail.addEventListener('input', e => {
  const t = e.target, r = t.dataset.r;
  if (r === 'filter') {
    D.filter = t.value;
    const q = t.value.toLowerCase();
    $$('.patch', detail).forEach(row => { row.hidden = !row.dataset.n.toLowerCase().includes(q); });
  } else if (r === 'am') D.am = t.value;
  else if (r === 'ap') D.ap = t.value;
});

$('#hero').addEventListener('click', e => {
  const f = e.target.closest('[data-feature]');
  if (!f) return;
  const a = featured.get(f.dataset.feature);
  S.q = a.name || a.package;
  S.origin = S.category = '';
  $('#q').value = S.q;
  $('#origin').value = '';
  closeApp();
  loadList(true).then(() => openApp(a.package)).catch(err => console.error('open featured app:', err));
});

$('#cats').addEventListener('click', e => {
  const p = e.target.closest('.pill');
  if (!p) return;
  S.category = p.dataset.f;
  closeApp();
  loadList(true).catch(err => console.error('category filter:', err));
});
const onFilter = fn => e => { fn(e.target.value); closeApp(); loadList(true).catch(err => console.error('filter:', err)); };
$('#q').addEventListener('input', onFilter(v => { S.q = v.trim(); }));
$('#origin').addEventListener('change', onFilter(v => { S.origin = v; }));
$('#sort').addEventListener('change', onFilter(v => { S.sort = v; }));
$('#more').addEventListener('click', () => { S.page += 1; loadList(false).catch(err => console.error('load more:', err)); });
addEventListener('resize', placeDetail);

async function refreshCatalog() {
  const btns = [$('#refresh'), ...$$('[data-act=refresh]')];
  btns.forEach(b => { b.disabled = true; });
  toast('Refreshing catalog…');
  try {
    await act('POST', '/api/catalog/refresh', {}, 'Catalog refreshed');
  } catch (err) {
    console.error('catalog refresh:', err);   // act() showed the failure, the feed error text follows from the reload
  }
  await loadAll();
  $('#refresh').disabled = false;
}
$('#refresh').addEventListener('click', refreshCatalog);
$('#empty').addEventListener('click', e => { if (e.target.closest('[data-act=refresh]')) refreshCatalog(); });

// ---------- custom sources ----------
const engine = () => $('input[name=eng]:checked').value;
$$('input[name=eng]').forEach(r => r.addEventListener('change', () => { $('#c-mod').hidden = engine() === 'morphe'; }));
$('#add-source').addEventListener('click', async () => {
  const dlg = $('#dlg-custom');
  if (await ask(dlg) !== 'ok') return;
  const body = { url: $('#c-url').value.trim(), engine: engine() };
  if (engine() !== 'morphe') {
    body.asset_regex = $('#c-regex').value.trim();
    body.packages = $('#c-pkgs').value.split(/[\s,]+/).filter(Boolean);
  }
  toast('Adding source…');
  try {
    await act('POST', '/api/sources', body, 'Source added');
  } catch (err) {
    console.error('add source:', err);   // act() showed the server message
    return;
  }
  $('#c-url').value = $('#c-regex').value = $('#c-pkgs').value = '';
  await loadAll();
});
$('#source-list').addEventListener('click', async e => {
  const b = e.target.closest('[data-del]');
  if (!b) return;
  $('#dd-msg').textContent = `Remove ${b.dataset.repo} from Marketplace? Apps that use it must switch bundle first.`;
  if (await ask($('#dlg-del')) !== 'ok') return;
  try {
    await act('DELETE', `/api/sources/${Number(b.dataset.del)}`, null, 'Source deleted');
  } catch (err) {
    console.error('delete source:', err);   // act() showed the server message (409 source_in_use lists the apps)
    return;
  }
  await loadAll();
});

shell();
loadAll().catch(err => {
  console.error('marketplace load:', err);
  toast(err.message, 'error');
});
