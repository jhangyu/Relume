import { $, $$, act, afterWrite, api, ask, fmt, html, icon, iconTile, live, report, shell, toast, toastSettled } from '/static/app.js';

// App manage matrix (plan §1.3 JSON, §1.5 SSE). Server order of apps and devices is kept as is.
const S = { data: null, filter: 'all', query: '', cur: 1, collapsed: { off_primary: false, on_primary: false } };

const SRC = {
  build: ['server', 'Relume build', 'Installs the Relume build automatically'],
  play_direct: ['cloud-dl', 'Google Play', 'Downloads from Google Play and installs automatically'],
  play_page: ['open', 'Play Store page', 'Opens the Play Store page on the phone; you tap Install there'],
};
const OP = { install: 'Install', upgrade: 'Upgrade', remove: 'Remove' };
const STATE = {
  queued: ['', 'Queued', 'history'],
  delivered: ['warn', 'Delivered', 'clock'],
  running: ['brand', 'Running', 'sync'],
  failed: ['err', 'Failed', 'error'],
};
const SYNC_BADGE = ['warn', 'Awaiting sync', 'clock'];   // a queued action on a companion phone waits for its next sync
const OPEN_STATES = ['queued', 'delivered', 'running'];
const CANCELLABLE = ['queued', 'delivered', 'failed'];   // DELETE: cancel (queued, delivered) or dismiss (failed)
const PHONE_WIDTH = '(max-width: 640px)';

const devById = id => S.data.devices.find(d => d.id === Number(id));
const appByPkg = p => S.data.apps.find(a => a.package === p);
const primaryOf = () => S.data.devices.find(d => d.id === S.data.primary_id);
const inst = (a, d) => a.cells[d.id].installed;
const sizeKnown = i => i.app_bytes != null && i.data_bytes != null && i.cache_bytes != null;
const letters = a => (a.label || a.package).split(/[\s·.]+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();
const shortVersion = v => v.split('.').slice(0, 2).join('.');
const isOpen = c => c.action && OPEN_STATES.includes(c.action.state);

const differs = a => {
  const p = primaryOf();
  if (!p) return false;
  const pi = inst(a, p);
  return S.data.devices.some(d => {
    if (d === p) return false;
    const i = inst(a, d);
    return Boolean(i) !== Boolean(pi) || (i && pi && i.version_code !== pi.version_code);
  });
};
const pending = a => Object.values(a.cells).some(c => c.action);

// ---------- render ----------
function devSub(d, isPri) {
  const kind = d.kind === 'adb' ? 'ADB' : 'Companion';
  const seen = d.reported_at ? `reported ${fmt.rel(d.reported_at)}` : d.kind === 'app' ? 'waiting for phone sync' : '';
  const pri = isPri ? html`<span class="pri">Primary</span> · ` : '';
  if (d.online === false) return html`${pri}${icon('wifi-off')}Offline${seen ? ` · ${seen}` : ''}`;
  return html`${pri}${d.online ? html`<span class="dot on" aria-hidden="true"></span>` : ''}${kind}${seen ? ` · ${seen}` : ''}`;
}

function devHead(d, isPri, si) {
  const tip = d.kind === 'app' ? 'Companion app: polls the server about every 15 min' : 'ADB: actions run immediately while online';
  return html`<th scope="col" class="${isPri ? 'pc' : 'sc'}" ${si ? html`data-si="${si}"` : ''} data-col="${d.id}"><div class="dh ${d.online === false ? 'offline' : ''}" data-tip="${d.name} — ${tip}" tabindex="0">${icon('devices')}<span class="nm">${d.name}</span><span class="sub">${devSub(d, isPri)}</span></div>${handle(d.id, d.name)}</th>`;
}

function pendingCell(a, d, c) {
  const x = c.action;
  const [bc, bl, bi] = x.state === 'queued' && d.kind === 'app' ? SYNC_BADGE : STATE[x.state];
  const tip = x.state === 'failed' ? x.error_text
    : x.state === 'queued' && d.kind === 'app' ? `Runs when ${d.name} next syncs (about every 15 min)`
    : x.state === 'queued' && d.online === false ? `${d.name} is offline; runs when it reconnects` : '';
  const i = c.installed;
  const op = x.state === 'failed' ? `${OP[x.action]} · ${x.error_text}` : `${OP[x.action]}${i ? ` · ${i.version_name}` : ''}`;
  const dismiss = x.state === 'failed';
  const key = html`data-a="${a.package}" data-d="${d.id}"`;
  const alt = dismiss && x.source === 'play_direct' ? html`<button class="cb" type="button" data-act="play-page" data-op="${x.action}" ${key} aria-label="Open Play Store page instead for ${a.label} on ${d.name}" data-tip="Open the Play Store page on the phone; you tap Install there">${icon('open')}Open Play Store page instead</button>` : '';
  return html`<div class="cell ${d.online === false ? 'stale' : ''}"><div class="cv"><span class="badge ${bc} ${x.state === 'running' ? 'spin' : ''}" ${tip ? html`data-tip="${tip}"` : ''} tabindex="0">${icon(bi)}${bl}</span><span class="op ${dismiss ? 'err' : ''}">${op}</span></div>${alt}${CANCELLABLE.includes(x.state) ? html`<div class="ca on"><button class="xb" type="button" data-act="cancel" data-id="${x.id}" aria-label="${dismiss ? 'Dismiss' : 'Cancel'} ${OP[x.action].toLowerCase()} of ${a.label} on ${d.name}" data-tip="${dismiss ? 'Dismiss' : 'Cancel'}">${icon('dismiss-circle')}</button></div>` : ''}</div>`;
}

function installedCell(a, d, c, batch) {
  const i = c.installed;
  const sz = sizeKnown(i)
    ? html`<span class="sz" data-tip="App ${fmt.bytes(i.app_bytes)} · Data ${fmt.bytes(i.data_bytes)} · Cache ${fmt.bytes(i.cache_bytes)}" tabindex="0">${fmt.bytes(i.app_bytes + i.data_bytes + i.cache_bytes)}</span>`
    : html`<span class="sz unk" data-tip="Size unknown: ${i.size_reason}" tabindex="0">— size</span>`;
  const key = html`data-a="${a.package}" data-d="${d.id}"`;
  const up = i.outdated ? html`<button class="cb up" type="button" data-act="upgrade" ${key} aria-label="Upgrade ${a.label} on ${d.name} to ${a.highest.version_name}" data-tip="Upgrade to ${a.highest.version_name}">${icon('upgrade')}${shortVersion(a.highest.version_name)}</button>` : '';
  return html`<div class="cell ${i.outdated ? 'old' : ''} ${d.online === false ? 'stale' : ''}"><div class="cv"><span class="v" title="${i.version_name}">${i.version_name}</span>${sz}</div>${up}<div class="ca">${i.outdated ? html`<button class="xb" type="button" data-act="pick-source" ${key} aria-label="Choose source to install ${a.label} on ${d.name}" data-tip="Choose source…">${icon('chevron-down')}</button>` : ''}<button class="xb rm" type="button" data-act="remove" ${key} aria-label="Remove ${a.label} from ${d.name}" data-tip="${d.kind === 'app' ? 'Remove (phone asks to confirm)' : 'Remove'}">${icon('delete')}</button>${batch}</div></div>`;
}

function emptyCell(a, d, batch) {
  const [si, sl, st] = SRC[a.source];
  const key = html`data-a="${a.package}" data-d="${d.id}"`;
  const label = d.kind === 'app' && !d.reported_at ? 'Waiting for phone sync' : 'Not installed';
  return html`<div class="cell none ${d.online === false ? 'stale' : ''}"><div class="cv"><span class="v">${label}</span></div><div class="ca"><button class="cb" type="button" data-act="install" ${key} aria-label="Install ${a.label} on ${d.name} (${sl})" data-tip="${st}">${icon(si)}Install</button>${html`<button class="xb" type="button" data-act="pick-source" ${key} aria-label="Choose source to install ${a.label} on ${d.name}" data-tip="Choose source…">${icon('chevron-down')}</button>`}${batch}</div></div>`;
}

function cell(a, d, isPri, si) {
  const c = a.cells[d.id];
  const key = html`data-a="${a.package}" data-d="${d.id}"`;
  const batch = isPri ? html`<button class="xb batch" type="button" data-act="batch-install" ${key} aria-label="Install ${a.label} on other devices" data-tip="Install on devices…">${icon('batch-install')}</button><button class="xb rm" type="button" data-act="batch-remove" ${key} aria-label="Remove ${a.label} from devices" data-tip="Remove from devices…">${icon('batch-remove')}</button>` : '';
  const inner = c.action ? pendingCell(a, d, c) : c.installed ? installedCell(a, d, c, batch) : emptyCell(a, d, batch);
  return html`<td class="${isPri ? 'pc' : 'sc'}" ${si ? html`data-si="${si}"` : ''} data-col="${d.id}">${inner}</td>`;
}

function row(a, devs, pri, sec) {
  const [si, sl] = SRC[a.source];
  const tile = a.icon ? iconTile({ label: a.label, icon_url: `/api/appmanage/icons/${a.icon}` }) : html`<span class="tile" aria-hidden="true">${letters(a)}</span>`;
  return html`<tr ${differs(a) ? html`data-differs` : ''}><th scope="row" class="ac"><div class="app">${tile}<div style="min-width:0"><div class="lab">${a.label}</div><div class="pkg">${a.package}</div></div><span class="src" data-tip="Source: ${sl}" tabindex="0" aria-label="Source: ${sl}">${icon(si)}</span></div></th>${devs.map(d => cell(a, d, d === pri, d === pri ? 0 : sec.indexOf(d) + 1))}</tr>`;
}

function render() {
  hideTip();   // its anchor element is about to be replaced
  const { devices, apps, primary_id } = S.data;
  const pri = primaryOf();
  const sec = devices.filter(d => d !== pri);
  const q = S.query;
  const visible = apps.filter(a => (!q || `${a.label} ${a.package}`.toLowerCase().includes(q))
    && (S.filter === 'all' || (S.filter === 'diff' ? differs(a) : pending(a))));
  const n = devices.length + 1;
  const pn = pri ? pri.name : '';
  const grp = (k, label, hint) => {
    const list = visible.filter(a => a.group === k);
    return html`<tr class="grp"><th colspan="${n}" scope="rowgroup"><button class="grp-in" type="button" data-grp="${k}" aria-expanded="${!S.collapsed[k]}">${icon('chevron-down')}${label}<span class="n">${list.length} apps</span><span class="hint-g">· ${hint}</span></button></th></tr>${S.collapsed[k] ? '' : list.map(a => row(a, devices, pri, sec))}`;
  };
  const groups = !apps.length ? html`<tr class="empty"><td colspan="${n}">No apps reported yet. Refresh asks ADB devices now; companion phones report on their next sync.</td></tr>`
    : !visible.length ? html`<tr class="empty"><td colspan="${n}">No apps match this filter.</td></tr>`
    : pri ? html`${grp('off_primary', `Not on ${pn}`, 'largest on other devices first')}${grp('on_primary', `On ${pn}`, `largest on ${pn} first`)}`
    : grp('off_primary', 'All apps', 'largest first');
  $('#mx').innerHTML = html`<thead><tr><th scope="col" class="ac"><div class="th-app"><span>App</span><span class="caption">${visible.length} of ${apps.length}</span></div>${handle('app', 'App')}</th>${devices.map(d => devHead(d, d === pri, d === pri ? 0 : sec.indexOf(d) + 1))}</tr></thead><tbody>${groups}</tbody>`;
  S.cur = Math.min(Math.max(S.cur, 1), Math.max(sec.length, 1));
  $('#mx').dataset.cur = S.cur;
  const other = sec[S.cur - 1];
  $('#pg-nm').textContent = [pri && pri.name, other && other.name].filter(Boolean).join('  vs  ');
  $('#pg-dots').innerHTML = html`${sec.map((_, i) => html`<span ${i + 1 === S.cur ? html`aria-current="true"` : ''}></span>`)}`;
  $('#pri').innerHTML = html`<option value="">No primary</option>${devices.map(d => html`<option value="${d.id}">${d.name}${d.online === false ? ' (offline)' : ''}</option>`)}`;
  $('#pri').value = primary_id == null ? '' : String(primary_id);
}

async function load() {
  S.data = await api('GET', '/api/appmanage');
  loadWidths(['app', ...S.data.devices.map(d => d.id)]);
  render();
}

// ---------- actions ----------
const doneText = (a, d, op, state) => `${OP[op]} ${a.label} on ${d.name}: ${d.kind === 'app' ? (op === 'remove' ? 'the phone will ask to confirm after its next sync' : 'waiting for phone sync') : state === 'running' ? 'running' : 'queued'}`;

$('#mx').addEventListener('click', e => {
  const g = e.target.closest('[data-grp]');
  if (g) { S.collapsed[g.dataset.grp] = !S.collapsed[g.dataset.grp]; render(); return; }
  const b = e.target.closest('[data-act]');
  if (!b) return;
  const kind = b.dataset.act;
  if (kind === 'cancel') {
    act('DELETE', `/api/appmanage/actions/${Number(b.dataset.id)}`, {}).then(r => { toast(r.deleted ? 'Dismissed' : 'Cancelled'); afterWrite(); }).catch(report);
    return;
  }
  const a = appByPkg(b.dataset.a), d = devById(b.dataset.d);
  if (kind === 'batch-install' || kind === 'batch-remove') { openBatch(a, kind === 'batch-install' ? 'install' : 'remove').catch(report); return; }
  if (kind === 'pick-source') { openBatch(a, 'install', d).catch(report); return; }
  if (kind === 'play-page') {
    act('POST', '/api/appmanage/actions', { package: a.package, action: b.dataset.op, device_ids: [d.id], source: 'play_page' })
      .then(r => { toast(doneText(a, d, b.dataset.op, r.actions[0].state)); afterWrite(); }).catch(report);
    return;
  }
  const body = { package: a.package, action: kind, device_ids: [d.id] };
  if (kind !== 'remove') body.source = a.source;
  act('POST', '/api/appmanage/actions', body)
    .then(r => { toast(doneText(a, d, kind, r.actions[0].state)); afterWrite(); }).catch(report);
});

// batch dialog
const dlg = $('#dlg-batch');
let batchMode = null;
function syncBatchBtn() {
  const n = $$('#bt-list input:checked').length, rm = batchMode === 'remove';
  const ok = $('#bt-ok');
  ok.className = `btn ${rm ? 'destructive' : 'primary'}`;
  ok.textContent = `${rm ? 'Remove from' : 'Install on'} ${n} device${n === 1 ? '' : 's'}`;
  ok.disabled = !n;
}
$('#bt-list').addEventListener('change', syncBatchBtn);

// only: a device the dialog is limited to (single-cell source choice); null lists every device
async function openBatch(a, mode, only = null) {
  batchMode = mode;
  const pri = primaryOf();
  $('#bt-t').textContent = mode === 'install' ? `Install ${a.label} on…` : `Remove ${a.label} from…`;
  $('#bt-m').textContent = mode === 'install'
    ? `Installs ${a.highest ? a.highest.version_name : 'the newest version'} on the selected devices. Devices already on it are skipped.`
    : 'Uninstalls the app and its data. Companion phones show a system confirm dialog after their next sync.';
  const srcs = Object.keys(SRC).filter(k => k !== 'build' || a.ref);
  $('#bt-src').innerHTML = html`${mode === 'install' ? html`<div class="seg" role="radiogroup" aria-label="Source">${srcs.map(k => html`<label data-tip="${SRC[k][2]}"><input type="radio" name="bt-s" value="${k}" ${k === a.source ? html`checked` : ''}><span>${SRC[k][1]}</span></label>`)}</div>` : ''}`;
  $('#bt-list').innerHTML = html`${S.data.devices.map(d => {
    const c = a.cells[d.id], i = c.installed, busy = isOpen(c);
    const can = !busy && (!only || d === only) && (mode === 'install' ? !i || i.outdated : Boolean(i));
    const state = busy ? `${STATE[c.action.state][1]} · ${OP[c.action.action]}` : i ? `Installed ${i.version_name}${sizeKnown(i) ? ` · ${fmt.bytes(i.app_bytes + i.data_bytes + i.cache_bytes)}` : ''}` : 'Not installed';
    const how = d.online === false ? 'offline — queued until it reconnects' : d.kind === 'app' ? 'companion — waits for next sync (~15 min)' : 'ADB — runs now';
    return html`<li><label><input type="checkbox" value="${d.id}" ${can ? '' : html`disabled`} ${can && (mode === 'install' || d !== pri) ? html`checked` : ''}>${icon('devices')}<span style="min-width:0"><span class="strong">${d.name}</span>${d === pri ? html` <span class="badge brand" style="height:18px">Primary</span>` : ''}<div class="d2">${how}</div></span><span class="d2">${state}</span></label></li>`;
  })}`;
  syncBatchBtn();
  if (await ask(dlg) !== 'ok') return;
  const ids = $$('#bt-list input:checked').map(x => Number(x.value));
  // one create call per action name: install and upgrade are different actions of the same batch
  const source = mode === 'install' ? $('input[name=bt-s]:checked', dlg).value : null;
  const byAction = new Map();
  ids.forEach(id => {
    const name = mode === 'remove' ? 'remove' : a.cells[id].installed ? 'upgrade' : 'install';
    byAction.set(name, [...(byAction.get(name) || []), id]);
  });
  const results = await Promise.allSettled([...byAction].map(([action, device_ids]) => api('POST', '/api/appmanage/actions', source ? { package: a.package, action, device_ids, source } : { package: a.package, action, device_ids })));
  toastSettled(results, `${mode === 'install' ? 'Install' : 'Remove'} ${a.label}: ${ids.length} device${ids.length === 1 ? '' : 's'} queued`);
  afterWrite();
}

// toolbar
$('#pri').addEventListener('change', e => {
  const id = e.target.value === '' ? null : Number(e.target.value);
  S.cur = 1;
  act('PUT', '/api/appmanage/primary', { device_id: id }, id == null ? 'Primary device cleared' : `Primary device: ${devById(id).name}`)
    .then(afterWrite).catch(err => { render(); report(err); });
});
$('#q').addEventListener('input', e => { S.query = e.target.value.trim().toLowerCase(); render(); });
$$('input[name=f]').forEach(r => r.addEventListener('change', () => { S.filter = r.value; render(); }));
$('#refresh').addEventListener('click', () => act('POST', '/api/appmanage/refresh', {}, 'Refresh requested').then(afterWrite).catch(report));

// phone pager + swipe on the matrix
const step = k => {
  const n = S.data.devices.filter(d => d.id !== S.data.primary_id).length;
  if (!n) return;
  S.cur = (S.cur - 1 + k + n) % n + 1;
  render();
};
$('#prev').addEventListener('click', () => step(-1));
$('#next').addEventListener('click', () => step(1));
let x0 = null;
$('#wrap').addEventListener('touchstart', e => { x0 = e.touches[0].clientX; }, { passive: true });
$('#wrap').addEventListener('touchend', e => {
  if (x0 === null || !matchMedia(PHONE_WIDTH).matches) return;
  const dx = e.changedTouches[0].clientX - x0;
  x0 = null;
  if (Math.abs(dx) > 48) step(dx < 0 ? 1 : -1);
});

// ---------- column widths: per column (app + each device id), remembered in localStorage ----------
const COLW_KEY = 'relume.appmanage.colw.';
const MIN_APP_W = 140, MIN_DEV_W = 96, MAX_COL_W = 640, KEY_STEP = 16, KEY_STEP_BIG = 48;
const minW = id => id === 'app' ? MIN_APP_W : MIN_DEV_W;
const W = {};
// Storage can be missing or throw (private window, blocked site data); the page then keeps widths for this visit only.
const colStore = {
  get(id) { try { return localStorage.getItem(COLW_KEY + id); } catch (e) { console.warn(`column width for "${id}" not read: ${e.message}`); return null; } },
  set(id, w) { try { if (w == null) localStorage.removeItem(COLW_KEY + id); else localStorage.setItem(COLW_KEY + id, String(w)); } catch (e) { console.warn(`column width for "${id}" not saved: ${e.message}`); } },
};
const colStyle = document.createElement('style');
document.head.append(colStyle);
// The app width drives --aw, which is also the sticky left offset of the primary column, so offsets follow every resize.
function applyWidths() {
  colStyle.textContent = `@media (min-width: 641px) {${Object.entries(W).map(([id, w]) => id === 'app'
    ? `.am .mx-wrap { --aw: ${w}px; }`
    : `.am .mx [data-col="${id}"] { width: ${w}px; min-width: ${w}px; max-width: ${w}px; }`).join('\n')}}`;
}
function loadWidths(ids) {
  ids.filter(id => !(id in W)).forEach(id => {
    const v = Number(colStore.get(id));
    if (v >= minW(id) && v <= MAX_COL_W) W[id] = v;
  });
  applyWidths();
}
function handle(id, name) {
  return html`<span class="rz" role="separator" aria-orientation="vertical" tabindex="0" data-rz="${id}" aria-label="Resize ${name} column" aria-valuemin="${minW(id)}" aria-valuemax="${MAX_COL_W}" ${W[id] ? html`aria-valuenow="${W[id]}"` : ''} data-tip="Drag, or focus and press ← →, to resize. Double-click or Home resets."></span>`;
}
function setW(id, w) {
  W[id] = Math.round(Math.min(MAX_COL_W, Math.max(minW(id), w)));
  applyWidths();
  $$(`[data-rz="${id}"]`).forEach(h => h.setAttribute('aria-valuenow', W[id]));
}
function resetW(id) {
  delete W[id];
  colStore.set(id, null);
  applyWidths();
  $$(`[data-rz="${id}"]`).forEach(h => h.removeAttribute('aria-valuenow'));
}
const thWidth = h => h.parentElement.getBoundingClientRect().width;
let drag = null;
$('#mx').addEventListener('pointerdown', e => {
  const h = e.target.closest('[data-rz]');
  if (!h || e.button !== 0) return;
  e.preventDefault();
  hideTip();
  drag = { h, id: h.dataset.rz, x: e.clientX, w: thWidth(h) };
  h.setPointerCapture(e.pointerId);
  h.classList.add('drag');
  document.body.classList.add('resizing');
});
$('#mx').addEventListener('pointermove', e => { if (drag) setW(drag.id, drag.w + e.clientX - drag.x); });
function endDrag() {
  if (!drag) return;
  drag.h.classList.remove('drag');
  document.body.classList.remove('resizing');
  if (W[drag.id]) colStore.set(drag.id, W[drag.id]);
  drag = null;
}
$('#mx').addEventListener('pointerup', endDrag);
$('#mx').addEventListener('pointercancel', endDrag);
$('#mx').addEventListener('dblclick', e => { const h = e.target.closest('[data-rz]'); if (h) resetW(h.dataset.rz); });
$('#mx').addEventListener('keydown', e => {
  const h = e.target.closest('[data-rz]');
  if (!h) return;
  const id = h.dataset.rz, stepPx = e.shiftKey ? KEY_STEP_BIG : KEY_STEP;
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    e.preventDefault();
    setW(id, (W[id] ?? thWidth(h)) + (e.key === 'ArrowRight' ? stepPx : -stepPx));
    colStore.set(id, W[id]);
  } else if (e.key === 'Home') {
    e.preventDefault();
    resetW(id);
  }
});

// ---------- tooltip: one position:fixed popup on <body>, flipped/clamped to stay inside the viewport ----------
const TIP_GAP = 6, TIP_EDGE = 8;
const tipEl = document.createElement('div');
tipEl.className = 'ftip';
tipEl.id = 'tip';
tipEl.setAttribute('role', 'tooltip');
document.body.append(tipEl);
let tipFor = null;
function showTip(el) {
  if (tipFor && tipFor !== el) tipFor.removeAttribute('aria-describedby');
  tipFor = el;
  el.setAttribute('aria-describedby', 'tip');
  tipEl.textContent = el.dataset.tip;
  tipEl.style.left = '0px'; tipEl.style.top = '0px';   // measure unconstrained by the right edge
  const r = el.getBoundingClientRect(), t = tipEl.getBoundingClientRect();
  let top = r.bottom + TIP_GAP;
  if (top + t.height > innerHeight - TIP_EDGE) top = r.top - TIP_GAP - t.height;   // no room below: open above
  top = Math.max(TIP_EDGE, top);
  const left = Math.min(Math.max(TIP_EDGE, r.left + r.width / 2 - t.width / 2), innerWidth - TIP_EDGE - t.width);
  tipEl.style.left = `${left}px`; tipEl.style.top = `${top}px`;
  tipEl.classList.add('show');
}
function hideTip() {
  if (!tipFor) return;
  tipFor.removeAttribute('aria-describedby');
  tipFor = null;
  tipEl.classList.remove('show');
}
document.addEventListener('pointerover', e => {
  if (drag) return;
  const el = e.target.closest('[data-tip]');
  if (!el) hideTip(); else if (el !== tipFor) showTip(el);
});
document.addEventListener('focusin', e => { const el = e.target.closest('[data-tip]'); if (el) showTip(el); else hideTip(); });
document.addEventListener('focusout', hideTip);
document.addEventListener('keydown', e => { if (e.key === 'Escape') hideTip(); });
$('#wrap').addEventListener('scroll', hideTip, { passive: true });
addEventListener('scroll', hideTip, { passive: true });
addEventListener('resize', hideTip);

(async () => {
  shell();
  // A refetch waits while the batch dialog is open or a column is being dragged (the handle would be replaced mid-drag).
  await live(['appmanage', 'devices'], load, () => Boolean(document.querySelector('dialog[open]')) || drag !== null);
  await act('POST', '/api/appmanage/refresh', {}).catch(report);
})().catch(report);
