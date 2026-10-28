import { $, $$, act, api, every, fillDownloads, fmt, html, icon, iconTile, shell, STATUS, toast } from '/static/app.js';

const REFRESH_MS = 5000;
const COLOR = /^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$/;
const ABIS = ['universal', 'arm64-v8a', 'armeabi-v7a', 'x86', 'x86_64'];
const ENGINE_LABEL = { lspatch: 'Xposed, LSPatch', npatch: 'Xposed, NPatch' };
const ORIGIN = { official: 'Official catalog', builtin: 'Built-in', custom: 'Custom' };
const MODE_TEXT = { auto: 'Auto', pinned: 'Pinned', latest: 'Latest' };
const FILTERS = [
  { key: 'all', label: 'All apps', icon: 'apps', tint: 'brand' },
  { key: 'upd', label: 'Update available', icon: 'sparkle', tint: 'lav' },
  { key: 'run', label: 'Building', icon: 'rebuild', tint: 'brand' },
  { key: 'fail', label: 'Failed', icon: 'error', tint: 'err' },
  { key: 'url', label: 'Needs URL', icon: 'link', tint: 'warn' },
];
const TINT = {
  brand: 'background:var(--colorBrandBackground2);color:var(--colorBrandForeground1)',
  lav: 'background:var(--colorPaletteLavenderBackground2);color:var(--colorPaletteLavenderForeground2)',
  err: 'background:var(--colorStatusDangerBackground1);color:var(--colorStatusDangerForeground1)',
  warn: 'background:var(--colorStatusWarningBackground1);color:var(--colorStatusWarningForeground1)',
};
const DL_TEXT = {
  found: ['ok', 'Found automatically'],
  manual: ['ok', 'Set by you'],
  pending: ['', 'Looked up on the next update run'],
  not_found: ['warn', 'Not found on APKMirror'],
  ambiguous: ['warn', 'Several APKMirror matches'],
  failed: ['err', 'Lookup failed'],
  blocked: ['err', 'APKMirror lookup blocked'],
};
const LOCATABLE = new Set(['pending', 'not_found', 'ambiguous', 'failed', 'blocked']);

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const clone = v => JSON.parse(JSON.stringify(v));
const S = { apps: [], followed: [], filter: 'all', q: '', menuOpen: false };
let D = null; // draft of the open drawer

// ---------- list ----------

const filterOf = a => {
  const s = a.status.state;
  if (s === 'queued' || s === 'building') return 'run';
  if (s === 'failed') return 'fail';
  if (s === 'needs_download_url') return 'url';
  if (s === 'update_available') return 'upd';
  return null;
};

function statusCell(a) {
  const s = a.status, st = STATUS[s.state], dl = a.download || {};
  if (s.state === 'building' || s.state === 'queued') {
    const p = s.progress, pct = p && p.count ? Math.round((100 * p.index) / p.count) : 0;
    return html`<div class="row"><span class="badge brand">${s.state === 'queued' ? 'Queued' : `Building · ${pct} %`}</span>${p && p.step ? html`<span class="caption">${p.step}</span>` : ''}</div>${s.state === 'building' ? html`<div class="progress" role="progressbar" aria-label="${a.name} build progress" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></div>` : ''}`;
  }
  let line;
  if (s.state === 'failed') {
    line = html`${s.fail_detail || s.fail_code || 'Build failed'} · <a href="/builds.html?job=${s.job_id}">View log</a>`;
  } else if (s.state === 'needs_download_url') {
    line = dl.state === 'ambiguous'
      ? html`${dl.candidates.length} APKMirror matches · <button class="link" type="button" data-act="url">Pick one</button>`
      : html`${dl.error || 'No download URL yet'} · <button class="link" type="button" data-act="url">Add URL</button>`;
  } else {
    line = a.app_version ? `Built ${a.app_version}` : 'Never built';
  }
  const label = s.state === 'needs_download_url' && dl.state === 'ambiguous' ? 'Pick one' : st.label;
  return html`<span><span class="badge ${st.cls}">${st.icon ? icon(st.icon) : ''}${label}</span></span><span class="caption">${line}</span>`;
}

function row(a) {
  const s = a.status.state, busy = s === 'building' || s === 'queued', noUrl = s === 'needs_download_url';
  const rebuildLabel = busy ? 'Build running' : noUrl ? 'Needs download URL' : s === 'failed' ? 'Retry build' : s === 'update_available' ? 'Rebuild with update' : 'Rebuild';
  const b = a.bundle;
  return html`<div class="arow ${D && D.app.id === a.id ? 'selected' : ''}" data-id="${a.id}">
<div class="row" style="gap:14px">${iconTile(a, 's48')}<div class="grow"><div class="nm"><a href="?app=${a.id}" data-act="settings">${a.name}</a></div><div class="mono subtle-text">${a.package}</div></div></div>
<div><div class="mono">${a.app_version || '—'}</div><div class="caption">${MODE_TEXT[a.version_mode] || a.version_mode}</div></div>
<div><div class="strong">${b.name}</div><div class="row" style="gap:6px;margin-top:2px"><span class="origin ${b.origin}">${ORIGIN[b.origin] || b.origin}</span><span class="mono subtle-text">${b.tag || ''}</span></div></div>
<div class="st">${statusCell(a)}</div>
<div class="acts">
<button class="icon-btn" type="button" data-act="check" aria-label="Check ${a.name} for updates" title="Check for updates" data-tip="Check for updates">${icon('sync')}</button>
<button class="icon-btn ${s === 'update_available' ? 'outline' : ''}" type="button" data-act="build" aria-label="${rebuildLabel}: ${a.name}" title="${rebuildLabel}" data-tip="${rebuildLabel}" ${busy || noUrl ? html`disabled` : ''}>${icon('rebuild')}</button>
<span class="dd"><button class="icon-btn" type="button" data-act="dl" aria-label="Download ${a.name} APK" title="Download APK" data-tip="Download APK" aria-haspopup="true">${icon('download')}</button></span>
<button class="icon-btn" type="button" data-act="settings" aria-label="${a.name} settings" title="App settings" data-tip="App settings">${icon('settings')}</button>
</div></div>`;
}

function followedRow(p) {
  const n = p.device_ids.length, tip = n ? `Install on ${n} assigned phone(s)` : 'Assign phones on Followed projects first';
  const noInstall = p.blocked || !p.version || !n;
  return html`<div class="arow" data-id="f${p.id}">
<div class="row" style="gap:14px">${iconTile(p, 's48')}<div class="grow"><div class="nm"><a href="/projects.html">${p.name}</a></div><div class="mono subtle-text">${p.package || p.repo}</div></div></div>
<div><div class="mono">${p.version || '—'}</div><div class="caption">Followed</div></div>
<div><span class="origin">${icon('follow')}Followed project</span><div class="mono subtle-text">github.com/${p.repo}</div></div>
<div class="st">${p.blocked ? html`<span><span class="badge err">Signer changed</span></span><span class="caption"><a href="/projects.html">Confirm on Followed projects</a></span>` : !p.version ? html`<span class="caption">Not downloaded yet</span>` : ''}</div>
<div class="acts">
<button class="icon-btn" type="button" data-act="install" aria-label="${tip}: ${p.name}" title="${tip}" data-tip="${tip}" ${noInstall ? html`disabled` : ''}>${icon('devices')}</button>
</div></div>`;
}

function renderList() {
  const counts = { all: S.apps.length + S.followed.length };
  for (const a of S.apps) { const f = filterOf(a); if (f) counts[f] = (counts[f] || 0) + 1; }
  $('#summary').innerHTML = html`${FILTERS.map(f => html`<button class="sum" type="button" data-filter="${f.key}" aria-pressed="${String(S.filter === f.key)}"><span class="ic" style="${TINT[f.tint]}">${icon(f.icon)}</span><span><span class="n">${counts[f.key] || 0}</span><br><span class="caption">${f.label}</span></span></button>`)}`;
  const q = S.q.trim().toLowerCase();
  const rows = S.apps.filter(a => (S.filter === 'all' || filterOf(a) === S.filter)
    && (!q || `${a.name} ${a.package}`.toLowerCase().includes(q)));
  const fol = S.filter === 'all' ? S.followed.filter(p => !q || `${p.name} ${p.package || ''} ${p.repo}`.toLowerCase().includes(q)) : [];
  $('#list').innerHTML = html`${rows.length || fol.length ? [...rows.map(row), ...fol.map(followedRow)]
    : html`<div class="empty">${S.apps.length || S.followed.length ? 'No app matches this filter.' : html`No app is enabled yet. <a href="/">Add one from the Marketplace</a>.`}</div>`}`;
}

async function loadList() {
  const r = await api('GET', '/api/apps');
  S.apps = r.apps;
  S.followed = r.followed;
  if (!S.menuOpen) renderList();
}

async function loadNextRun() {
  const s = await api('GET', '/api/schedule');
  $('#nextrun').textContent = !s.enabled ? 'Schedule is off' : s.next_run_at ? `Next scheduled run ${fmt.rel(s.next_run_at)}` : '';
}

const appOf = el => {
  const k = el.closest('[data-id]').dataset.id;
  return k[0] === 'f' ? S.followed.find(p => p.id === Number(k.slice(1))) : S.apps.find(a => a.id === Number(k));
};

async function openMenu(btn, a) {
  const host = btn.parentElement;
  S.menuOpen = true;
  host.insertAdjacentHTML('beforeend', html`<div class="menu" role="menu"><span class="caption">Loading…</span></div>`);
  await fillDownloads($('.menu', host), a.id);
}

function closeMenus() {
  S.menuOpen = false;
  $$('.menu').forEach(m => m.remove());
}

document.addEventListener('click', e => {
  if (S.menuOpen && !e.target.closest('.menu') && !e.target.closest('[data-act=dl]')) closeMenus();
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (S.menuOpen) closeMenus();
  else if (D) closeDrawer().catch(() => {});
});

$('#list').addEventListener('click', async e => {
  const t = e.target.closest('[data-act]');
  if (!t) return;
  const a = appOf(t);
  try {
    if (t.dataset.act === 'dl') {
      e.preventDefault();
      if ($('.menu', t.parentElement)) return closeMenus();
      closeMenus();
      return await openMenu(t, a);
    }
    if (t.dataset.act === 'settings') { e.preventDefault(); return await openDrawer(a.id); }
    if (t.dataset.act === 'url') return await openDrawer(a.id, 'sec-url');
    if (t.dataset.act === 'install') await act('POST', `/api/projects/${a.id}/install`, {}, `Installing ${a.name} ${a.version} on ${a.device_ids.length} phone(s)`);
    if (t.dataset.act === 'check') await act('POST', `/api/apps/${a.id}/check`, {}, `Checking ${a.name}`);
    if (t.dataset.act === 'build') await act('POST', `/api/apps/${a.id}/build`, {}, `Build queued for ${a.name}`);
    await loadList();
  } catch (_) { /* toast shown by act or api */ }
});
$('#summary').addEventListener('click', e => {
  const b = e.target.closest('[data-filter]');
  if (!b) return;
  S.filter = b.dataset.filter;
  renderList();
});
$('#q').addEventListener('input', e => { S.q = e.target.value; renderList(); });
$('#checkall').addEventListener('click', () => act('POST', '/api/schedule/run', {}, 'Checking all apps for updates').then(loadList).catch(() => {}));

// ---------- drawer: draft state ----------

const isModule = engine => engine === 'lspatch' || engine === 'npatch';
const chosenBundle = () => (D.market && D.market.bundles.find(b => b.bundle_id === D.bundleId)) || null;
const chosenEngine = () => (chosenBundle() ? chosenBundle().engine : D.app.bundle.engine);
const patchesEditable = () => D.patches && D.bundleId === D.orig.bundleId;

function fieldsOf(app) {
  const s = app.settings, d = app.download || {};
  return {
    enabled: app.enabled,
    bundleId: app.bundle.bundle_id,
    version: { mode: s.version.mode, pin: s.version.pin, allow_nearest: s.version.allow_nearest },
    variants: clone(s.variants),
    eo: clone(s.engine_options),
    deps: [...s.project_ids].sort((x, y) => x - y),
    name: s.display_name || '',
    dl: { apkmirror_list_url: d.apkmirror_list_url || '', apkpure_url: d.apkpure_url || '' },
  };
}

function overridesFrom(p) {
  const out = {};
  for (const x of p.patches) {
    const o = {};
    if (x.enabled !== x.default_enabled) o.enabled = x.enabled;
    const opts = {};
    for (const op of x.options) if (!same(op.value, op.default)) opts[op.key] = op.value;
    if (Object.keys(opts).length) o.options = opts;
    if (Object.keys(o).length) out[x.name] = o;
  }
  return out;
}

const effEnabled = x => (D.edit[x.name] && 'enabled' in D.edit[x.name] ? D.edit[x.name].enabled : x.default_enabled);
const effValue = (x, op) => {
  const o = D.edit[x.name] && D.edit[x.name].options;
  return o && op.key in o ? o[op.key] : op.default;
};

function setEnabled(x, on) {
  const o = D.edit[x.name] || {};
  if (on === x.default_enabled) delete o.enabled; else o.enabled = on;
  if (Object.keys(o).length) D.edit[x.name] = o; else delete D.edit[x.name];
}

function setValue(x, op, v) {
  const o = D.edit[x.name] || {};
  const opts = o.options || {};
  if (same(v, op.default)) delete opts[op.key]; else opts[op.key] = v;
  if (Object.keys(opts).length) o.options = opts; else delete o.options;
  if (Object.keys(o).length) D.edit[x.name] = o; else delete D.edit[x.name];
}

const dirty = () => !!D && (!same(D.cur, D.orig) || (patchesEditable() && !same(D.edit, D.saved)));

// ---------- drawer: sections ----------

function renderBundle() {
  const bs = D.market ? D.market.bundles : [D.app.bundle];
  const sec = $('#sec-bundle');
  sec.innerHTML = html`<h3 class="subtitle2" id="s-b">${icon('package')}Patch bundle</h3>
<div class="bpick" role="radiogroup" aria-labelledby="s-b">${bs.map(b => {
    const id = b.bundle_id, ok = b.selectable !== false || id === D.cur.bundleId;
    return html`<label class="bopt"><input type="radio" name="bd" data-f="bundle" value="${id}" ${id === D.cur.bundleId ? html`checked` : ''} ${ok ? '' : html`disabled`}><div class="grow"><div class="strong">${b.name}</div><div class="mono subtle-text">${[b.repo, b.tag || b.list_tag, ENGINE_LABEL[b.engine]].filter(Boolean).join(' · ')}</div>${ok ? '' : html`<div class="caption">${b.not_selectable_reason}</div>`}</div><span class="origin ${b.origin}">${ORIGIN[b.origin] || b.origin}</span></label>`;
  })}</div>
<p class="hint">Switching bundle keeps the patch choices whose names match in the new bundle.</p>`;
}

function stale() {
  return D.patches ? D.patches.stale_overrides : D.app.settings.stale_overrides;
}

function renderPatchNote() {
  const notes = [];
  if (D.bundleId !== D.orig.bundleId) notes.push(html`<div class="msgbar info">${icon('info')}<span>Save to load the patches of the new bundle. Same-name choices are kept.</span></div>`);
  else if (D.patches && D.patches.unavailable) notes.push(html`<div class="msgbar warn">${icon('warning')}<span>Patch list unavailable. Builds still use the saved choices. Check this app for updates to fetch it.</span></div>`);
  if (stale().length) notes.push(html`<div class="msgbar warn">${icon('warning')}<span>Kept but not in this bundle: ${stale().join(', ')}</span></div>`);
  $('#pnote').innerHTML = html`${notes}`;
}

function fieldFor(x, op, i, j) {
  const id = `o-${i}-${j}`, v = effValue(x, op), t = String(op.type || 'string');
  const data = html`data-f="opt" data-i="${i}" data-j="${j}"`;
  const label = html`<label for="${id}">${op.title || op.key} <span class="caption">${t}</span></label>`;
  const hint = html`<span class="hint">Default: ${JSON.stringify(op.default)}</span>`;
  if (op.values && op.values.length) {
    return html`<div class="field">${label}<span class="select"><select class="dropdown" id="${id}" ${data} data-kind="select">${op.values.map(pv => html`<option value="${JSON.stringify(pv)}" ${same(pv, v) ? html`selected` : ''}>${String(pv)}</option>`)}</select></span>${hint}</div>`;
  }
  if (/bool/i.test(t)) {
    return html`<label class="row" for="${id}"><span class="switch"><input type="checkbox" id="${id}" ${data} data-kind="bool" ${v ? html`checked` : ''}><span class="track"></span></span>${op.title || op.key}</label>`;
  }
  if (/list|\[\]/i.test(t)) {
    return html`<div class="field">${label}<textarea class="input mono" id="${id}" rows="3" ${data} data-kind="list">${(v || []).join('\n')}</textarea>${hint}</div>`;
  }
  if (/int|long|float|double/i.test(t)) {
    return html`<div class="field">${label}<span class="input"><input class="mono" type="number" ${/float|double/i.test(t) ? html`step="any"` : ''} id="${id}" ${data} data-kind="num" value="${v ?? ''}"></span>${hint}</div>`;
  }
  const pickFrom = COLOR.test(String(v ?? '')) ? v : COLOR.test(String(op.default ?? '')) ? op.default : null;
  return html`<div class="field">${label}<div class="row">${pickFrom ? html`<input type="color" data-color-for="${id}" value="${'#' + String(pickFrom).slice(-6)}" aria-label="Pick colour">` : ''}<span class="input grow"><input class="mono" id="${id}" ${data} data-kind="text" value="${v ?? ''}"></span></div>${hint}</div>`;
}

function patchRow(x, i) {
  const on = effEnabled(x), chg = on !== x.default_enabled;
  return html`<div class="pt-row" data-i="${i}"><div class="grow"><div class="strong">${x.name} <span class="badge lav" data-chg style="height:18px" ${chg ? '' : html`hidden`}>Changed · default ${x.default_enabled ? 'on' : 'off'}</span></div><div class="caption">${x.description || ''}${x.options.length ? ` ${x.options.length} option${x.options.length === 1 ? '' : 's'}.` : ''}</div></div><label class="switch"><input type="checkbox" data-f="patch" data-i="${i}" ${on ? html`checked` : ''} ${patchesEditable() ? '' : html`disabled`} aria-label="${x.name}"><span class="track"></span></label></div>${x.options.length ? html`<div class="pt-opts">${x.options.map((op, j) => fieldFor(x, op, i, j))}</div>` : ''}`;
}

function renderPatches() {
  const sec = $('#sec-patches');
  sec.hidden = isModule(chosenEngine()) && D.bundleId === D.orig.bundleId;
  renderPatchNote();
  const list = $('#plist');
  if (!D.patches || !D.patches.patches.length) {
    list.hidden = true;
    $('#pcount').textContent = '';
    return;
  }
  list.hidden = false;
  list.innerHTML = html`${D.patches.patches.map(patchRow)}`;
  filterPatches();
  patchMeta();
}

function filterPatches() {
  const q = $('#pfilter').value.trim().toLowerCase();
  $$('#plist .pt-row').forEach(r => {
    const x = D.patches.patches[Number(r.dataset.i)];
    const hit = !q || `${x.name} ${x.description || ''}`.toLowerCase().includes(q);
    r.hidden = !hit;
    if (r.nextElementSibling && r.nextElementSibling.classList.contains('pt-opts')) r.nextElementSibling.hidden = !hit;
  });
}

function patchMeta() {
  const p = D.patches.patches, on = p.filter(effEnabled).length;
  $('#pcount').textContent = `${on} on of ${p.length}`;
  $$('#plist .pt-row').forEach(r => {
    const x = p[Number(r.dataset.i)];
    $('[data-chg]', r).hidden = effEnabled(x) === x.default_enabled;
  });
}

function renderModule() {
  const sec = $('#sec-module'), eo = D.cur.eo;
  const engine = chosenEngine();
  sec.hidden = !(isModule(engine) && eo && D.bundleId === D.orig.bundleId);
  if (sec.hidden) return;
  const max = engine === 'npatch' ? 4 : 3;
  sec.innerHTML = html`<h3 class="subtitle2" id="s-m">${icon('package')}Xposed module</h3>
<div class="field"><label for="m-sig">Signature bypass level</label><span class="select"><select class="dropdown" id="m-sig" data-f="sig">${Array.from({ length: max + 1 }, (_, n) => html`<option value="${n}" ${n === eo.sigbypass ? html`selected` : ''}>${n}</option>`)}</select></span></div>
<label class="row"><span class="switch"><input type="checkbox" data-f="dex" ${eo.injectdex ? html`checked` : ''}><span class="track"></span></span>Inject loader dex</label>`;
}

function renderVersion() {
  const v = D.cur.version, s = D.app.settings.version, xposed = isModule(chosenEngine());
  const sup = s.supported, mirror = s.apkmirror.filter(m => !sup.includes(m));
  const modes = [
    ['auto', 'Auto', sup.length ? `Newest the bundle supports · ${sup[0]}` : 'No supported version listed'],
    ['pinned', 'Pinned', 'Stay on one version'],
    ['latest', 'Latest', 'Newest on APKMirror, ignores compatibility'],
  ].filter(m => !(xposed && m[0] === 'auto'));
  $('#sec-version').innerHTML = html`<h3 class="subtitle2" id="s-v">${icon('pin')}Version</h3>
<div class="modes" role="radiogroup" aria-labelledby="s-v">${modes.map(([k, t, c]) => html`<label class="mode"><input type="radio" name="vm" data-f="vmode" value="${k}" ${v.mode === k ? html`checked` : ''}><span class="strong">${t}</span><span class="caption">${c}</span></label>`)}</div>
<div class="field"><label for="v-pin">Pinned version</label><span class="input"><input id="v-pin" data-f="pin" list="v-list" placeholder="Choose a version" class="mono" value="${v.pin || ''}" ${v.mode === 'pinned' ? '' : html`disabled`}></span><datalist id="v-list">${sup.map(x => html`<option value="${x}">`)}${mirror.map(x => html`<option value="${x}">`)}</datalist><span class="hint" data-pinwarn>Used only in Pinned mode.</span></div>
<label class="row" style="justify-content:space-between"><span><span class="strong">Allow nearest version</span><br><span class="caption">If the chosen version is not on APKMirror, build the closest one.</span></span><span class="switch"><input type="checkbox" data-f="near" aria-label="Allow nearest version" ${v.allow_nearest ? html`checked` : ''}><span class="track"></span></span></label>`;
  pinWarn();
}

function pinWarn() {
  const w = $('#sec-version [data-pinwarn]'), v = D.cur.version, sup = D.app.settings.version.supported;
  if (!w) return;
  w.textContent = v.mode === 'pinned' && v.pin && sup.length && !sup.includes(v.pin)
    ? 'Not in the bundle\'s supported list. The build still runs and may fail to patch.' : 'Used only in Pinned mode.';
}

function renderVariants() {
  const xposed = isModule(chosenEngine());
  $('#sec-variants').innerHTML = html`<h3 class="subtitle2" id="s-a">${icon('package')}ABI variants</h3>
<div class="row wrap">${ABIS.map(a => html`<label class="pill"><input type="checkbox" data-f="abi" value="${a}" ${D.cur.variants.some(v => v.abi === a) ? html`checked` : ''}>${a}</label>`)}</div>
<p class="hint">${xposed ? 'Each checked ABI is built as its own variant.' : 'Each checked ABI is built as its own variant, stripped to that ABI.'}</p>`;
}

function renderDeps() {
  const ps = D.projects;
  $('#sec-deps').innerHTML = html`<h3 class="subtitle2" id="s-d">${icon('follow')}Depends on Followed projects</h3>
<p class="hint" style="margin-top:-6px">OTA push and install bring these along.</p>
${ps.length ? ps.map(p => html`<label class="dep"><input type="checkbox" data-f="dep" value="${p.id}" ${D.cur.deps.includes(p.id) ? html`checked` : ''}><span class="tile s32" data-cat="tools" aria-hidden="true">${p.label.slice(0, 2)}</span><span class="grow"><span class="strong">${p.label}</span><br><span class="mono subtle-text">${p.repo}${p.downloaded_tag ? ` · ${p.downloaded_tag}` : ''}</span></span></label>`) : html`<p class="caption">No followed project yet.</p>`}`;
}

function renderUrls() {
  const dl = D.app.download || { state: 'pending', candidates: [] };
  const [cls, text] = DL_TEXT[dl.state] || ['', dl.state];
  const retry = dl.state === 'blocked' && dl.retry_at ? ` until ${fmt.abs(dl.retry_at)}` : '';
  $('#sec-url').innerHTML = html`<h3 class="subtitle2" id="s-u">${icon('download')}Download URLs</h3>
<div class="field"><label for="u-am">APKMirror listing <span class="badge ${cls}" style="height:18px;margin-left:4px">${text}${retry}</span></label><div class="row"><span class="input grow">${icon('link')}<input id="u-am" data-f="dl" data-k="apkmirror_list_url" value="${D.cur.dl.apkmirror_list_url}" placeholder="https://www.apkmirror.com/uploads/?appcategory=…"></span>${LOCATABLE.has(dl.state) ? html`<button class="icon-btn outline" type="button" data-f="locate" aria-label="Locate the APKMirror page again" title="Locate again" data-tip="Locate again">${icon('search')}</button>` : ''}</div>${dl.error ? html`<span class="hint">${dl.error}</span>` : ''}</div>
${dl.state === 'ambiguous' ? html`<div class="field"><span class="strong">Pick the right APKMirror app</span>${dl.candidates.map((c, k) => html`<div class="dep"><span class="grow"><span class="strong">${c.title}</span><br><span class="mono subtle-text">${c.url}</span></span><button class="btn small" type="button" data-f="pick" data-k="${k}">Use this</button></div>`)}</div>` : ''}
<div class="field"><label for="u-ap">APKPure <span class="subtle-text" style="font-weight:400">(optional)</span></label><span class="input">${icon('link')}<input id="u-ap" data-f="dl" data-k="apkpure_url" value="${D.cur.dl.apkpure_url}" placeholder="https://apkpure.com/…"></span></div>`;
}

function renderName() {
  $('#sec-name').innerHTML = html`<h3 class="subtitle2" id="s-n">${icon('edit')}Display name</h3>
<div class="row"><span class="input grow"><input id="n-name" data-f="name" value="${D.cur.name}" placeholder="${D.app.name}" aria-label="Display name (empty = default)"></span><button class="btn" type="button" data-f="icon">Refresh icon</button></div>`;
}

function renderAll() {
  renderBundle();
  renderPatches();
  renderModule();
  renderVersion();
  renderVariants();
  renderDeps();
  renderUrls();
  renderName();
}

// ---------- drawer: open, edit, save ----------

async function openDrawer(id, scrollTo) {
  if (D && D.app.id !== id && !(await closeDrawer())) return;
  let app;
  try {
    app = await api('GET', `/api/apps/${id}`);
  } catch (e) {
    return toast(e.message, 'error');
  }
  const [market, projects, patches] = await Promise.all([
    api('GET', `/api/marketplace/${encodeURIComponent(app.package)}`).catch(() => null),
    api('GET', '/api/projects').then(r => r.projects),
    isModule(app.bundle.engine) ? null : api('GET', `/api/apps/${id}/patches`).catch(() => null),
  ]).catch(e => { toast(e.message, 'error'); return []; });
  if (!projects) return;
  const f = fieldsOf(app);
  D = { app, market, projects, patches, orig: f, cur: clone(f), get bundleId() { return this.cur.bundleId; } };
  D.saved = patches ? overridesFrom(patches) : {};
  D.edit = clone(D.saved);
  $('#dr-tile').innerHTML = html`${iconTile(app, 's48')}`;
  $('#dr-t').textContent = `${app.name} settings`;
  $('#dr-pkg').textContent = app.package;
  $('#d-en').checked = app.enabled;
  $('#pfilter').value = '';
  renderAll();
  $('#drawer').hidden = $('#scrim').hidden = false;
  history.replaceState(null, '', `?app=${id}`);
  renderList();
  if (scrollTo) document.getElementById(scrollTo).scrollIntoView({ block: 'start' });
}

async function closeDrawer() {
  if (dirty() && !confirm('Discard unsaved changes?')) return false;
  D = null;
  $('#drawer').hidden = $('#scrim').hidden = true;
  history.replaceState(null, '', location.pathname);
  renderList();
  return true;
}

function readField(el) {
  const k = el.dataset.kind;
  if (k === 'bool') return el.checked;
  if (k === 'select') return JSON.parse(el.value);
  if (k === 'num') return el.value === '' ? null : Number(el.value);
  if (k === 'list') return el.value.split('\n').map(s => s.trim()).filter(Boolean);
  return el.value;
}

// colour picker -> text: an 8-digit #AARRGGBB value keeps its alpha prefix
function colorInput(picker) {
  const text = document.getElementById(picker.dataset.colorFor);
  const old = text.value;
  text.value = /^#[0-9A-Fa-f]{8}$/.test(old) ? old.slice(0, 3) + picker.value.slice(1) : picker.value;
  text.dispatchEvent(new Event('input', { bubbles: true }));
}

function onEdit(e) {
  const t = e.target;
  if (t.matches('[data-color-for]')) return colorInput(t);
  const f = t.dataset.f;
  if (!f || !D) return;
  const c = D.cur;
  if (f === 'bundle') {
    c.bundleId = Number(t.value);
    renderPatches(); renderModule(); renderVersion(); renderVariants();
  } else if (f === 'patch') {
    setEnabled(D.patches.patches[Number(t.dataset.i)], t.checked);
    patchMeta();
  } else if (f === 'opt') {
    const x = D.patches.patches[Number(t.dataset.i)], op = x.options[Number(t.dataset.j)];
    setValue(x, op, readField(t));
    const pick = document.querySelector(`[data-color-for="${t.id}"]`);
    if (pick && COLOR.test(t.value)) pick.value = '#' + t.value.slice(-6);
    patchMeta();
  } else if (f === 'vmode') {
    c.version.mode = t.value;
    c.version.pin = t.value === 'pinned' ? (c.version.pin || null) : null;
    const pin = $('[data-f=pin]');
    pin.disabled = t.value !== 'pinned';
    if (t.value !== 'pinned') pin.value = '';
    pinWarn();
  } else if (f === 'pin') {
    c.version.pin = t.value.trim() || null;
    pinWarn();
  } else if (f === 'near') {
    c.version.allow_nearest = t.checked;
  } else if (f === 'sig') {
    c.eo.sigbypass = Number(t.value);
  } else if (f === 'dex') {
    c.eo.injectdex = t.checked;
  } else if (f === 'abi') {
    // reads t.checked, not a toggle: a checkbox fires both input and change, and both reach onEdit
    const a = t.value, has = c.variants.some(v => v.abi === a);
    if (has === t.checked) return;
    if (has && c.variants.length === 1) { t.checked = true; return toast('At least one ABI variant is required', 'error'); }
    if (has) c.variants = c.variants.filter(v => v.abi !== a);
    else c.variants.push({ ...clone(c.variants[0]), name: a, abi: a, strip_libs: a === 'universal' || isModule(chosenEngine()) ? null : a });
  } else if (f === 'dep') {
    const id = Number(t.value);
    c.deps = t.checked ? [...c.deps, id].sort((x, y) => x - y) : c.deps.filter(d => d !== id);
  } else if (f === 'dl') {
    c.dl[t.dataset.k] = t.value.trim();
  } else if (f === 'name') {
    c.name = t.value;
  }
}

async function onClickIn(e) {
  const t = e.target.closest('[data-f]');
  if (!t || !D) return;
  try {
    if (t.dataset.f === 'pick') {
      const cand = D.app.download.candidates[Number(t.dataset.k)];
      D.cur.dl.apkmirror_list_url = cand.list_url;
      $('[data-k=apkmirror_list_url]').value = cand.list_url;
    } else if (t.dataset.f === 'locate') {
      await act('POST', `/api/apps/${D.app.id}/locate`, {}, 'Locating the APKMirror page');
    } else if (t.dataset.f === 'icon') {
      await act('POST', `/api/apps/${encodeURIComponent(D.app.package)}/icon/refresh`, {}, 'Icon refreshed');
    }
  } catch (_) { /* toast shown by act */ }
}

const drawer = $('#drawer');
drawer.addEventListener('input', onEdit);
drawer.addEventListener('change', onEdit);
drawer.addEventListener('click', onClickIn);
$('#pfilter').addEventListener('input', () => D && D.patches && filterPatches());
$('#preset').addEventListener('click', () => {
  if (!patchesEditable()) return;
  D.edit = {};
  renderPatches();
  toast('Reset to bundle defaults (save to apply)');
});
$('#d-close').addEventListener('click', () => closeDrawer());
$('#d-cancel').addEventListener('click', () => closeDrawer());
$('#scrim').addEventListener('click', () => closeDrawer());
$('#d-en').addEventListener('change', e => { D.cur.enabled = e.target.checked; });
$('#d-save').addEventListener('click', () => save(false));
$('#d-saverun').addEventListener('click', () => save(true));
window.addEventListener('beforeunload', e => { if (dirty()) e.preventDefault(); });

function settingsBody() {
  const c = D.cur, o = D.orig, body = {};
  if (c.bundleId !== o.bundleId) body.bundle_id = c.bundleId;
  if (!same(c.version, o.version)) body.version = { ...c.version, pin: c.version.mode === 'pinned' ? c.version.pin : null };
  if (!same(c.variants, o.variants)) body.variants = c.variants;
  if (!same(c.eo, o.eo)) body.engine_options = c.eo;
  if (!same(c.deps, o.deps)) body.project_ids = c.deps;
  if (c.name !== o.name) body.display_name = c.name.trim() || null;
  const dl = {};
  for (const k of Object.keys(c.dl)) if (c.dl[k] !== o.dl[k]) dl[k] = c.dl[k] || null;
  if (Object.keys(dl).length) body.download = dl;
  return body;
}

async function save(rebuild) {
  const id = D.app.id, c = D.cur;
  try {
    if (!c.enabled && D.orig.enabled) {
      if (!confirm(`Disable ${D.app.name}? It stops updating and building. Its settings and builds are kept.`)) return;
      await act('DELETE', `/api/apps/${id}`, {}, `${D.app.name} disabled`);
      D = null;
      await closeDrawer();
      return await loadList();
    }
    if (c.enabled && !D.orig.enabled) {
      await act('POST', '/api/apps', { package: D.app.package, bundle_id: c.bundleId }, `${D.app.name} enabled`);
      D.orig.enabled = true;
      D.orig.bundleId = c.bundleId;
    }
    const body = settingsBody();
    if (Object.keys(body).length) {
      await api('PUT', `/api/apps/${id}`, body);
      D.orig = clone(c);
    }
    if (patchesEditable() && !same(D.edit, D.saved)) {
      await api('PUT', `/api/apps/${id}/patches`, { overrides: D.edit });
      D.saved = clone(D.edit);
    }
    if (rebuild) await api('POST', `/api/apps/${id}/build`);
    toast(rebuild ? 'Saved · build queued' : 'Saved');
    D = null;
    await closeDrawer();
    await loadList();
  } catch (e) {
    toast(e.message, 'error');
  }
}

shell();
const refresh = () => Promise.all([loadList(), loadNextRun()]).catch(e => toast(e.message, 'error'));
refresh().then(() => {
  const want = Number(new URLSearchParams(location.search).get('app'));
  if (want) return openDrawer(want);
});
every(REFRESH_MS, refresh);
