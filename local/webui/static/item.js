import { $, $$, act, api, ask, fillDownloads, fmt, html, iconTile, JOB_BADGE, shell, toast } from '/static/app.js';

const ID = Number(new URLSearchParams(location.search).get('id'));
const COLOR = /^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$/;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const clone = v => JSON.parse(JSON.stringify(v));
const S = { item: null, patches: null, saved: {}, edit: {}, version: null, vEdit: null };

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

const savedEnabled = x => (S.saved[x.name] && 'enabled' in S.saved[x.name] ? S.saved[x.name].enabled : x.default_enabled);
const savedValue = (x, op) => {
  const o = S.saved[x.name] && S.saved[x.name].options;
  return o && op.key in o ? o[op.key] : op.default;
};
const effEnabled = x => (S.edit[x.name] && 'enabled' in S.edit[x.name] ? S.edit[x.name].enabled : x.default_enabled);
const effValue = (x, op) => {
  const o = S.edit[x.name] && S.edit[x.name].options;
  return o && op.key in o ? o[op.key] : op.default;
};

function setEnabled(x, on) {
  const o = S.edit[x.name] || {};
  if (on === x.default_enabled) delete o.enabled; else o.enabled = on;
  if (Object.keys(o).length) S.edit[x.name] = o; else delete S.edit[x.name];
}

function setValue(x, op, v) {
  const o = S.edit[x.name] || {};
  const opts = o.options || {};
  if (same(v, op.default)) delete opts[op.key]; else opts[op.key] = v;
  if (Object.keys(opts).length) o.options = opts; else delete o.options;
  if (Object.keys(o).length) S.edit[x.name] = o; else delete S.edit[x.name];
}

const dirty = () => !same(S.edit, S.saved) || !same(S.vEdit, S.version);

function refreshSaveBar() {
  let n = 0;
  if (S.patches) {
    for (const x of S.patches.patches) {
      if (effEnabled(x) !== savedEnabled(x)) n++;
      for (const op of x.options) if (!same(effValue(x, op), savedValue(x, op))) n++;
    }
  }
  if (!same(S.vEdit, S.version)) n++;
  $('#savebar').hidden = !n;
  $('#sbtxt').textContent = `${n} unsaved change${n === 1 ? '' : 's'} · applies to next build`;
}

function tagOf(x) {
  const on = effEnabled(x);
  if (on === x.default_enabled) return [x.default_enabled ? 'default on' : 'default off', false];
  return [on ? 'included by you' : 'excluded by you', true];
}

function renderPatches() {
  const p = S.patches;
  $('#pfiles').textContent = p.files.join(', ');
  $('#plist').innerHTML = html`${p.patches.map((x, i) => {
    const [tag, chg] = tagOf(x);
    return html`<label class="prow row" data-i="${i}" ${x.options.length ? html`data-hasopt` : ''}><span class="switch"><input type="checkbox" ${effEnabled(x) ? html`checked` : ''} aria-label="${x.name}"><span></span></span><div class="grow" style="min-width:0"><div style="font-weight:500">${x.name}</div><div class="xs muted">${x.description || ''}</div></div>${x.options.length ? html`<a href="#opt-${i}" class="xs" data-opt="opt-${i}">${x.options.length} option${x.options.length === 1 ? '' : 's'}</a>` : ''}<span class="tag badge plain b-mute ${chg ? 'chg' : ''}">${tag}</span></label>`;
  })}`;
  const stale = $('#pstale');
  stale.hidden = !p.stale_overrides.length;
  if (p.stale_overrides.length) stale.querySelector('span').textContent = `Kept but not in this bundle: ${p.stale_overrides.join(', ')}`;
  updatePatchMeta();
}

function updatePatchMeta() {
  const p = S.patches;
  const on = p.patches.filter(effEnabled).length;
  $('#pinc').textContent = on;
  $('#ptotal').textContent = p.patches.length;
  $('#tcount').textContent = `${on}/${p.patches.length}`;
  $$('#plist .prow').forEach(r => {
    const x = p.patches[Number(r.dataset.i)];
    const [tag, chg] = tagOf(x);
    const t = r.querySelector('.tag');
    t.textContent = tag;
    t.classList.toggle('chg', chg);
    r.querySelector('input').checked = effEnabled(x);
  });
  const lines = [];
  for (const x of p.patches) {
    if (effEnabled(x) !== savedEnabled(x)) lines.push(`${effEnabled(x) ? '+ include' : '− exclude'} ${x.name}`);
    for (const op of x.options) {
      if (!same(effValue(x, op), savedValue(x, op))) lines.push(`~ ${x.name}.${op.key} = ${JSON.stringify(effValue(x, op))}`);
    }
  }
  $('#diff').textContent = lines.length ? lines.join('\n') : 'no unsaved changes';
  $('#raw').textContent = JSON.stringify([{ patches: S.edit }], null, 2);
  const changedOpts = p.patches.reduce((n, x) => n + x.options.filter(op => !same(effValue(x, op), op.default)).length, 0);
  $('#ocount').textContent = changedOpts ? `· ${changedOpts} changed` : '';
  $$('#opts fieldset').forEach(f => {
    const x = p.patches[Number(f.dataset.i)];
    f.querySelector('[data-unused]').hidden = effEnabled(x);
  });
  filterPatches();
  refreshSaveBar();
}

function filterPatches() {
  const q = $('#pf').value.trim().toLowerCase(), m = $('[name=pshow]:checked').value;
  $$('#plist .prow').forEach(r => {
    const on = r.querySelector('input').checked;
    r.hidden = !(r.textContent.toLowerCase().includes(q)
      && (m === 'all' || (m === 'in' && on) || (m === 'ex' && !on) || (m === 'opt' && r.hasAttribute('data-hasopt'))));
  });
}

function fieldFor(x, op, i, j) {
  const id = `o-${i}-${j}`, v = effValue(x, op), t = String(op.type || 'string');
  const head = html`<label class="lbl" for="${id}">${op.title || op.key} <span class="badge plain b-mute">${t}</span>${same(v, op.default) ? '' : html` <span class="badge plain b-warn">changed</span>`}</label>`;
  const hint = html`<p class="hint">Default: ${JSON.stringify(op.default)}</p>`;
  const data = html`data-i="${i}" data-j="${j}"`;
  if (op.values && op.values.length) {
    return html`<div class="field">${head}<select class="input" id="${id}" ${data} data-kind="select">${op.values.map(pv => html`<option value="${JSON.stringify(pv)}" ${same(pv, v) ? html`selected` : ''}>${String(pv)}</option>`)}</select>${hint}</div>`;
  }
  if (/bool/i.test(t)) {
    return html`<label class="row sm" style="align-self:center"><span class="switch"><input type="checkbox" id="${id}" ${data} data-kind="bool" ${v ? html`checked` : ''}><span></span></span>${op.title || op.key} <span class="badge plain b-mute">${t}</span></label>`;
  }
  if (/list|\[\]/i.test(t)) {
    return html`<div class="field">${head}<textarea class="input mono" id="${id}" rows="3" ${data} data-kind="list">${(v || []).join('\n')}</textarea>${hint}</div>`;
  }
  if (/int|long|float|double/i.test(t)) {
    return html`<div class="field">${head}<input class="input mono" type="number" ${/float|double/i.test(t) ? html`step="any"` : ''} id="${id}" ${data} data-kind="num" value="${v ?? ''}">${hint}</div>`;
  }
  const pickFrom = COLOR.test(String(v ?? '')) ? v : COLOR.test(String(op.default ?? '')) ? op.default : null;
  return html`<div class="field">${head}<div class="row" style="flex-wrap:nowrap">${pickFrom ? html`<input type="color" data-color-for="${id}" value="${'#' + String(pickFrom).slice(-6)}" aria-label="Pick colour" style="width:2.75rem;height:2.25rem;padding:2px">` : ''}<input class="input mono" id="${id}" ${data} data-kind="text" value="${v ?? ''}"></div>${hint}</div>`;
}

function renderOptions() {
  const p = S.patches;
  $('#opts').innerHTML = html`${p.patches.map((x, i) => (x.options.length ? html`<fieldset id="opt-${i}" data-i="${i}" class="stack" style="border-width:1px;border-radius:var(--radius);padding:1rem;margin:0"><legend class="sm" style="font-weight:600;padding:0 .375rem">${x.name} <span class="badge plain b-mute" data-unused hidden>kept but unused</span></legend><div class="grid2">${x.options.map((op, j) => fieldFor(x, op, i, j))}</div></fieldset>` : ''))}`;
}

function readField(el) {
  const k = el.dataset.kind;
  if (k === 'bool') return el.checked;
  if (k === 'select') return JSON.parse(el.value);
  if (k === 'num') return el.value === '' ? null : Number(el.value);
  if (k === 'list') return el.value.split('\n').map(s => s.trim()).filter(Boolean);
  return el.value;
}

// colour picker -> text (spec §0.6 R3): an 8-digit #AARRGGBB value keeps its alpha prefix
function colorInput(picker) {
  const text = document.getElementById(picker.dataset.colorFor);
  const old = text.value;
  text.value = /^#[0-9A-Fa-f]{8}$/.test(old) ? old.slice(0, 3) + picker.value.slice(1) : picker.value;
  text.dispatchEvent(new Event('input', { bubbles: true }));
}

function wirePatchEvents() {
  $('#plist').addEventListener('change', e => {
    const r = e.target.closest('.prow');
    if (!r) return;
    setEnabled(S.patches.patches[Number(r.dataset.i)], e.target.checked);
    updatePatchMeta();
  });
  $('#plist').addEventListener('click', e => {
    const a = e.target.closest('[data-opt]');
    if (!a) return;
    e.preventDefault();
    document.getElementById('t2').click();
    const f = document.getElementById(a.dataset.opt);
    f.scrollIntoView({ block: 'center' });
    f.classList.add('flash');
    setTimeout(() => f.classList.remove('flash'), 1500);
  });
  $('#pf').addEventListener('input', filterPatches);
  $$('[name=pshow]').forEach(r => r.addEventListener('change', filterPatches));
  $$('[data-all]').forEach(b => b.addEventListener('click', () => {
    if (!S.patches) return;
    S.patches.patches.forEach(x => setEnabled(x, b.dataset.all === '1'));
    updatePatchMeta();
  }));
  $('#reset').addEventListener('click', () => {
    if (!S.patches) return;
    S.edit = {};
    renderOptions();
    updatePatchMeta();
    toast('Reset to bundle defaults (save to apply)');
  });
  const onField = e => {
    if (e.target.matches('[data-color-for]')) return colorInput(e.target);
    const el = e.target.closest('[data-kind]');
    if (!el) return;
    const x = S.patches.patches[Number(el.dataset.i)], op = x.options[Number(el.dataset.j)];
    setValue(x, op, readField(el));
    const pick = document.querySelector(`[data-color-for="${el.id}"]`);
    if (pick && COLOR.test(el.value)) pick.value = '#' + el.value.slice(-6);
    updatePatchMeta();
  };
  $('#opts').addEventListener('input', onField);
  $('#opts').addEventListener('change', onField);
}

const chan = up => (up.channel === 'tag' ? (up.downloaded_tag || 'tag') : up.channel);

function renderHeader() {
  const it = S.item, up = it.upstream || {};
  document.title = `${it.label} · Relume`;
  $('h1').textContent = it.label;
  const newer = up.latest_tag && up.latest_tag !== up.downloaded_tag;
  const built = it.last_success ? `built ${it.last_success.app_version} with ${it.last_success.patches_version}` : 'not built yet';
  $('#hdr').innerHTML = html`${iconTile(it, 'ico-lg')}<div class="grow" style="min-width:220px"><div class="row"><h2 style="font-size:1.125rem">${it.label}</h2><span class="src">${it.source_name}</span>${newer ? html`<span class="badge b-warn">${up.latest_tag} available</span>` : ''}</div><p class="sm muted mono">${it.package} · ${up.repo} (${chan(up)}) · ${built}</p></div>`;
  $('#b-pull').disabled = !newer;
  $('#b-pull').title = it.engine === 'morphe' ? `Updates the ${it.source_name} source for all its items` : `Updates module ${up.repo} for all its apps`;
  $('#b-build').disabled = !!it.active_job;
  const mor = it.engine === 'morphe';
  $('#t5').hidden = mor;
  $('#t1').hidden = $('#t2').hidden = !mor;
  if (!mor && $('#t5').getAttribute('aria-selected') !== 'true') $('#t5').click();
}

async function renderInstalled() {
  const { devices } = await api('GET', '/api/devices');
  const rows = devices.flatMap(d => d.assignments.filter(a => a.item_id === ID).map(a => ({ d, a })));
  $('#inst').innerHTML = html`${rows.length
    ? rows.map(({ d, a }) => html`<span class="row sm" style="gap:.375rem"><span class="dot ${d.online ? 'on' : 'off'}" aria-hidden="true"></span>${d.name} <span class="mono ${a.pending ? 'newer' : ''}">${a.installed ? a.installed.version_name : 'not installed'}</span>${a.pending ? html`<span class="xs muted">(behind)</span>` : ''}</span>`)
    : html`<span class="sm muted">Not assigned to any device</span>`}`;
}

async function save(rebuild) {
  try {
    if (!same(S.edit, S.saved)) await api('PUT', `/api/items/${ID}/patches`, { overrides: S.edit });
    if (!same(S.vEdit, S.version)) await api('PUT', `/api/items/${ID}/version`, S.vEdit);
    if (rebuild) await api('POST', `/api/items/${ID}/build`);
    toast(rebuild ? 'Saved · build queued' : 'Saved');
    await load();
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function load() {
  S.item = await api('GET', `/api/items/${ID}`);
  S.version = { version_mode: S.item.version.mode, version_pin: S.item.version.pin, allow_nearest: S.item.version.allow_nearest };
  S.vEdit = clone(S.version);
  S.patches = null;
  if (S.item.engine === 'morphe') {
    try {
      S.patches = await api('GET', `/api/items/${ID}/patches`);
      if (!S.patches.tag || S.patches.unavailable) throw new Error('no patch list');
    } catch (_) {
      S.patches = null;
    }
  }
  renderHeader();
  if (S.patches) {
    S.saved = overridesFrom(S.patches);
    S.edit = clone(S.saved);
    $('#punavail').hidden = true;
    renderPatches();
    renderOptions();
  } else {
    S.saved = {};
    S.edit = {};
    $('#plist').textContent = '';
    $('#opts').textContent = '';
    $('#punavail').hidden = S.item.engine !== 'morphe';
  }
  renderVersion();
  renderHistory().catch(() => {});
  renderModule().catch(() => {});
  renderInstalled().catch(() => {});
  refreshSaveBar();
  if (location.hash === '#version') document.getElementById('t3').click();
}

$('#discard').addEventListener('click', () => load().catch(() => {}));
$('#save').addEventListener('click', () => save(false));
$('#saverun').addEventListener('click', () => save(true));
$('#b-build').addEventListener('click', () => act('POST', `/api/items/${ID}/build`, {}, 'Build queued').then(load).catch(() => {}));
$('#b-pull').addEventListener('click', () => act('POST', `/api/upstreams/${S.item.upstream.id}/pull`, { rebuild: true }, 'Update queued').catch(() => {}));
$('#b-dl').addEventListener('toggle', async e => {
  if (!e.target.open) return;
  const menu = e.target.querySelector('.menu');
  await fillDownloads(menu, ID);
});
$('#b-menu .menu').addEventListener('click', () => { $('#b-menu').open = false; });
window.addEventListener('beforeunload', e => { if (dirty()) e.preventDefault(); });

// ---- Version, History, Module tabs and dialogs ----
// Exact port of catalog.version_key (catalog.py): one leading v/V dropped, digit parts are ints else -1, trailing 0.
const verKey = v => [...String(v).replace(/^[vV]/, '').split(/[.\-+_]/).map(p => (/^[0-9]+$/.test(p) ? Number(p) : -1)), 0];
const newer = (a, b) => {
  const x = verKey(a), y = verKey(b);
  for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i] > y[i];
  return x.length > y.length;
};
let builtOk = new Set();

function renderVersion() {
  const it = S.item, compat = (S.patches && S.patches.compatible_versions) || [];
  const xposed = it.engine !== 'morphe';
  $('[name=pin][value=auto]').closest('.rcard').hidden = xposed;
  $('#xp-note').hidden = !xposed;
  $('#vauto-sub').textContent = compat.length ? `Resolves to ${compat[0]} today.`
    : 'No compatible version — the build will fail; pin a version or choose Latest.';
  $(`[name=pin][value=${S.vEdit.version_mode === 'pinned' ? 'pin' : S.vEdit.version_mode}]`).checked = true;
  $('#pv').value = S.vEdit.version_pin || '';
  $('#pv').disabled = S.vEdit.version_mode !== 'pinned';
  $('#latestwarn').hidden = S.vEdit.version_mode !== 'latest';
  $('#near').checked = !!S.vEdit.allow_nearest;
  const mirror = ((it.apkmirror && it.apkmirror.versions) || []).filter(v => !compat.length || newer(v, compat[0]));
  $('#pvl').innerHTML = html`${compat.map((v, i) => html`<option value="${v}">${i ? 'compatible' : 'patches recommend'}${builtOk.has(v) ? ' · last built OK' : ''}</option>`)}${mirror.map(v => html`<option value="${v}">not in patch list ⚠</option>`)}`;
  $('#pvhint').textContent = it.apkmirror && it.apkmirror.checked_at ? `APKMirror checked ${fmt.rel(it.apkmirror.checked_at)}` : 'APKMirror versions: filled by the next build';
  checkPin();
}

function checkPin() {
  const compat = (S.patches && S.patches.compatible_versions) || [];
  const v = $('#pv').value.trim();
  $('#pvwarn').hidden = !(S.vEdit.version_mode === 'pinned' && v && compat.length && !compat.includes(v));
}

document.addEventListener('change', e => {
  if (e.target.name === 'pin') {
    const m = e.target.value === 'pin' ? 'pinned' : e.target.value;
    S.vEdit.version_mode = m;
    S.vEdit.version_pin = m === 'pinned' ? ($('#pv').value.trim() || null) : null;
    $('#pv').disabled = m !== 'pinned';
    $('#latestwarn').hidden = m !== 'latest';
    checkPin();
    refreshSaveBar();
  }
  if (e.target.id === 'near') { S.vEdit.allow_nearest = e.target.checked; refreshSaveBar(); }
});
$('#pv').addEventListener('input', () => { S.vEdit.version_pin = $('#pv').value.trim() || null; checkPin(); refreshSaveBar(); });

async function renderHistory() {
  const [{ jobs }, settings] = await Promise.all([
    api('GET', `/api/jobs?kind=build&item_id=${ID}&limit=20`), api('GET', '/api/settings')]);
  const ok = new Set(jobs.filter(j => j.status === 'success' && j.detail).map(j => j.detail.app_version));
  const changed = ok.size !== builtOk.size || [...ok].some(v => !builtOk.has(v));
  builtOk = ok;
  $('#keep').textContent = `Keeping the last ${settings.artifact_retention} successful builds (Settings → Artifacts).`;
  $('#allb').href = `/builds.html?item=${ID}`;
  const badge = j => html`<span class="badge ${JOB_BADGE[j.status][0]}">${j.status}</span>`;
  $('#hist tbody').innerHTML = html`${jobs.map(j => {
    const d = j.detail || {}, arts = d.artifacts || [];
    return html`<tr ${j.status === 'failed' ? html`data-href="/builds.html?job=${j.id}" style="cursor:pointer"` : ''}><td class="mono"><a href="/builds.html?job=${j.id}">#${j.id}</a></td><td>${fmt.abs(j.started_at || j.created_at)}</td><td class="hide-m mono">${d.app_version || '—'}</td><td class="hide-m mono">${d.patches_version || '—'}</td><td>${badge(j)}${j.status === 'failed' ? html`<div class="xs danger">${j.reason || ''}</div>` : ''}</td><td class="hide-m">${fmt.duration(j.started_at, j.finished_at)}</td><td>${arts.length ? html`<a class="btn btn-ghost btn-sm" href="/api/artifacts/${arts[0].id}/download" download>${arts.length > 1 ? `Download (${arts.length})` : 'Download'}</a>` : ''}</td></tr>`;
  })}`;
  if (changed) renderVersion();
}
$('#hist').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-href]');
  if (tr && !e.target.closest('a')) location.href = tr.dataset.href;
});

async function renderModule() {
  const it = S.item;
  if (it.engine === 'morphe') return;
  const [{ sources }, { upstreams }] = await Promise.all([api('GET', '/api/sources'), api('GET', '/api/upstreams')]);
  const src = sources.find(s => s.id === it.source_id);
  const eng = upstreams.find(u => u.id === (src && src.cli_upstream_id)) || {};
  const up = it.upstream || {};
  $('#mod').innerHTML = html`<div><dt class="muted">Module</dt><dd class="mono">${up.repo}</dd></div><div><dt class="muted">Channel</dt><dd class="mono">${chan(up)}</dd></div><div><dt class="muted">Downloaded</dt><dd class="mono">${up.downloaded_tag || '—'}</dd></div><div><dt class="muted">Latest</dt><dd class="mono">${up.latest_tag || '—'}</dd></div><div><dt class="muted">Engine</dt><dd class="mono">${it.engine === 'lspatch' ? 'LSPatch' : 'NPatch'} ${eng.downloaded_tag || ''}</dd></div>`;
  const max = it.engine === 'npatch' ? 4 : 3, eo = it.engine_options || { sigbypass: 0, injectdex: false };
  $('#m-sig').innerHTML = html`${Array.from({ length: max + 1 }, (_, n) => html`<option value="${n}" ${n === eo.sigbypass ? html`selected` : ''}>${n}</option>`)}`;
  $('#m-dex').checked = !!eo.injectdex;
}
$('#m-save').addEventListener('click', () => act('PUT', `/api/items/${ID}`,
  { engine_options: { sigbypass: Number($('#m-sig').value), injectdex: $('#m-dex').checked } }, 'Module settings saved').then(load).catch(() => {}));

$('#m-icon').addEventListener('click', () => act('POST', `/api/apps/${encodeURIComponent(S.item.package)}/icon/refresh`, {}, 'Icon refreshed').catch(() => {}));
$('#m-app').addEventListener('click', async () => {
  const dlg = $('#dlg-app');
  $('#e-name').value = S.item.display_name || '';
  if (await ask(dlg) !== 'ok') return;
  const body = { display_name: $('#e-name').value.trim() || null };
  for (const [id, key] of [['#e-list', 'apkmirror_list_url'], ['#e-ex', 'apkmirror_example_url'], ['#e-ex2', 'apkmirror_example_url2'], ['#e-pure', 'apkpure_url']]) {
    if ($(id).value.trim()) body[key] = $(id).value.trim();
  }
  act('PUT', `/api/apps/${encodeURIComponent(S.item.package)}`, body, 'App saved').then(load).catch(() => {});
});

const ABIS = ['universal', 'arm64-v8a', 'armeabi-v7a', 'x86', 'x86_64'];
$('#m-item').addEventListener('click', async () => {
  const it = S.item;
  let groups, projects;
  try {
    [{ groups }, { projects }] = await Promise.all([api('GET', '/api/groups'), api('GET', '/api/projects')]);
  } catch (e) {
    return toast(e.message, 'error');
  }
  let variants = clone(it.variants);
  $('#i-en').checked = it.enabled;
  $('#i-name').value = it.display_name || '';
  $('#i-grp').innerHTML = html`${groups.map(g => html`<option value="${g.id}" ${g.id === it.group_id ? html`selected` : ''}>${g.name}</option>`)}<option value="" ${it.group_id == null ? html`selected` : ''}>Ungrouped</option>`;
  const chips = () => {
    $('#i-abis').innerHTML = html`${ABIS.map(a => html`<label class="chip"><input type="checkbox" data-abi="${a}" ${variants.some(v => v.abi === a) ? html`checked` : ''}><span>${a}</span></label>`)}`;
  };
  chips();
  $('#i-abis').onchange = e => {
    const b = e.target.closest('[data-abi]');
    if (!b) return;
    const a = b.dataset.abi, has = variants.some(v => v.abi === a);
    if (has && variants.length > 1) variants = variants.filter(v => v.abi !== a);
    if (!has) variants.push({ ...clone(variants[0]), name: a, abi: a, strip_libs: a === 'universal' || it.engine !== 'morphe' ? null : a });
    chips();
  };
  $('#i-deps').innerHTML = html`${projects.map(p => html`<label class="row sm"><input type="checkbox" class="cb" value="${p.id}" ${it.project_ids.includes(p.id) ? html`checked` : ''}> ${p.label} <span class="xs muted mono">${p.package || p.repo}</span></label>`)}`;
  if (await ask($('#dlg-item')) !== 'ok') return;
  const body = {
    enabled: $('#i-en').checked,
    display_name: $('#i-name').value.trim() || null,
    group_id: $('#i-grp').value ? Number($('#i-grp').value) : null,
    variants,
    project_ids: $$('#i-deps input:checked').map(i => Number(i.value)),
  };
  act('PUT', `/api/items/${ID}`, body, 'Item saved').then(load).catch(() => {});
});
$('#i-del').addEventListener('click', async () => {
  if (!confirm(`Delete ${S.item.label}? Its build files are deleted too.`)) return;
  try { await act('DELETE', `/api/items/${ID}`, {}, 'Item deleted'); location.href = '/'; } catch (_) { /* shown */ }
});

shell();
wirePatchEvents();
load().catch(e => toast(e.message, 'error'));
