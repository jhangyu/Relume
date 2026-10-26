import { $, $$, act, api, every, html, icon, iconTile, JOB_BADGE, logStream, shell, toast } from '/static/app.js';

const qs = new URLSearchParams(location.search);
const ITEM = qs.get('item') ? Number(qs.get('item')) : null;
const STEPS = ['version', 'download', 'patch', 'verify', 'store'];
const STEP_RE = /^(\d\d:\d\d:\d\d) ::step::(\w+)(?:::(.*))?$/;
const B = { items: new Map(), schedules: new Map(), sel: null, es: null, text: '', marks: [], last: null, pct: 0, filt: ITEM ? 'item' : '' };
const RESULT = { success: 'Succeeded', failed: 'Failed', cancelled: 'Cancelled' };
const DAY_MS = 86400000;
const dayStart = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
const HM = { hour: '2-digit', minute: '2-digit', hour12: false };
// "Today 11:05", "Yesterday 20:00", "Mon 14:00" within a week, else "Oct 3 14:00" (UI language is English)
function when(iso) {
  if (!iso) return '—';
  const d = new Date(iso), days = Math.round((dayStart(new Date()) - dayStart(d)) / DAY_MS), hm = d.toLocaleTimeString('en', HM);
  if (days === 0) return `Today ${hm}`;
  if (days === 1) return `Yesterday ${hm}`;
  if (days > 1 && days < 7) return `${d.toLocaleDateString('en', { weekday: 'short' })} ${hm}`;
  return `${d.toLocaleDateString('en', { month: 'short', day: 'numeric' })} ${hm}`;
}
function dur(a, b) {
  if (!a || !b) return '—';
  const s = Math.max(0, Math.round((Date.parse(b) - Date.parse(a)) / 1000));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
}

const triggerText = j => (j.trigger === 'schedule' ? `schedule "${B.schedules.get(j.schedule_id) || j.schedule_id}"`
  : j.trigger === 'upstream' ? 'patch update' : j.trigger === 'after_build' ? 'after build' : 'manual');
const itemOf = j => B.items.get(j.target.item_id) || { label: `app ${j.target.item_id}`, bundle_name: '', enabled: false, abis: [] };

async function loadRefs() {
  const [apps, sch] = await Promise.all([api('GET', '/api/apps'), api('GET', '/api/schedules')]);
  apps.apps.forEach(a => B.items.set(a.id, { ...a, label: a.name, bundle_name: a.bundle.name }));
  sch.schedules.forEach(s => B.schedules.set(s.id, s.name));
}

async function renderQueue() {
  const [r, q] = await Promise.all([api('GET', '/api/jobs?kind=build&status=running'), api('GET', '/api/jobs?kind=build&status=queued&limit=100')]);
  const rows = [...r.jobs, ...q.jobs.reverse()];
  $('#qsum').textContent = `${r.jobs.length} running · ${q.jobs.length} queued`;
  $('#queue').innerHTML = html`<div class="label" style="padding:0 4px 4px">Queue</div>${rows.length ? rows.map(j => {
    const it = itemOf(j), running = j.status === 'running', ver = (j.detail && j.detail.app_version) || it.app_version;
    return html`<a class="job ${j.id === B.sel ? 'on' : ''}" href="?job=${j.id}" data-sel="${j.id}">${iconTile(it, 's40')}<div class="row"><span class="strong grow">${it.label}</span>${running ? html`<span class="caption">#${j.id}</span>` : html`<span class="badge">Queued</span>`}</div><div class="caption">${[it.bundle_name, ver].filter(Boolean).join(' · ')}</div>${running ? html`<div class="progress" role="progressbar" aria-label="${it.label} build progress" aria-valuenow="${j.id === B.sel ? B.pct : 0}" aria-valuemin="0" aria-valuemax="100"><span style="width:${j.id === B.sel ? B.pct : 0}%"></span></div>` : html`<div class="caption">Started by ${triggerText(j)}</div>`}</a>`;
  }) : html`<div class="caption" style="padding:4px">Nothing running or waiting.</div>`}`;
  return r.jobs[0];
}

function lineClass(t) {
  if (t.includes('::step::')) return 's';
  if (t.includes('[-]') || t.includes('::fail::')) return 'e';
  if (t.includes('[!]')) return 'w';
  if (t.includes('[+]')) return 'ok';
  return '';
}

function appendLines(chunk) {
  B.text += chunk;
  const log = $('#log');
  for (const t of chunk.split('\n')) {
    if (!t) continue;
    const d = document.createElement('div');
    d.className = lineClass(t);
    const s = document.createElement('span');
    s.className = 't';
    s.textContent = t.slice(0, 8);
    d.append(s, document.createTextNode(` ${t.slice(9)}`));
    log.appendChild(d);
    const m = STEP_RE.exec(t);
    if (m) B.marks.push({ at: m[1], step: m[2], detail: m[3] || '' });
  }
  renderProgress();
  if (B.follow) log.scrollTop = log.scrollHeight;
}

function renderProgress(final) {
  const status = final || (B.last && B.last.status);
  const cur = B.marks.length ? STEPS.indexOf(B.marks[B.marks.length - 1].step) : -1;
  B.pct = status === 'success' ? 100 : Math.max(0, Math.round(((cur + 0.5) / STEPS.length) * 100));
  const bar = $('#queue .job.on .progress');
  if (bar) { bar.setAttribute('aria-valuenow', B.pct); bar.firstElementChild.style.width = `${B.pct}%`; }
}

async function select(id) {
  if (B.es) B.es.close();
  B.text = '';
  B.marks = [];
  B.follow = true;
  $('#log').textContent = '';
  const j = await api('GET', `/api/jobs/${id}`);
  B.sel = id;
  B.last = j;
  const it = itemOf(j);
  $('#bt').textContent = `${it.label} #${j.id} · ${j.status === 'running' ? 'live log' : 'log'}`;
  const live = j.status === 'running' || j.status === 'queued';
  $('#bsub').textContent = `Started ${when(j.started_at)} · ${live ? 'running ' : ''}${dur(j.started_at, j.finished_at || new Date().toISOString())}`;
  $('#cancel').disabled = !live;
  renderProgress();
  $$('#queue .job').forEach(a => a.classList.toggle('on', Number(a.dataset.sel) === id));
  B.es = logStream(id, appendLines, status => {
    B.last = { ...B.last, status };
    $('#cancel').disabled = true;
    $('#bt').textContent = `${it.label} #${j.id} · log`;
    renderProgress(status);
    refresh();
  });
}

let oldest = null;
const warnPatches = j => (j.detail && j.detail.failed_patches || []).length ? html`<span class="caption">Patch failed: ${j.detail.failed_patches.join(', ')}</span>` : '';

function actions(j) {
  const it = itemOf(j), arts = (j.detail && j.detail.artifacts) || [];
  const log = html`<button class="icon-btn" type="button" data-sel="${j.id}" aria-label="View log of ${it.label} build ${j.id}" data-tip="View log">${icon('builds')}</button>`;
  if (j.status === 'success' && arts.length === 1) return html`${log}<a class="icon-btn" href="${arts[0].download_url}" download aria-label="Download ${it.label} ${arts[0].version_name || ''} APK" data-tip="Download APK">${icon('download')}</a>`;
  if (j.status === 'success' && arts.length) return html`${log}<details class="dd"><summary class="icon-btn" aria-label="Download ${it.label} APK" data-tip="Download APK">${icon('download')}</summary><div class="menu">${arts.map(a => html`<a href="${a.download_url}" download>${a.variant} · ${a.abi}</a>`)}</div></details>`;
  if (j.status === 'success') return log;
  return html`${log}<button class="icon-btn" type="button" data-retry="${j.target.item_id}" aria-label="Retry ${it.label} build" data-tip="Retry" ${it.enabled === false ? html`disabled` : ''}>${icon('rebuild')}</button>`;
}

function historyRow(j) {
  const it = itemOf(j), [cls] = JOB_BADGE[j.status], d = j.detail || {}, bundle = [it.bundle_name, d.patches_version].filter(Boolean).join(' ');
  return html`<tr data-id="${j.id}" data-q="${`${it.label} ${bundle}`.toLowerCase()}" ${j.id === B.sel ? html`aria-current="true"` : ''}><td><div class="row">${iconTile(it, 's32')}<span class="strong">${it.label}</span></div></td><td>${bundle || '—'}</td><td class="mono">${d.app_version || '—'}</td><td>${when(j.started_at)}</td><td>${dur(j.started_at, j.finished_at)}</td><td><span class="badge ${cls}">${RESULT[j.status] || j.status}</span>${j.status === 'failed' && j.reason ? html` <span class="caption danger">${j.reason}</span>` : ''} ${warnPatches(j)}</td><td style="text-align:right">${actions(j)}</td></tr>`;
}

function applySearch() {
  const q = $('#hq').value.trim().toLowerCase();
  $$('#hist tbody tr').forEach(tr => { tr.hidden = !!q && !tr.dataset.q.includes(q); });
}

async function renderHistory(append) {
  const p = new URLSearchParams({ kind: 'build', limit: '25' });
  if (B.filt === 'item') p.set('item_id', ITEM);
  else if (B.filt) p.set('status', B.filt);
  if (append && oldest) p.set('before_id', oldest);
  const { jobs } = await api('GET', `/api/jobs?${p}`);
  const done = jobs.filter(j => j.status !== 'queued' && j.status !== 'running');
  if (jobs.length) oldest = jobs[jobs.length - 1].id;
  const rows = html`${done.map(historyRow)}`;
  if (append) $('#hist tbody').insertAdjacentHTML('beforeend', html`${rows}`);
  else $('#hist tbody').innerHTML = html`${rows}`;
  $('#older').hidden = jobs.length < 25;
  $('#hsum').textContent = `${$$('#hist tbody tr').length} builds shown`;
  applySearch();
  return done[0];
}

async function refresh() {
  try { await renderQueue(); await renderHistory(false); } catch (_) { /* next tick retries */ }
}

document.addEventListener('click', async e => {
  const sel = e.target.closest('[data-sel]');
  const retry = e.target.closest('[data-retry]');
  try {
    if (sel) {
      e.preventDefault();
      history.replaceState(null, '', `?job=${sel.dataset.sel}${ITEM ? `&item=${ITEM}` : ''}`);
      await select(Number(sel.dataset.sel));
      $('.live').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    if (retry) await act('POST', `/api/apps/${retry.dataset.retry}/build`, {}, 'Build queued');
    if (retry) await refresh();
  } catch (_) { /* act() showed it */ }
});
$('#cancel').addEventListener('click', () => act('POST', `/api/jobs/${B.sel}/cancel`, {}, 'Cancel requested').then(refresh).catch(() => {}));
$('#log').addEventListener('scroll', () => { const l = $('#log'); B.follow = l.scrollTop + l.clientHeight >= l.scrollHeight - 24; });
$('#dlog').addEventListener('click', () => {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([B.text], { type: 'text/plain' }));
  a.download = `build-${B.sel}.log`;
  a.click();
  URL.revokeObjectURL(a.href);
});
$('#pills').addEventListener('click', e => {
  const b = e.target.closest('[data-filt]');
  if (!b) return;
  B.filt = b.dataset.filt;
  $$('#pills .pill').forEach(x => x.setAttribute('aria-pressed', x === b));
  renderHistory(false).catch(err => toast(err.message, 'error'));
});
$('#hq').addEventListener('input', applySearch);
$('#older').addEventListener('click', () => renderHistory(true).catch(() => {}));

(async () => {
  shell();
  if (ITEM) $$('#pills .pill').forEach(x => x.setAttribute('aria-pressed', x.dataset.filt === 'item'));
  else $('#pill-item').remove();
  await loadRefs();
  const running = await renderQueue();
  const newest = await renderHistory(false);
  const first = qs.get('job') ? Number(qs.get('job')) : running ? running.id : newest ? newest.id : null;
  if (first) await select(first);
  every(5000, refresh);
})().catch(e => toast(e.message, 'error'));
