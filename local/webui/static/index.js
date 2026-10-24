import { $, $$, act, api, ask, catalogItems, every, fillDownloads, fmt, html, iconTile, itemStatus, shell, STATUS, toast } from '/static/app.js';

const state = { catalog: null, upstreams: [], filter: { s: 'all', q: '' }, dragging: false };
const openIds = () => new Set(JSON.parse(localStorage.getItem('relume-open') || '[]'));
const allItems = () => catalogItems(state.catalog);
const groupsWithUngrouped = () => [...state.catalog.groups,
  { id: 0, name: 'Ungrouped', color: null, items: state.catalog.ungrouped }];

function badge(it, s) {
  if (s === 'run') {
    const j = it.active_job;
    const txt = j.status === 'queued' ? 'Queued'
      : `Building ${j.step_count ? Math.round((100 * j.step_index) / j.step_count) : 0} %`;
    return html`<span class="badge b-info">${txt}</span>`;
  }
  if (s === 'fail') {
    const b = it.last_build;
    return html`<span class="badge b-err">Failed #${b.job_id}</span><div class="why xs"><span class="muted">${b.reason || ''}</span> · <a href="/builds.html?job=${b.job_id}">Log</a></div>`;
  }
  return html`<span class="badge ${STATUS[s].cls}">${STATUS[s].label}</span>`;
}

function itemRow(it) {
  const s = itemStatus(it), up = it.upstream || {};
  const current = !up.latest_tag || up.latest_tag === up.downloaded_tag;
  const xposed = it.engine !== 'morphe';
  const updTitle = xposed ? `Updates module ${up.repo} for all its apps` : `Updates the ${it.source_name} source for all its items`;
  const rebuildLabel = s === 'fail' ? 'Retry' : s === 'new' ? 'Build' : 'Rebuild';
  return html`<div class="item" draggable="true" data-item="${it.id}" data-s="${s}">
<span class="handle" aria-label="Reorder ${it.label}; Alt+arrow keys" role="button" tabindex="0"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/></svg></span>${iconTile(it)}
<div style="min-width:0"><div class="row" style="gap:.375rem"><a href="/item.html?id=${it.id}" style="font-weight:600;text-decoration:none">${it.label}</a><span class="src">${it.source_name}</span>${xposed ? html`<span class="badge b-mute plain">Xposed · ${it.engine === 'lspatch' ? 'LSPatch' : 'NPatch'}</span>` : ''}</div>
<div class="xs muted mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${it.package} · ${(it.abis || []).join(', ')}</div></div>
<div class="ver col-built"><small>Built app</small><span class="mono">${it.last_success ? it.last_success.app_version : '—'}</span></div>
<div class="ver col-up"><small>Patches · local → upstream</small><span class="mono">${up.downloaded_tag || '—'} → ${current ? (up.latest_tag || '—') : html`<span class="newer">${up.latest_tag}</span>`}</span></div>
<div>${badge(it, s)}</div>
<div class="acts">
<label class="row xs" style="gap:.25rem;flex-wrap:nowrap" title="Build on schedule"><span class="switch"><input type="checkbox" data-sw="enabled" ${it.enabled ? html`checked` : ''} aria-label="Build ${it.label}"><span></span></span>Build</label>
<label class="row xs" style="gap:.25rem;flex-wrap:nowrap" title="Update on schedule"><span class="switch"><input type="checkbox" data-sw="auto_update" ${it.auto_update ? html`checked` : ''} aria-label="Update ${it.label}"><span></span></span>Update</label>
<button class="btn btn-outline btn-sm" type="button" data-act="pull" title="${updTitle}" ${current ? html`disabled` : ''}>Update</button>
<button class="btn btn-outline btn-sm" type="button" data-act="build" ${s === 'run' ? html`disabled` : ''}>${rebuildLabel}</button>
<details class="dd"><summary class="btn btn-ghost btn-sm btn-icon" aria-label="Download ${it.label}" title="${it.last_success ? 'Download' : 'No build yet'}" data-act="dl" ${it.last_success ? '' : html`aria-disabled="true"`}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg></summary><div class="menu" role="menu" data-dl-menu><span class="xs muted" style="padding:.5rem">Loading…</span></div></details>
</div></div>`;
}

function groupCard(g, open) {
  const counts = {};
  g.items.forEach(it => { const s = itemStatus(it); counts[s] = (counts[s] || 0) + 1; });
  const named = g.id !== 0;
  const colorAttr = g.color ? html`data-color style="--gc:${g.color}"` : '';
  return html`<details class="card group" data-group="${g.id}" ${named ? html`draggable="true"` : ''} ${open ? html`open` : ''} ${colorAttr}>
<summary>${named ? html`<span class="handle" aria-label="Reorder group ${g.name}; Alt+arrow keys" role="button" tabindex="0"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/></svg></span>` : ''}<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>
<input type="checkbox" class="cb" aria-label="Select all in ${g.name}" data-act="selall">${g.color ? html`<span class="gdot" style="--gc:${g.color}" aria-hidden="true"></span>` : ''}<h2>${g.name}</h2><span class="badge b-mute plain">${g.items.length}</span>
<span class="gsum xs hide-m" aria-label="Group status">${Object.entries(counts).map(([s, n]) => html`<span class="${STATUS[s].cls}"><i></i>${n} ${STATUS[s].label.toLowerCase()}</span>`)}</span><span class="grow"></span>
<button class="btn btn-ghost btn-sm hide-m" type="button" data-act="gpull">Update all</button>
<button class="btn btn-ghost btn-sm hide-m" type="button" data-act="gbuild">Rebuild all</button>
<details class="dd"><summary class="btn btn-ghost btn-sm btn-icon" aria-label="More actions for ${g.name}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg></summary>
<div class="menu" role="menu"><button role="menuitem" type="button" data-act="gota">OTA group to devices</button><a role="menuitem" href="/api/groups/${g.id}/download" download>Download all (zip) · newest successful build of each item</a>${named ? html`<button role="menuitem" type="button" data-act="grename">Rename…</button><button role="menuitem" type="button" data-act="gdelete">Delete group</button>` : ''}</div></details>
</summary><div data-list>${g.items.map(itemRow)}</div></details>`;
}

function renderTallies(items) {
  const by = s => items.filter(it => itemStatus(it) === s);
  const sources = new Set(items.map(it => it.source_id));
  const checked = items.map(it => it.upstream && it.upstream.checked_at).filter(Boolean).sort().pop();
  const queued = items.filter(it => it.active_job && it.active_job.status === 'queued').length;
  const failed = by('fail');
  $('#n-all').textContent = items.length;
  $('#s-all').textContent = `${state.catalog.groups.length} groups · ${sources.size} patch sources`;
  $('#n-upd').textContent = by('upd').length;
  $('#s-upd').textContent = `upstream checked ${fmt.rel(checked)}`;
  $('#n-run').textContent = by('run').length;
  $('#s-run').textContent = `+${queued} queued`;
  $('#n-fail').textContent = failed.length;
  $('#s-fail').textContent = failed.length ? failed[0].label : 'none';
}

function renderBanner() {
  const bad = state.upstreams.filter(u => u.check_error);
  const b = $('#banner');
  b.hidden = !bad.length;
  if (!bad.length) return;
  const shown = bad.slice(0, 3).map(u => `${u.repo} — ${u.check_error}`).join('; ');
  b.querySelector('span').textContent = `Upstream check problems: ${shown}${bad.length > 3 ? `; and ${bad.length - 3} more` : ''}`;
}

function applyFilter() {
  const { s, q } = state.filter;
  let n = 0;
  $$('[data-item]').forEach(el => {
    const ok = (s === 'all' || el.dataset.s === s) && el.textContent.toLowerCase().includes(q);
    el.hidden = !ok;
    if (ok) n++;
  });
  $$('[data-group]').forEach(g => {
    const any = g.querySelector('[data-item]:not([hidden])');
    g.hidden = !any && (s !== 'all' || q !== '');
    if (any && (s !== 'all' || q)) g.open = true;
  });
  $$('[data-f]').forEach(b => b.setAttribute('aria-pressed', b.dataset.f === s));
  $('#sf').value = s;
  $('#nores').hidden = n > 0;
}

function render() {
  const open = openIds();
  $('#groups').innerHTML = html`${groupsWithUngrouped().map(g => groupCard(g, open.has(g.id)))}`;
  renderTallies(allItems());
  renderBanner();
  applyFilter();
}

async function load() {
  const [catalog, ups] = await Promise.all([api('GET', '/api/catalog'), api('GET', '/api/upstreams')]);
  state.catalog = catalog;
  state.upstreams = ups.upstreams;
  render();
}

const itemById = id => allItems().find(it => it.id === Number(id));
const groupById = id => groupsWithUngrouped().find(g => g.id === Number(id));

async function pullAll(items) {
  const ids = [...new Set(items.map(it => it.upstream && it.upstream.id).filter(Boolean))];
  await Promise.all(ids.map(id => act('POST', `/api/upstreams/${id}/pull`, { rebuild: true })));
  toast(`Update queued for ${ids.length} upstream${ids.length === 1 ? '' : 's'}`);
}

async function otaGroup(g) {
  const ids = new Set(g.items.map(it => it.id));
  const { devices } = await api('GET', '/api/devices');
  let n = 0;
  for (const d of devices) {
    const mine = d.assignments.map(a => a.item_id).filter(id => ids.has(id));
    if (mine.length) { await act('POST', `/api/devices/${d.id}/ota`, { item_ids: mine }); n++; }
  }
  toast(n ? `OTA queued on ${n} device${n === 1 ? '' : 's'}` : 'No device has items of this group assigned');
}

document.addEventListener('click', async e => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const row = el.closest('[data-item]'), card = el.closest('[data-group]');
  const it = row && itemById(row.dataset.item), g = card && groupById(card.dataset.group);
  const a = el.dataset.act;
  if (a === 'grename' || a === 'gdelete') return;   // handled by the dialog code below
  if (a === 'selall') { e.stopPropagation(); $$('[data-item]', card).forEach(r => r.classList.toggle('selected', el.checked)); return; }
  if (a === 'dl') { if (!it.last_success) { e.preventDefault(); return; } fillDownloads(el.nextElementSibling, it.id).catch(() => {}); return; }
  e.preventDefault();
  try {
    if (a === 'pull') await act('POST', `/api/upstreams/${it.upstream.id}/pull`, { rebuild: true }, `Update queued: ${it.source_name}`);
    if (a === 'build') await act('POST', `/api/items/${it.id}/build`, {}, `Build queued: ${it.label}`);
    if (a === 'gpull') await pullAll(g.items);
    if (a === 'gbuild') {
      const picked = $$('[data-item].selected', card).map(r => Number(r.dataset.item));
      const ids = picked.length ? picked : g.items.filter(x => x.enabled).map(x => x.id);
      await act('POST', '/api/builds', { item_ids: ids }, `Queued ${ids.length} builds`);
    }
    if (a === 'gota') await otaGroup(g);
    await load();
  } catch (_) { /* act() already showed the error */ }
});

document.addEventListener('change', async e => {
  const sw = e.target.closest('[data-sw]');
  if (!sw) return;
  try { await act('PUT', `/api/items/${sw.closest('[data-item]').dataset.item}`, { [sw.dataset.sw]: sw.checked }); } catch (_) { /* shown */ }
  await load();
});

document.addEventListener('toggle', e => {
  const g = e.target.closest && e.target.matches('details[data-group]') ? e.target : null;
  if (!g) return;
  const ids = openIds();
  if (g.open) ids.add(Number(g.dataset.group)); else ids.delete(Number(g.dataset.group));
  localStorage.setItem('relume-open', JSON.stringify([...ids]));
}, true);

$$('[data-f]').forEach(b => b.addEventListener('click', () => { state.filter.s = b.dataset.f; applyFilter(); }));
$('#sf').addEventListener('change', e => { state.filter.s = e.target.value; applyFilter(); });
$('#q').addEventListener('input', e => { state.filter.q = e.target.value.trim().toLowerCase(); applyFilter(); });
$('#check').addEventListener('click', async () => {
  try { await act('POST', '/api/upstreams/check', {}, 'Upstream checked'); await load(); } catch (_) { /* shown */ }
});

shell();
load().catch(() => {});
every(5000, () => {
  if (state.dragging || document.querySelector('dialog[open], details.dd[open]')) return;
  load().catch(() => {});
});

// ---- reorder (spec ui §3 drag-and-drop) ----
let dragEl = null, fromList = null;
const listGroupId = list => Number(list.closest('[data-group]').dataset.group);

async function saveOrder(el, srcList) {
  try {
    if (el.hasAttribute('data-group')) {
      const ids = $$('#groups > [data-group]').map(g => Number(g.dataset.group)).filter(id => id !== 0);
      await api('PUT', '/api/groups/order', { ids });
    } else {
      const lists = new Set([el.closest('[data-list]'), srcList]);
      for (const list of lists) {
        if (!list) continue;
        const item_ids = $$('[data-item]', list).map(r => Number(r.dataset.item));
        await api('PUT', `/api/groups/${listGroupId(list)}/items/order`, { item_ids });
      }
    }
    toast('Order saved');
  } catch (e) {
    toast(e.message, 'error');
  }
  state.dragging = false;
  load().catch(() => {});
}

function placeOver(el, x, y, target) {
  const isItem = el.hasAttribute('data-item');
  let over = isItem ? target.closest('[data-item]') : target.closest('[data-group]');
  const list = isItem ? target.closest('[data-list]') : null;
  if (!isItem && over && over.dataset.group === '0') over = null;   // Ungrouped stays last
  if (over && over !== el) {
    const r = over.getBoundingClientRect();
    over.parentNode.insertBefore(el, y - r.top > r.height / 2 ? over.nextSibling : over);
    return true;
  }
  if (!over && list) { list.appendChild(el); return true; }
  if (!isItem && !over && target.closest('[data-group="0"]')) {
    $('#groups').insertBefore(el, $('#groups > [data-group="0"]'));
    return true;
  }
  return false;
}

document.addEventListener('dragstart', e => {
  dragEl = e.target.closest('[data-item]') || e.target.closest('[data-group]:not([data-group="0"])');
  if (!dragEl) return;
  e.stopPropagation();
  state.dragging = true;
  fromList = dragEl.closest('[data-list]');
  dragEl.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', '');
});
document.addEventListener('dragover', e => {
  if (!dragEl) return;
  if (placeOver(dragEl, e.clientX, e.clientY, e.target)) e.preventDefault();
});
document.addEventListener('dragend', () => {
  if (!dragEl) return;
  dragEl.classList.remove('dragging');
  const el = dragEl;
  dragEl = null;
  saveOrder(el, fromList);
});

let tEl = null, tFrom = null;
document.addEventListener('pointerdown', e => {
  const h = e.target.closest('.handle');
  if (!h || e.pointerType === 'mouse') return;
  tEl = h.closest('[data-item]') || h.closest('[data-group]');
  e.preventDefault();
  state.dragging = true;
  tFrom = tEl.closest('[data-list]');
  tEl.classList.add('touch-drag');
  try { h.setPointerCapture(e.pointerId); } catch (_) { /* capture unsupported */ }
});
document.addEventListener('pointermove', e => {
  if (!tEl) return;
  tEl.style.pointerEvents = 'none';
  const t = document.elementFromPoint(e.clientX, e.clientY);
  tEl.style.pointerEvents = '';
  if (t) placeOver(tEl, e.clientX, e.clientY, t);
});
const tEnd = () => {
  if (!tEl) return;
  tEl.classList.remove('touch-drag');
  const el = tEl;
  tEl = null;
  saveOrder(el, tFrom);
};
document.addEventListener('pointerup', tEnd);
document.addEventListener('pointercancel', tEnd);
document.addEventListener('click', e => { if (e.target.closest('summary .handle')) e.preventDefault(); }, true);
document.addEventListener('keydown', e => {
  const h = e.target.closest && e.target.closest('.handle');
  if (!h || !e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
  const el = h.closest('[data-item]') || h.closest('[data-group]');
  const sib = e.key === 'ArrowUp' ? el.previousElementSibling : el.nextElementSibling;
  if (!sib || sib.dataset.group === '0') return;
  e.preventDefault();
  if (e.key === 'ArrowUp') el.parentNode.insertBefore(el, sib); else el.parentNode.insertBefore(sib, el);
  h.focus();
  saveOrder(el, el.closest('[data-list]'));
});

// ---- group dialogs (spec §0.6 R6) ----
async function editGroup(g) {
  const dlg = $('#dlg-group');
  $('#dg-t').textContent = g ? 'Rename group' : 'New group';
  $('#g-name').value = g ? g.name : '';
  $('#g-color').value = (g && g.color) || '#10b981';
  $('#g-nocolor').checked = !!g && !g.color;
  if (await ask(dlg) !== 'ok') return;
  const body = { name: $('#g-name').value.trim(), color: $('#g-nocolor').checked ? null : $('#g-color').value };
  try {
    if (g) await act('PUT', `/api/groups/${g.id}`, body, 'Group saved');
    else await act('POST', '/api/groups', body, 'Group created');
    await load();
  } catch (_) { /* shown */ }
}
$('#g-color').addEventListener('input', () => { $('#g-nocolor').checked = false; });
$('#newgrp').addEventListener('click', () => editGroup(null));
document.addEventListener('click', async e => {
  const el = e.target.closest('[data-act="grename"], [data-act="gdelete"]');
  if (!el) return;
  const g = groupById(el.closest('[data-group]').dataset.group);
  if (el.dataset.act === 'grename') return editGroup(g);
  if (!confirm(`Delete group "${g.name}"? Its items move to Ungrouped.`)) return;
  try { await act('DELETE', `/api/groups/${g.id}`, {}, 'Group deleted'); await load(); } catch (_) { /* shown */ }
});

// ---- Add app and New source ----
let sourcesCache = [];
const KS_DEFAULT = { morphe: 'morphe.keystore', lspatch: 'lspatch-signing.p12', npatch: 'fiorenmas.ks' };

function syncEngine(engine) {
  const mod = engine !== 'morphe';
  $('[data-repo-label]').textContent = mod ? 'Module repo' : 'Patches repo';
  $('#s-re').required = mod;
  $('#s-re-h').textContent = mod ? 'Required: must match exactly one asset of the module release.' : 'Optional for Morphe (default \\.mpp$).';
}

// src = null creates a source, otherwise the form is prefilled and sends PUT.
async function sourceDialog(src) {
  const { upstreams } = await api('GET', '/api/upstreams');
  $('#s-cli').innerHTML = html`${upstreams.filter(u => u.kind === 'cli').map(u => html`<option value="${u.id}">${u.repo} · ${u.channel === 'tag' ? u.tag : u.channel}</option>`)}`;
  $('#ds-t').textContent = src ? `Edit source ${src.id}` : 'New source';
  $('#s-id').value = src ? src.id : '';
  $('#s-id').disabled = !!src;
  $('#s-name').value = src ? src.name : '';
  $('#s-engine').value = src ? src.engine : 'morphe';
  $('#s-engine').disabled = !!src;   // the engine decides the upstream kind and the item rules: not editable
  $('#s-repo').value = src ? src.repo : '';
  $('#s-ch').value = src ? src.channel : 'stable';
  $('#s-tag-f').hidden = $('#s-ch').value !== 'tag';
  $('#s-tag').value = (src && src.tag) || '';
  $('#s-re').value = (src && src.asset_regex) || '';
  $('#s-ks').value = src ? src.keystore : KS_DEFAULT.morphe;
  if (src) $('#s-cli').value = src.cli_upstream_id;
  syncEngine($('#s-engine').value);
  if (await ask($('#dlg-src')) !== 'ok') return null;
  const engine = $('#s-engine').value, ch = $('#s-ch').value;
  const body = {
    name: $('#s-name').value.trim(), engine,
    repo: $('#s-repo').value.trim(), channel: ch, keystore: $('#s-ks').value.trim(),
    cli_upstream_id: Number($('#s-cli').value),
  };
  if (ch === 'tag') body.tag = $('#s-tag').value.trim();
  if ($('#s-re').value.trim()) body.asset_regex = $('#s-re').value.trim();
  if (src) {
    await act('PUT', `/api/sources/${src.id}`, body, 'Source saved');
    return src.id;
  }
  body.id = $('#s-id').value.trim();
  const r = await act('POST', '/api/sources', body, 'Source created');
  return r.id;
}
$('#s-ch').addEventListener('change', e => { $('#s-tag-f').hidden = e.target.value !== 'tag'; });
$('#s-engine').addEventListener('change', e => {
  syncEngine(e.target.value);
  $('#s-ks').value = KS_DEFAULT[e.target.value];
});

// Module settings (engine_options) apply only to LSPatch/NPatch sources.
function syncModule() {
  const s = sourcesCache.find(x => x.id === $('#a-src').value);
  const mod = !!s && s.engine !== 'morphe';
  $('#a-mod').hidden = !mod;
  $('#a-src-edit').disabled = $('#a-src-del').disabled = !s;
  if (!mod) return;
  const max = s.engine === 'npatch' ? 4 : 3;
  $('#a-sig').innerHTML = html`${Array.from({ length: max + 1 }, (_, n) => html`<option value="${n}">${n}</option>`)}`;
}

async function fillSources(selectId) {
  const { sources } = await api('GET', '/api/sources');
  sourcesCache = sources;
  $('#a-src').innerHTML = html`${sources.map(s => html`<option value="${s.id}">${s.name} · ${s.engine}</option>`)}<option value="__new">New source…</option>`;
  if (selectId) $('#a-src').value = selectId;
  syncModule();
}

$('#a-src').addEventListener('change', async e => {
  if (e.target.value === '__new') {
    try { await fillSources(await sourceDialog(null)); } catch (_) { await fillSources(); }
  }
  syncModule();
});
$('#a-src-edit').addEventListener('click', async () => {
  const src = sourcesCache.find(x => x.id === $('#a-src').value);
  if (!src) return;
  try { await fillSources(await sourceDialog(src)); } catch (_) { await fillSources(src.id); }
});
$('#a-src-del').addEventListener('click', async () => {
  const src = sourcesCache.find(x => x.id === $('#a-src').value);
  if (!src || !confirm(`Delete source "${src.name}"?`)) return;
  try { await act('DELETE', `/api/sources/${src.id}`, {}, 'Source deleted'); } catch (_) { /* 409 text shown */ }
  await fillSources();
});
$('#add').addEventListener('click', async () => {
  try {
    await fillSources();
    $('#a-grp').innerHTML = html`${state.catalog.groups.map(g => html`<option value="${g.id}">${g.name}</option>`)}<option value="">Ungrouped</option>`;
    if (await ask($('#dlg-add')) !== 'ok' || $('#a-src').value === '__new') return;
    const body = { source_id: $('#a-src').value, package: $('#a-pkg').value.trim(),
      group_id: $('#a-grp').value ? Number($('#a-grp').value) : null };
    if ($('#a-name').value.trim()) body.display_name = $('#a-name').value.trim();
    if (!$('#a-mod').hidden) body.engine_options = { sigbypass: Number($('#a-sig').value), injectdex: $('#a-dex').checked };
    await act('POST', '/api/items', body, 'Added; the icon appears after the next fetch');
    await load();
  } catch (_) { /* shown */ }
});
