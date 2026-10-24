import { $, $$, act, api, ask, catalogItems, fmt, html, shell, toast } from '/static/app.js';

const KIND = { app: 'App', ota: 'OTA' };
const DAYS = ['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays'];
const R = { list: [], items: [], projects: [], devices: [], cur: null, pick: { item_ids: [], project_ids: [] } };

const uiToSpec = u => (u + 6) % 7;
const specToUi = d => (d + 1) % 7;

function sentence(spec) {
  if (spec.every === 'hours') return spec.hours === 1 ? 'Every hour' : `Every ${spec.hours} hours`;
  if (spec.every === 'day') return `Every day at ${spec.at}`;
  const names = [...spec.days].sort().map(d => DAYS[d]);
  if (!names.length) return `Weekly at ${spec.at}`;
  return `${names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}` : names[0]} at ${spec.at}`;
}

function targetText(kind, t) {
  if (t.all) return kind === 'app' ? 'All apps' : 'All devices';
  if (kind === 'app') return [...(t.item_ids || []).map(id => (R.items.find(i => i.id === id) || {}).label || `item ${id}`), ...(t.project_ids || []).map(id => (R.projects.find(p => p.id === id) || {}).label || `project ${id}`)].join(', ');
  return (t.device_ids || []).map(id => (R.devices.find(d => d.id === id) || {}).name || `device ${id}`).join(', ');
}

function optionText(s) {
  const o = s.options;
  if (s.kind === 'app') return [o.update && 'Update patches', o.build && (o.force ? 'Rebuild even if unchanged' : 'Build when changed')].filter(Boolean).join(' · ');
  return o.retries ? `Retry offline devices ${o.retries}×` : 'Skip offline devices';
}

function countsText(c) {
  const parts = Object.entries(c).map(([k, n]) => `${n} ${k}`);
  return parts.length ? parts.join(' · ') : 'never';
}

function renderList() {
  $('#list').innerHTML = html`${R.list.map(s => html`<div class="card sch" tabindex="0" data-id="${s.id}" ${R.cur && R.cur.id === s.id ? html`aria-current="true"` : ''} style="${s.enabled ? '' : 'opacity:.6'}"><div class="row between" style="flex-wrap:nowrap;align-items:flex-start"><div style="min-width:0"><div class="row"><h3>${s.name}</h3><span class="badge b-mute plain">${KIND[s.kind]}</span></div><p class="sm">${sentence(s.spec)} <span class="muted">· ${targetText(s.kind, s.target)}</span></p><p class="xs muted">${optionText(s)}</p><p class="xs muted" style="margin-top:.25rem">Next: <b>${s.enabled ? fmt.abs(s.next_run_at) : 'paused'}</b> ${s.enabled ? fmt.rel(s.next_run_at) : ''} · Last: ${s.last_run_at ? fmt.abs(s.last_run_at) : 'never'} <span class="badge ${s.last_counts.failed ? 'b-warn' : 'b-ok'}" style="height:1.125rem">${countsText(s.last_counts)}</span></p></div><div class="row" style="flex-wrap:nowrap"><button class="btn btn-ghost btn-sm" type="button" data-run="${s.id}">Run now</button><span class="switch"><input type="checkbox" data-en="${s.id}" ${s.enabled ? html`checked` : ''} aria-label="Enable ${s.name}"><span></span></span></div></div></div>`)}`;
}

function renderTimeline(now) {
  const t0 = Date.parse(now), span = 86400000;
  const ticks = R.list.filter(s => s.enabled).flatMap(s => s.next_runs.filter(r => Date.parse(r) - t0 < span).map(r => ({ s, r })));
  $('#tl').innerHTML = html`<span class="now-m">now</span>${ticks.map(({ s, r }) => html`<i class="ev k-${s.kind}" style="left:${Number(((Date.parse(r) - t0) / span) * 100).toFixed(2)}%" title="${s.name} ${fmt.abs(r)}"></i>`)}`;
  $('#tll').innerHTML = html`${R.list.filter(s => s.enabled).map(s => html`<li><i class="k-${s.kind}"></i>${s.name}</li>`)}`;
}

function chip(name, value, label, checked) {
  return html`<label class="chip"><input type="checkbox" name="${name}" value="${value}" ${checked ? html`checked` : ''}><span>${label}</span></label>`;
}

const pickCount = () => R.pick.item_ids.length + R.pick.project_ids.length;
const pickLabel = () => `Pick apps…${pickCount() ? ` (${pickCount()})` : ''}`;

function renderTargets(kind, t) {
  const app = kind === 'app' ? t : null, ota = kind === 'ota' ? t : null;
  R.pick = { item_ids: [...((app && app.item_ids) || [])], project_ids: [...((app && app.project_ids) || [])] };
  $('#tg-app').innerHTML = html`${chip('ta', 'all', `All apps (${R.items.length + R.projects.length})`, !app || app.all)}<button class="btn btn-ghost btn-sm" type="button" id="pick">${pickLabel()}</button>`;
  $('#tg-ota').innerHTML = html`${chip('to', 'all', 'All devices', !ota || ota.all)}${R.devices.map(d => chip('to', d.id, d.name, !!(ota && ota.device_ids && ota.device_ids.includes(d.id))))}`;
}

const kindNow = () => $('[name=act]:checked').value;

function readSpec() {
  const f = $('[name=f]:checked').value;
  if (f === 'hours') return { every: 'hours', hours: Number($('#hh-n').value || $('#hh').value) };
  if (f === 'day') return { every: 'day', at: $('#tm').value };
  return { every: 'week', days: $$('#days input:checked').map(i => uiToSpec(Number(i.value))), at: $('#tm').value };
}

function readTarget(kind) {
  if (kind === 'app') {
    if ($('[name=ta][value=all]').checked || !pickCount()) return { all: true };
    const t = {};
    if (R.pick.item_ids.length) t.item_ids = R.pick.item_ids;
    if (R.pick.project_ids.length) t.project_ids = R.pick.project_ids;
    return t;
  }
  const v = $$('[name=to]:checked').map(i => i.value);
  return v.includes('all') || !v.length ? { all: true } : { device_ids: v.map(Number) };
}

function readOptions(kind) {
  const notify = $('#notify').checked;
  if (kind === 'app') { const update = $('#o-upd').checked, build = $('#o-bld').checked; return { update, build, force: build && !update && $('#o-force').checked, notify }; }
  return { retries: Math.min(5, Math.max(0, Number($('#o-retries').value) || 0)), notify };
}

function syncForce() {
  const f = $('#o-force');
  f.disabled = !($('#o-bld').checked && !$('#o-upd').checked);
  if (f.disabled) f.checked = false;
}

function showKind(kind) {
  $$('[data-act]').forEach(el => { el.hidden = el.dataset.act !== kind; });
  $$('[id^=tg-]').forEach(el => { el.hidden = el.id !== `tg-${kind}`; });
}

function showRepeat(f) {
  $('#every-h').hidden = f !== 'hours';
  $('#at').hidden = f === 'hours';
  $('#days').hidden = f !== 'week';
}

let pv = null;
function preview() {
  clearTimeout(pv);
  pv = setTimeout(async () => {
    const spec = readSpec();
    $('#human').textContent = `${sentence(spec)} · ${targetText(kindNow(), readTarget(kindNow()))}`;
    try {
      const { runs } = await api('POST', '/api/schedules/preview', { spec });
      $('#nexts').innerHTML = html`${runs.map(r => html`<li><span>${fmt.abs(r)}</span> <span class="muted">${fmt.rel(r)}</span></li>`)}`;
    } catch (e) {
      $('#nexts').innerHTML = html`<li class="xs" style="color:hsl(var(--destructive))">${e.message}</li>`;
    }
  }, 300);
}

function edit(s) {
  R.cur = s;
  const kind = s ? s.kind : 'app', spec = s ? s.spec : { every: 'day', at: '04:00' }, o = s ? s.options : {};
  $('#sn').value = s ? s.name : '';
  $(`[name=act][value=${kind}]`).checked = true;
  $('#o-upd').checked = kind === 'app' && s ? !!o.update : true;
  $('#o-bld').checked = kind === 'app' && s ? !!o.build : true;
  $('#o-force').checked = !!o.force;
  syncForce();
  $('#o-retries').value = o.retries || 0;
  $('#notify').checked = o.notify !== false;
  $(`[name=f][value=${spec.every}]`).checked = true;
  if (spec.every === 'hours') { $('#hh').value = [1, 3, 6, 12, 24].includes(spec.hours) ? spec.hours : ''; $('#hh-n').value = spec.hours; }
  if (spec.at) $('#tm').value = spec.at;
  $$('#days input').forEach(i => { i.checked = spec.every === 'week' && spec.days.map(specToUi).includes(Number(i.value)); });
  renderTargets(kind, s ? s.target : null);
  showKind(kind);
  showRepeat(spec.every);
  $('#del').hidden = $('#runnow').hidden = !s;
  $('#ed').textContent = s ? 'Edit schedule' : 'New schedule';
  renderList();
  preview();
}

async function load() {
  const [{ schedules }, settings] = await Promise.all([api('GET', '/api/schedules'), api('GET', '/api/settings')]);
  R.list = schedules;
  $('#srvtime').textContent = `Server time ${new Date(settings.server.now).toLocaleString([], { timeZone: settings.server.tz, weekday: 'short', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })} · ${settings.server.tz}`;
  renderList();
  renderTimeline(settings.server.now);
}

async function save() {
  const kind = kindNow();
  const body = { name: $('#sn').value.trim(), kind, spec: readSpec(), target: readTarget(kind), options: readOptions(kind) };
  try {
    if (R.cur) await act('PUT', `/api/schedules/${R.cur.id}`, body, 'Schedule saved');
    else { const r = await act('POST', '/api/schedules', body, 'Schedule saved'); R.cur = { id: r.id }; }
    await load();
    edit(R.list.find(s => s.id === R.cur.id) || null);
  } catch (_) { /* shown */ }
}

$('#edc').addEventListener('change', e => {
  if (e.target.name === 'act') showKind(e.target.value);
  if (e.target.name === 'f') showRepeat(e.target.value);
  if (e.target.id === 'hh' && e.target.value) $('#hh-n').value = e.target.value;
  if (e.target.id === 'o-upd' || e.target.id === 'o-bld') syncForce();
  if (e.target.name === 'ta' && e.target.checked) { R.pick = { item_ids: [], project_ids: [] }; $('#tg-app button').textContent = pickLabel(); }
  if (e.target.name === 'to') {
    const all = $('[name=to][value=all]');
    if (e.target === all && all.checked) $$('[name=to]').forEach(i => { if (i !== all) i.checked = false; });
    else if (e.target !== all && e.target.checked) all.checked = false;
  }
  preview();
});
$('#edc').addEventListener('input', e => { if (e.target.id === 'tm' || e.target.id === 'hh-n') preview(); });
$('#edc').addEventListener('click', async e => {
  if (e.target.id !== 'pick') return;
  const row = (cls, o, text) => html`<label class="row sm"><input type="checkbox" class="cb ${cls}" value="${o.id}" ${R.pick[cls === 'pi' ? 'item_ids' : 'project_ids'].includes(o.id) ? html`checked` : ''}> ${o.label} <span class="xs muted mono">${text}</span></label>`;
  $('#pick-list').innerHTML = html`<h3 class="sm">Apps</h3>${R.items.map(it => row('pi', it, it.package))}<h3 class="sm">Followed projects</h3>${R.projects.length ? R.projects.map(p => row('pp', p, p.package || p.repo)) : html`<p class="xs muted">None followed</p>`}`;
  const btn = e.target;
  if (await ask($('#dlg-items')) !== 'ok') return;
  R.pick = { item_ids: $$('#pick-list .pi:checked').map(i => Number(i.value)), project_ids: $$('#pick-list .pp:checked').map(i => Number(i.value)) };
  if (pickCount()) $('[name=ta][value=all]').checked = false;
  btn.textContent = pickLabel();
  preview();
});
$('#list').addEventListener('click', async e => {
  const run = e.target.closest('[data-run]'), en = e.target.closest('[data-en]'), card = e.target.closest('.sch');
  try {
    if (run) { const s = R.list.find(x => x.id === Number(run.dataset.run)); await act('POST', `/api/schedules/${s.id}/run`, {}, `Started: ${s.name} — see Builds`); return load(); }
    if (en) { await act('PUT', `/api/schedules/${en.dataset.en}`, { enabled: en.checked }); return load(); }
    if (card) edit(R.list.find(x => x.id === Number(card.dataset.id)));
  } catch (_) { load(); }
});
$('#list').addEventListener('keydown', e => {
  if ((e.key !== 'Enter' && e.key !== ' ') || !e.target.matches('.sch')) return;
  e.preventDefault();
  edit(R.list.find(x => x.id === Number(e.target.dataset.id)));
});
$('#save').addEventListener('click', save);
$('#cancel').addEventListener('click', () => edit(R.cur && R.cur.name ? R.cur : null));
$('#new').addEventListener('click', () => edit(null));
$('#runnow').addEventListener('click', () => act('POST', `/api/schedules/${R.cur.id}/run`, {}, 'Running now — see Builds').then(load).catch(() => {}));
$('#del').addEventListener('click', async () => {
  if (!confirm(`Delete schedule "${R.cur.name}"?`)) return;
  try { await act('DELETE', `/api/schedules/${R.cur.id}`, {}, 'Schedule deleted'); await load(); edit(null); } catch (_) { /* shown */ }
});

(async () => {
  shell();
  const [cat, { projects }, { devices }] = await Promise.all([api('GET', '/api/catalog'), api('GET', '/api/projects'), api('GET', '/api/devices')]);
  R.projects = projects;
  R.items = catalogItems(cat);
  R.devices = devices;
  await load();
  edit(R.list[0] || null);
})().catch(e => toast(e.message, 'error'));
