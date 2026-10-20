import { $, $$, act, api, every, fmt, html, iconTile, logStream, shell, toast } from '/static/app.js';

const qs = new URLSearchParams(location.search);
const ITEM = qs.get('item') ? Number(qs.get('item')) : null;
const STEPS = ['version', 'download', 'patch', 'verify', 'store'];
const STEP_RE = /^(\d\d:\d\d:\d\d) ::step::(\w+)(?:::(.*))?$/;
const B = { items: new Map(), schedules: new Map(), sel: null, es: null, text: '', marks: [], last: null };

const triggerText = j => (j.trigger === 'schedule' ? `schedule "${B.schedules.get(j.schedule_id) || j.schedule_id}"`
  : j.trigger === 'upstream' ? 'patch update' : j.trigger === 'after_build' ? 'after build' : 'manual');
const itemOf = j => B.items.get(j.target.item_id) || { label: `item ${j.target.item_id}`, source_name: '', abis: [] };
const BADGE = { success: ['b-ok', 'Success'], failed: ['b-err', 'Failed'], running: ['b-info', 'Running'], queued: ['b-mute', 'Waiting'], cancelled: ['b-mute', 'Cancelled'] };

async function loadRefs() {
  const [cat, sch] = await Promise.all([api('GET', '/api/catalog'), api('GET', '/api/schedules')]);
  [...cat.groups.flatMap(g => g.items), ...cat.ungrouped].forEach(it => B.items.set(it.id, it));
  sch.schedules.forEach(s => B.schedules.set(s.id, s.name));
}

async function renderQueue() {
  const [r, q] = await Promise.all([api('GET', '/api/jobs?kind=build&status=running'), api('GET', '/api/jobs?kind=build&status=queued&limit=100')]);
  const rows = [...r.jobs, ...q.jobs.reverse()];
  $('#qsum').textContent = `${r.jobs.length} running · ${q.jobs.length} waiting · 1 worker`;
  $('#queue tbody').innerHTML = html`${rows.map(j => {
    const it = itemOf(j), [cls, label] = BADGE[j.status];
    return html`<tr><td class="mono" style="width:4rem"><a href="?job=${j.id}" data-sel="${j.id}">#${j.id}</a></td><td><div class="row" style="flex-wrap:nowrap">${iconTile(it, 'ico-sm')}<b style="font-weight:500">${it.label}</b><span class="src">${it.source_name}</span></div></td><td class="hide-m sm muted">${triggerText(j)}</td><td><span class="badge ${cls}">${label}</span></td><td style="text-align:right"><button class="btn btn-ghost btn-sm" type="button" data-cancel="${j.id}">${j.status === 'running' ? 'Cancel' : 'Remove'}</button></td></tr>`;
  })}`;
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
    d.append(s, document.createTextNode(t.slice(9)));
    log.appendChild(d);
    const m = STEP_RE.exec(t);
    if (m) B.marks.push({ at: m[1], step: m[2], detail: m[3] || '' });
  }
  renderSteps();
  $('#lines').textContent = `${log.children.length} lines`;
  if ($('#follow').checked) log.scrollTop = log.scrollHeight;
  find();
}

const secs = t => { const [h, m, s] = t.split(':').map(Number); return h * 3600 + m * 60 + s; };
function renderSteps(final) {
  const status = final || (B.last && B.last.status);
  const cur = B.marks.length ? STEPS.indexOf(B.marks[B.marks.length - 1].step) : -1;
  $$('#steps li').forEach((li, i) => {
    const mark = B.marks.find(m => m.step === STEPS[i]);
    const next = B.marks.find(m => STEPS.indexOf(m.step) === i + 1);
    li.className = status === 'success' || i < cur ? 'done' : i === cur ? (status === 'failed' ? 'fail' : 'now') : '';
    let small = '—';
    if (mark && next) { const d = Math.max(0, secs(next.at) - secs(mark.at)); small = `${Math.floor(d / 60)}:${String(d % 60).padStart(2, '0')}`; }
    if (STEPS[i] === 'patch' && mark && mark.detail && i === cur) small = mark.detail;
    li.querySelector('small').textContent = small;
  });
  const pct = status === 'success' ? 100 : Math.max(0, Math.round(((cur + 0.5) / STEPS.length) * 100));
  $('#pbar').style.width = `${pct}%`;
  $('#pbar').parentNode.setAttribute('aria-valuenow', pct);
}

function find() {
  const q = $('#find').value.trim().toLowerCase();
  let n = 0;
  $$('#log > div').forEach(d => { const h = !!q && d.textContent.toLowerCase().includes(q); d.classList.toggle('hit', h); if (h) n++; });
  $('#hits').textContent = q ? `${n} match${n === 1 ? '' : 'es'}` : '';
}

async function select(id) {
  if (B.es) B.es.close();
  B.text = '';
  B.marks = [];
  $('#log').textContent = '';
  const j = await api('GET', `/api/jobs/${id}`);
  B.sel = id;
  B.last = j;
  const it = itemOf(j), d = j.detail || {}, [cls, label] = BADGE[j.status];
  const tpl = document.createElement('template');
  tpl.innerHTML = html`${iconTile(it)}`;
  tpl.content.firstChild.id = 'bico';
  $('#bico').replaceWith(tpl.content.firstChild);
  $('#bt').textContent = `Build #${j.id} · ${it.label}`;
  $('#bsrc').textContent = it.source_name;
  $('#bst').className = `badge ${cls}`;
  $('#bst').textContent = label;
  const live = j.status === 'running' || j.status === 'queued';
  $('#bsub').textContent = `Trigger: ${triggerText(j)} · started ${fmt.abs(j.started_at)} · ${fmt.duration(j.started_at, j.finished_at)} ${live ? 'elapsed' : 'took'}`;
  $('#cancel').disabled = !live;
  let full = null;
  try { full = await api('GET', `/api/items/${j.target.item_id}`); } catch (_) { /* item deleted */ }
  $('#facts').innerHTML = html`<div><dt class="muted">Patches</dt><dd class="mono">${d.patches_version || '—'}</dd></div><div><dt class="muted">App</dt><dd class="mono">${d.app_version || '—'}</dd></div><div><dt class="muted">Arch</dt><dd class="mono">${(it.abis || []).join(', ') || '—'}</dd></div><div><dt class="muted">CLI</dt><dd class="mono">${d.cli_version || '—'}</dd></div><div><dt class="muted">APK source</dt><dd class="mono">${full && full.variants.length && full.variants[0].download ? full.variants[0].download.kind : '—'}</dd></div>`;
  renderSteps();
  B.es = logStream(id, appendLines, status => {
    B.last = { ...B.last, status };
    const [c2, l2] = BADGE[status] || ['b-mute', status];
    $('#bst').className = `badge ${c2}`;
    $('#bst').textContent = l2;
    $('#cancel').disabled = true;
    renderSteps(status);
    refresh();
  });
}

let oldest = null;
function fixFor(j) {
  const code = j.detail && j.detail.fail_code, it = itemOf(j);
  if (code === 'version_unresolved' || code === 'patch_failed') return html`<a class="xs" href="/item.html?id=${j.target.item_id}#version">Pin a version</a>`;
  if (code === 'upstream_missing' && it.upstream) return html`<button class="btn btn-ghost btn-sm" type="button" data-pull="${it.upstream.id}">Update upstream</button>`;
  return html`<button class="btn btn-ghost btn-sm" type="button" data-retry="${j.target.item_id}">Retry</button>`;
}

async function renderHistory(append) {
  const f = $('#filt').value;
  const p = new URLSearchParams({ kind: 'build', limit: '25' });
  if (f === 'failed' || f === 'success') p.set('status', f);
  if (f === 'item' && ITEM) p.set('item_id', ITEM);
  if (append && oldest) p.set('before_id', oldest);
  const { jobs } = await api('GET', `/api/jobs?${p}`);
  const done = jobs.filter(j => j.status !== 'queued' && j.status !== 'running');
  if (jobs.length) oldest = jobs[jobs.length - 1].id;
  const rows = html`${done.map(j => {
    const it = itemOf(j), [cls, label] = BADGE[j.status], size = ((j.detail && j.detail.artifacts) || []).reduce((n, a) => n + a.size, 0);
    return html`<tr data-id="${j.id}" ${j.id === B.sel ? html`aria-current="true"` : ''}><td class="mono">#${j.id}</td><td><div class="row" style="flex-wrap:nowrap">${iconTile(it, 'ico-sm')}<span>${it.label}</span></div></td><td class="hide-m sm muted">${triggerText(j)}</td><td><span class="badge ${cls}">${label}</span>${j.status === 'failed' ? html`<div class="xs danger">${j.reason || ''}</div>${fixFor(j)}` : ''}</td><td class="hide-m">${fmt.abs(j.started_at)}</td><td class="hide-m">${fmt.duration(j.started_at, j.finished_at)}</td><td class="hide-m">${size ? fmt.bytes(size) : '—'}</td><td><button class="btn btn-ghost btn-sm" type="button" data-sel="${j.id}">View log</button></td></tr>`;
  })}`;
  if (append) $('#hist tbody').insertAdjacentHTML('beforeend', html`${rows}`);
  else $('#hist tbody').innerHTML = html`${rows}`;
  $('#older').hidden = jobs.length < 25;
  return done[0];
}

async function refresh() {
  try { await renderQueue(); await renderHistory(false); } catch (_) { /* next tick retries */ }
}

document.addEventListener('click', async e => {
  const sel = e.target.closest('[data-sel]'), can = e.target.closest('[data-cancel]');
  const pull = e.target.closest('[data-pull]'), retry = e.target.closest('[data-retry]');
  try {
    if (sel) {
      e.preventDefault();
      history.replaceState(null, '', `?job=${sel.dataset.sel}${ITEM ? `&item=${ITEM}` : ''}`);
      await select(Number(sel.dataset.sel));
      $('[aria-labelledby=bt]').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    if (can) await act('POST', `/api/jobs/${can.dataset.cancel}/cancel`, {}, 'Cancel requested');
    if (pull) await act('POST', `/api/upstreams/${pull.dataset.pull}/pull`, { rebuild: true }, 'Update queued');
    if (retry) await act('POST', `/api/items/${retry.dataset.retry}/build`, {}, 'Build queued');
    if (can || pull || retry) await refresh();
  } catch (_) { /* act() showed it */ }
});
$('#cancel').addEventListener('click', () => act('POST', `/api/jobs/${B.sel}/cancel`, {}, 'Cancel requested').then(refresh).catch(() => {}));
$('#log').addEventListener('scroll', () => { const l = $('#log'); if (l.scrollTop + l.clientHeight < l.scrollHeight - 24) $('#follow').checked = false; });
$('#wrap').addEventListener('change', e => $('#log').classList.toggle('nowrap', !e.target.checked));
$('#find').addEventListener('input', find);
$('#copy').addEventListener('click', () => navigator.clipboard.writeText(B.text).then(() => toast('Log copied')));
$('#dlog').addEventListener('click', () => {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([B.text], { type: 'text/plain' }));
  a.download = `build-${B.sel}.log`;
  a.click();
  URL.revokeObjectURL(a.href);
});
$('#filt').addEventListener('change', () => renderHistory(false).catch(() => {}));
$('#older').addEventListener('click', () => renderHistory(true).catch(() => {}));

(async () => {
  shell();
  if (!ITEM) $('#filt option[value=item]').remove();
  else $('#filt').value = 'item';
  await loadRefs();
  const running = await renderQueue();
  const newest = await renderHistory(false);
  const first = qs.get('job') ? Number(qs.get('job')) : running ? running.id : newest ? newest.id : null;
  if (first) await select(first);
  every(5000, refresh);
})().catch(e => toast(e.message, 'error'));
