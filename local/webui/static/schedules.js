import { $, $$, act, api, fmt, html, icon, shell, toast } from '/static/app.js';

const DAYS = ['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays'];
const UI_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const PRESET_HOURS = [1, 3, 6, 12, 24];
const DEFAULT_SPEC = { every: 'day', at: '04:00' };
const R = { ota: [], devices: [], cur: null, global: null };

const uiToSpec = u => (u + 6) % 7;
const specToUi = d => (d + 1) % 7;
const byId = id => document.getElementById(id);

function sentence(spec) {
  if (spec.every === 'hours') return spec.hours === 1 ? 'Every hour' : `Every ${spec.hours} hours`;
  if (spec.every === 'day') return `Every day at ${spec.at}`;
  const names = [...spec.days].sort().map(d => DAYS[d]);
  return `${names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}` : names[0]} at ${spec.at}`;
}

// The day / week / hours form shared by the global Custom choice and the OTA dialog; `p` prefixes the element ids.
function specForm(p, spec) {
  return html`<div class="field"><label for="${p}-f">Repeat</label><span class="select"><select class="dropdown" id="${p}-f" style="width:140px"><option value="hours" ${spec.every === 'hours' ? 'selected' : ''}>Hourly</option><option value="day" ${spec.every === 'day' ? 'selected' : ''}>Daily</option><option value="week" ${spec.every === 'week' ? 'selected' : ''}>Weekly</option></select></span></div>
<div class="field" id="${p}-hw"><label for="${p}-h">Every (hours)</label><span class="input" style="width:100px"><input id="${p}-h" type="number" min="1" max="168" value="${spec.hours || 6}"></span></div>
<div class="field" id="${p}-aw"><label for="${p}-at">At</label><span class="input" style="width:120px"><input id="${p}-at" type="time" value="${spec.at || DEFAULT_SPEC.at}"></span></div>
<div class="field" id="${p}-dw"><span class="label" id="${p}-dl">On days</span><div class="days" role="group" aria-labelledby="${p}-dl">${UI_DAYS.map((n, i) => html`<label class="row"><input type="checkbox" name="${p}-d" value="${i}" ${spec.every === 'week' && spec.days.map(specToUi).includes(i) ? 'checked' : ''}>${n}</label>`)}</div></div>`;
}

function syncSpecForm(p) {
  const f = byId(`${p}-f`).value;
  byId(`${p}-hw`).hidden = f !== 'hours';
  byId(`${p}-aw`).hidden = f === 'hours';
  byId(`${p}-dw`).hidden = f !== 'week';
}

function readSpecForm(p) {
  const f = byId(`${p}-f`).value;
  if (f === 'hours') return { every: 'hours', hours: Number(byId(`${p}-h`).value) };
  if (f === 'day') return { every: 'day', at: byId(`${p}-at`).value };
  return { every: 'week', days: $$(`[name=${p}-d]:checked`).map(i => uiToSpec(Number(i.value))), at: byId(`${p}-at`).value };
}

// ---- global schedule (spec §7.5) ----

function renderGlobal() {
  const g = R.global, s = g.spec;
  const preset = s.every === 'hours' && PRESET_HOURS.includes(s.hours) ? String(s.hours) : 'custom';
  $('#g-seg').innerHTML = html`${[...PRESET_HOURS.map(h => [String(h), `${h} h`]), ['custom', 'Custom']].map(([v, l]) => html`<label><input type="radio" name="iv" value="${v}" ${v === preset ? 'checked' : ''}><span>${l}</span></label>`)}`;
  $('#g-custom').innerHTML = html`${specForm('gs', preset === 'custom' ? s : DEFAULT_SPEC)}`;
  syncSpecForm('gs');
  $('#g-custom').hidden = $('#g-save').hidden = preset !== 'custom';
  $('#g-en').checked = g.enabled;
  $('#g-notify').checked = g.options.notify;
  $('#g-next').textContent = g.enabled && g.next_run_at ? fmt.abs(g.next_run_at) : 'Paused';
  $('#g-rel').textContent = g.enabled && g.next_run_at ? fmt.rel(g.next_run_at) : '';
  $('#g-last').textContent = g.last_run_at ? `Last run ${fmt.abs(g.last_run_at)}` : 'Never run';
  $('#g-sub').textContent = `${sentence(s)}. Enabling a new app builds it at once, without waiting.`;
}

async function putGlobal(body, msg) {
  try {
    R.global = await act('PUT', '/api/schedule', body, msg);
    renderGlobal();
  } catch (_) { renderGlobal(); }
}

$('#g-seg').addEventListener('change', e => {
  if (e.target.value === 'custom') { $('#g-custom').hidden = $('#g-save').hidden = false; return; }
  putGlobal({ spec: { every: 'hours', hours: Number(e.target.value) } }, 'Schedule saved');
});
$('#g-custom').addEventListener('change', e => { if (e.target.id === 'gs-f') syncSpecForm('gs'); });
$('#g-save').addEventListener('click', () => putGlobal({ spec: readSpecForm('gs') }, 'Schedule saved'));
$('#g-en').addEventListener('change', e => putGlobal({ enabled: e.target.checked }, e.target.checked ? 'Schedule on' : 'Schedule paused'));
$('#g-notify').addEventListener('change', e => putGlobal({ options: { notify: e.target.checked } }));
$('#g-run').addEventListener('click', () => act('POST', '/api/schedule/run', {}, 'Update run started — see Builds').catch(() => {}));

// ---- OTA schedules ----

const deviceNames = t => (t.all ? R.devices.map(d => d.name) : (t.device_ids || []).map(id => (R.devices.find(d => d.id === id) || {}).name || `device ${id}`));

function countsBadge(c) {
  const parts = Object.entries(c).map(([k, n]) => `${n} ${k}`);
  if (!parts.length) return html`<span class="badge">Never run</span>`;
  return html`<span class="badge ${c.failed ? 'warn' : 'ok'}">${icon(c.failed ? 'warning' : 'check')}${parts.join(' · ')}</span>`;
}

function renderList() {
  $('#list').innerHTML = html`${R.ota.length ? R.ota.map(s => html`<div class="ota" data-id="${s.id}" style="${s.enabled ? '' : 'opacity:.65'}">
<span class="ic">${icon('devices')}</span>
<div><div class="strong">${s.name}</div><div class="caption">${sentence(s.spec)} · ${s.options.retries ? `${s.options.retries} retries` : 'no retries'} · ${s.enabled ? `next ${fmt.abs(s.next_run_at)}` : 'paused'}</div><div class="devs">${deviceNames(s.target).map(n => html`<span class="badge">${n}</span>`)}</div></div>
${countsBadge(s.last_counts)}
<div class="row" style="gap:2px"><label class="switch" style="margin-right:8px"><input type="checkbox" data-en="${s.id}" ${s.enabled ? 'checked' : ''} aria-label="${s.name} on"><span class="track"></span></label>
<button class="icon-btn" type="button" data-run="${s.id}" aria-label="Run ${s.name} now" title="Run now">${icon('play')}</button>
<button class="icon-btn" type="button" data-edit="${s.id}" aria-label="Edit ${s.name}" title="Edit">${icon('edit')}</button>
<button class="icon-btn" type="button" data-del="${s.id}" aria-label="Delete ${s.name}" title="Delete">${icon('delete')}</button></div></div>`)
    : html`<div class="card-pad subtle-text">No OTA schedule yet.</div>`}`;
}

function openDialog(s) {
  R.cur = s;
  const spec = s ? s.spec : DEFAULT_SPEC, t = s ? s.target : { all: true };
  $('#do-t').textContent = s ? 'Edit OTA schedule' : 'New OTA schedule';
  $('#o-name').value = s ? s.name : '';
  $('#o-form').innerHTML = html`${specForm('os', spec)}`;
  syncSpecForm('os');
  $('#o-retries').value = s ? s.options.retries : 0;
  $('#o-notify').checked = s ? s.options.notify : true;
  $('#o-devs').innerHTML = html`<label class="row"><input type="checkbox" name="od" value="all" ${t.all ? 'checked' : ''}>All phones</label>${R.devices.map(d => html`<label class="row"><input type="checkbox" name="od" value="${d.id}" ${!t.all && (t.device_ids || []).includes(d.id) ? 'checked' : ''}>${d.name}</label>`)}`;
  byId('dlg-ota').showModal();
  preview();
}

let pv = null;
function preview() {
  clearTimeout(pv);
  pv = setTimeout(async () => {
    try {
      const { runs } = await api('POST', '/api/schedules/preview', { spec: readSpecForm('os') });
      $('#o-next').textContent = `Next runs: ${runs.slice(0, 3).map(fmt.abs).join(' · ')}`;
    } catch (e) { $('#o-next').textContent = e.message; }
  }, 300);
}

function readTarget() {
  const v = $$('[name=od]:checked').map(i => i.value);
  return v.includes('all') || !v.length ? { all: true } : { device_ids: v.map(Number) };
}

$('#dlg-ota').addEventListener('change', e => {
  if (e.target.id === 'os-f') syncSpecForm('os');
  if (e.target.name === 'od') {
    const all = $('[name=od][value=all]');
    if (e.target === all && all.checked) $$('[name=od]').forEach(i => { if (i !== all) i.checked = false; });
    else if (e.target !== all && e.target.checked) all.checked = false;
  }
  preview();
});
$('#dlg-ota').addEventListener('input', e => { if (e.target.id === 'os-at' || e.target.id === 'os-h') preview(); });
$('#o-cancel').addEventListener('click', () => byId('dlg-ota').close());
$('#o-save').addEventListener('click', async () => {
  const body = { name: $('#o-name').value.trim(), kind: 'ota', spec: readSpecForm('os'), target: readTarget(),
    options: { retries: Math.min(5, Math.max(0, Number($('#o-retries').value) || 0)), notify: $('#o-notify').checked } };
  try {
    if (R.cur) await act('PUT', `/api/schedules/${R.cur.id}`, body, 'Schedule saved');
    else await act('POST', '/api/schedules', body, 'Schedule saved');
    byId('dlg-ota').close();
    await load();
  } catch (_) { /* shown */ }
});
$('#new').addEventListener('click', () => openDialog(null));
$('#list').addEventListener('click', async e => {
  const run = e.target.closest('[data-run]'), edit = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]'), en = e.target.closest('[data-en]');
  const find = id => R.ota.find(x => x.id === Number(id));
  try {
    if (run) { await act('POST', `/api/schedules/${run.dataset.run}/run`, {}, `Started: ${find(run.dataset.run).name} — see Builds`); await load(); }
    else if (edit) openDialog(find(edit.dataset.edit));
    else if (del) {
      if (!confirm(`Delete schedule "${find(del.dataset.del).name}"?`)) return;
      await act('DELETE', `/api/schedules/${del.dataset.del}`, {}, 'Schedule deleted');
      await load();
    } else if (en) { await act('PUT', `/api/schedules/${en.dataset.en}`, { enabled: en.checked }); await load(); }
  } catch (_) { load(); }
});

async function load() {
  const [{ schedules }, settings, g] = await Promise.all([api('GET', '/api/schedules'), api('GET', '/api/settings'), api('GET', '/api/schedule')]);
  R.ota = schedules.filter(s => s.kind === 'ota');
  R.global = g;
  $('#srvtime').textContent = `Server time ${new Date(settings.server.now).toLocaleString('en', { timeZone: settings.server.tz, weekday: 'short', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })} · ${settings.server.tz}`;
  renderGlobal();
  renderList();
}

(async () => {
  shell();
  R.devices = (await api('GET', '/api/devices')).devices.filter(d => d.kind !== 'app');
  await load();
})().catch(e => toast(e.message, 'error'));
