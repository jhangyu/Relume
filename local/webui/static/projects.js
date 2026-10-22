// Followed projects page (spec 2026-10-23-follow-project-design §8; layout from mockup option A).
import { $, $$, act, api, ask, every, fmt, html, iconTile, shell, toast } from '/static/app.js';

const S = { projects: [], devices: [], items: new Map(), schedules: [], running: [], queued: [], ota: [], cur: null, filter: 'all', q: '' };
const byId = id => S.projects.find(p => p.id === id);
const dpOf = (d, pid) => (d.projects || []).find(x => x.project_id === pid);
const phonesOf = p => S.devices.filter(d => dpOf(d, p.id));
const assignedOf = p => S.devices.filter(d => (dpOf(d, p.id) || {}).assigned);
const behind = p => S.devices.some(d => (dpOf(d, p.id) || {}).update_available);
const attn = p => !!(p.signer_alert || p.check_error || behind(p));
const jobFor = (list, p) => list.some(j => j.target && j.target.upstream_id === p.upstream_id);
const initials = s => (s || '?').split(/[\s_\-·/]+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();
const hue = s => [...(s || '')].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const tile = (p, cls = '') => html`<span class="ico ${cls}" style="background:hsl(${hue(p.repo)} 55% 42%)" aria-hidden="true">${initials(p.label)}</span>`;
const sha = s => (s || '').toUpperCase().match(/../g)?.join(':') || '';
const pull = p => api('POST', `/api/upstreams/${p.upstream_id}/pull`, { rebuild: false });
const WARN = html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>`;

function chips(p) {
  const c = [];
  if (p.signer_alert) c.push(['b-err', 'Signer changed']);
  if (p.auto_push && p.signer_alert) c.push(['b-mute plain', 'Auto-push paused']);
  if (p.check_error) c.push(['b-err', 'Check error']);
  if (jobFor(S.running, p)) c.push(['b-info', 'Downloading']);
  if (jobFor(S.queued, p)) c.push(['b-mute', 'Queued']);
  if (behind(p)) c.push(['b-warn', 'Update available']);
  if (!p.device_ids.length && !p.item_ids.length) c.push(['b-mute plain', 'No phones']);
  if (!c.length && p.downloaded_tag && p.downloaded_tag === p.latest_tag) c.push(['b-ok', 'Up to date']);
  return c.map(([cls, t]) => html`<span class="badge ${cls}">${t}</span>`);
}

function phones(p) {
  const ds = phonesOf(p);
  if (!ds.length) return html`<span class="xs muted">none</span>`;
  const lab = ds.map(d => `${d.name}${dpOf(d, p.id).update_available ? ' (behind)' : ''}${d.online ? '' : ' (offline)'}`).join(', ');
  return html`<span class="ph" role="img" aria-label="${lab}">${ds.map(d => {
    const b = dpOf(d, p.id).update_available;
    return html`<i class="${b ? 'behind' : ''} ${d.online ? '' : 'off'}" title="${d.name}${b ? ': older version' : ''}${d.online ? '' : ' (offline)'}">${initials(d.name)}</i>`;
  })}</span>`;
}

function visible(p) {
  if (S.filter === 'attn' && !attn(p)) return false;
  if (S.filter === 'dep' && !p.item_ids.length) return false;
  return !S.q || `${p.label} ${p.repo}`.toLowerCase().includes(S.q);
}

function rows() {
  const list = S.projects.filter(visible);
  $('#rows').innerHTML = html`${list.length ? list.map(p => html`<tr data-id="${p.id}" aria-selected="${p.id === S.cur}"><td><div class="pname">${tile(p, 'ico-sm')}<div style="min-width:0"><button type="button" class="rowbtn">${p.label}</button><div class="repo mono">${p.repo}${p.channel === 'prerelease' ? html` · <span class="ch pre">pre-release</span>` : ''}${p.item_ids.length ? html` · <span class="ch">dependency</span>` : ''}</div></div></div></td>
<td><div class="tag">${p.latest_tag || '—'}<small>${p.latest_published_at ? fmt.abs(p.latest_published_at) : ''}</small></div></td>
<td class="c-dl"><div class="tag ${p.downloaded_tag !== p.latest_tag ? 'newer' : ''}">${p.downloaded_tag || 'none yet'}</div></td>
<td><div class="chiprow">${chips(p)}</div></td><td class="c-ph">${phones(p)}</td>
<td><label class="switch" data-stop><input type="checkbox" data-auto="${p.id}" ${p.auto_push ? html`checked` : ''} aria-label="Auto-push ${p.label} to assigned phones"><span></span></label></td></tr>`)
    : html`<tr><td colspan="6" class="empty" style="padding:1.5rem;text-align:center">${S.projects.length ? 'No projects match.' : 'No followed projects yet. Use Follow project.'}</td></tr>`}`;
  $('#cnt').textContent = S.projects.length;
  $('#attn').textContent = S.projects.filter(attn).length;
}

function summary() {
  const last = S.projects.map(p => p.checked_at).filter(Boolean).sort().pop();
  const sch = S.schedules.find(s => s.enabled && s.kind === 'upstream' && s.target && s.target.all);
  $('#lastCheck').innerHTML = html`Last check <b>${last ? fmt.abs(last) : 'never'}</b> · ${sch ? `next ${fmt.abs(sch.next_run_at)} via schedule "${sch.name}"` : 'no schedule checks projects'}`;
  $('#disk').textContent = fmt.bytes(S.projects.reduce((n, p) => n + (p.disk_bytes || 0), 0));
}

function otaRows(p) {
  const out = [];
  for (const j of S.ota) {
    const d = j.detail || {}, dev = (S.devices.find(x => x.id === j.target.device_id) || {}).name || `device ${j.target.device_id}`;
    const when = j.finished_at || j.created_at;
    if (d.outcome === 'skipped_offline' && (d.project_ids || j.target.project_ids || []).includes(p.id)) {
      out.push({ when, dev, trig: j.trigger, ver: '—', res: ['res-skip', 'Skipped'] });
      continue;
    }
    for (const i of d.installs || []) {
      if (i.kind !== 'project' || i.ref_id !== p.id) continue;
      const res = i.result === 'installed' ? ['res-ok', 'Installed'] : i.result === 'up_to_date' ? ['res-ok', 'Current']
        : i.error_code === 'SIGNER_CHANGED' ? ['res-skip', 'Blocked'] : ['res-fail', 'Failed'];
      out.push({ when, dev, trig: j.trigger, ver: i.upstream_tag || '—', res, err: i.result === 'failed' ? i.error_code : '' });
    }
  }
  return out;
}

function pane() {
  const p = byId(S.cur), el = $('#pane');
  if (!p) { el.innerHTML = html`<div class="pane-s"><p class="empty">Select a project.</p></div>`; return; }
  const asg = assignedOf(p), blocked = !!p.signer_alert;
  const behindAsg = asg.filter(d => dpOf(d, p.id).update_available);
  const ota = otaRows(p);
  const usedBy = p.item_ids.map(id => S.items.get(id) || { id, label: `item ${id}` });
  el.innerHTML = html`<div class="pane-h"><div class="row between" style="flex-wrap:nowrap;align-items:flex-start"><div class="row" style="flex-wrap:nowrap;gap:.75rem;min-width:0">${tile(p)}<div style="min-width:0"><h2>${p.label}</h2><span class="xs muted mono">github.com/${p.repo}</span></div></div>
<button class="btn btn-ghost btn-icon btn-sm pane-close" type="button" data-pane-close aria-label="Close details"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12"/></svg></button></div>
<div class="chiprow">${chips(p)}<span class="badge b-mute plain">${p.channel === 'prerelease' ? 'Pre-release' : 'Stable'} channel</span></div>
<div class="row"><details class="dd"><summary class="btn btn-accent btn-sm"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/></svg>Install to phone</summary><div class="menu" style="left:0;right:auto;min-width:270px">${blocked ? html`<p class="xs danger" style="padding:.5rem">Blocked: confirm the new signer first.</p>`
    : asg.length ? asg.map(d => { const x = dpOf(d, p.id); return html`<button type="button" data-install="${d.id}" ${d.online ? '' : html`disabled style="opacity:.5"`}><span class="dot ${d.online ? 'on' : 'off'}"></span><span class="grow">${d.name}<br><span class="xs muted mono">${x.available_abi || '—'} → ${x.available_file || 'no match'}</span></span>${d.online ? '' : html`<span class="xs muted">offline</span>`}</button>`; })
      : html`<p class="xs muted" style="padding:.5rem">No phones assigned yet. Use ⋯ → Assign phones.</p>`}</div></details>
<button class="btn btn-outline btn-sm" type="button" data-check>Check now</button>
<details class="dd" style="margin-left:auto"><summary class="btn btn-ghost btn-sm btn-icon" aria-label="More actions"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg></summary><div class="menu"><button type="button" data-edit>Edit assets, regex &amp; channel</button><button type="button" data-assign>Assign phones</button><hr>${p.item_ids.length ? html`<button type="button" disabled style="opacity:.55" class="danger">Unfollow · used by ${p.item_ids.length} items</button>` : html`<button type="button" class="danger" data-unfollow>Unfollow</button>`}</div></details></div></div>
${p.check_error ? html`<div class="pane-s"><div class="alert err" role="alert"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/></svg><span><b>Last check failed.</b> ${p.check_error}</span></div><div class="field"><label class="lbl" style="font-size:.75rem" for="rxEdit">Asset regex</label><div class="row" style="flex-wrap:nowrap"><input id="rxEdit" class="input rx" data-rx-edit value="${p.asset_regex}" spellcheck="false"><button class="btn btn-outline btn-sm" type="button" data-rx-save>Save &amp; check</button></div></div></div>` : ''}
${behindAsg.length && !p.auto_push ? html`<div class="pane-s"><div class="alert warn">${WARN}<span>${behindAsg.map(d => `${d.name} is still on ${dpOf(d, p.id).installed_tag}`).join('; ')}. Auto-push is off; it updates on the next scheduled OTA, or use Install to phone.</span></div></div>` : ''}
<div class="pane-s"><div class="row between"><span class="lbl-s">APK signer · SHA-256</span>${blocked ? html`<span class="badge b-err">Changed</span>` : p.signer_sha256 ? html`<span class="badge b-ok">Trusted since ${p.signer_since}</span>` : ''}</div>
${blocked ? html`<div class="alert err" role="alert">${WARN}<span><b>${p.downloaded_tag} is signed with a different certificate.</b> It is downloaded but quarantined: auto-push, scheduled OTA and Install are blocked until you confirm.</span></div><div class="xs muted">Trusted (first seen ${p.signer_since})</div><div class="sha">${sha(p.signer_sha256)}</div><div class="xs muted">New in ${p.downloaded_tag}</div><div class="sha new">${sha(p.signer_alert)}</div><div class="row"><button class="btn btn-sm btn-outline danger" type="button" data-sig>Review &amp; confirm signer…</button></div>`
    : p.signer_sha256 ? html`<div class="sha">${sha(p.signer_sha256)}</div><p class="xs muted">Recorded on first download (trust on first use). If a later version has a different signer, auto-push stops until you confirm.</p>`
      : html`<p class="xs muted">Recorded after the first download (trust on first use). If a later version has a different signer, auto-push stops until you confirm.</p>`}</div>
<div class="pane-s"><span class="lbl-s">Kept versions (${p.versions.length} of 2)</span><p class="xs muted mono" style="word-break:break-all" title="Asset regex">regex /${p.asset_regex}/</p>
${p.versions.length ? p.versions.map(v => html`<div class="rel"><div class="rel-h"><span><span class="tag">${v.tag}</span>${v.tag === p.latest_tag && p.latest_published_at ? html` <span class="xs muted">· ${fmt.abs(p.latest_published_at)}</span>` : ''}</span>${v.current && blocked ? html`<span class="badge b-err">Quarantined</span>` : v.current ? html`<span class="badge b-ok">Current</span>` : html`<span class="badge b-mute plain">Previous</span>`}</div><ul class="alist2">${v.files.map(f => html`<li><span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${f.name}">${f.name}</span><span class="abi ${f.abi === 'universal' ? 'u' : ''}">${f.abi || '?'}</span><span class="muted">${fmt.bytes(f.size)}</span></li>`)}</ul></div>`) : html`<p class="empty">Nothing downloaded yet.</p>`}</div>
<div class="pane-s"><span class="lbl-s">Assigned phones</span>${asg.length ? html`<ul class="deps">${asg.map(d => { const x = dpOf(d, p.id); return html`<li><span class="dot ${d.online ? 'on' : 'off'}" aria-hidden="true"></span><span class="grow">${d.name} <span class="xs muted mono">${x.available_abi || '—'} → ${x.available_file || 'no match'}</span></span>${x.update_available ? html`<span class="badge b-warn">behind</span>` : !x.installed_tag ? html`<span class="xs muted">not installed</span>` : x.installed_tag === x.available_tag ? html`<span class="xs muted">current</span>` : html`<span class="xs muted mono">${x.installed_tag}</span>`}</li>`; })}</ul>` : html`<p class="empty">Not assigned to any phone. New versions still download; nothing installs.</p>`}</div>
<div class="pane-s"><span class="lbl-s">Used as dependency by</span>${usedBy.length ? html`<ul class="deps">${usedBy.map(it => html`<li>${iconTile(it, 'ico-sm')}<a href="/item.html?id=${it.id}">${it.label}</a>${it.source_id ? html` <span class="src">${it.source_id}</span>` : ''}</li>`)}</ul><p class="xs muted">Installed or upgraded before these items on every OTA run. Unfollow is disabled while items use it.</p>` : html`<p class="empty">No item uses it. Link it from an item's Dependencies setting.</p>`}</div>
<div class="pane-s"><span class="lbl-s">OTA history</span>${ota.length ? html`<table class="otbl"><tbody>${ota.map(o => html`<tr><td class="muted" style="white-space:nowrap">${fmt.abs(o.when)}</td><td>${o.dev}<div class="xs muted">${o.trig}${o.err ? ` · ${o.err}` : ''}</div></td><td class="mono">${o.ver}</td><td class="${o.res[0]}" style="white-space:nowrap">${o.res[1]}</td></tr>`)}</tbody></table><p class="xs muted">Covers the last 100 OTA jobs.</p>` : html`<p class="empty">No installs yet.</p>`}</div>`;
}

function render() { rows(); summary(); pane(); }

async function load() {
  const [pr, dv, it, sc, run, qu, ota] = await Promise.all([
    api('GET', '/api/projects'), api('GET', '/api/devices'), api('GET', '/api/items'), api('GET', '/api/schedules'),
    api('GET', '/api/jobs?kind=upstream&status=running'), api('GET', '/api/jobs?kind=upstream&status=queued'),
    api('GET', '/api/jobs?kind=ota&limit=100'),
  ]);
  S.projects = pr.projects;
  S.devices = dv.devices;
  S.items = new Map(it.items.map(x => [x.id, x]));
  S.schedules = sc.schedules;
  S.running = run.jobs;
  S.queued = qu.jobs;
  S.ota = ota.jobs;
  if (!byId(S.cur)) S.cur = S.projects.length ? S.projects[0].id : null;
  render();
}
const reload = () => load().catch(e => toast(e.message, 'error'));

function select(id, open) {
  S.cur = id;
  rows();
  pane();
  if (open && matchMedia('(max-width:1279px)').matches) { document.body.classList.add('pane-open'); $('#pane').focus(); }
}
function closePane() {
  document.body.classList.remove('pane-open');
  const r = $('#rows tr[aria-selected=true] .rowbtn');
  if (r) r.focus();
}

$('#rows').addEventListener('click', e => {
  if (e.target.closest('[data-stop]')) return;
  const tr = e.target.closest('tr[data-id]');
  if (tr) select(Number(tr.dataset.id), true);
});
$('#rows').addEventListener('change', async e => {
  const id = Number(e.target.dataset.auto);
  if (!id) return;
  const p = byId(id), on = e.target.checked;
  try {
    await act('PUT', `/api/projects/${id}`, { auto_push: on }, `Auto-push ${on ? 'on' : 'off'} for ${p.label}${p.signer_alert && on ? ' (still paused until the signer is confirmed)' : ''}`);
  } catch (_) { /* act showed it */ }
  reload();
});
$('#rows').addEventListener('keydown', e => {
  const d = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0;
  if (!d || !e.target.closest('.rowbtn')) return;
  e.preventDefault();
  const trs = $$('#rows tr[data-id]'), i = trs.findIndex(t => Number(t.dataset.id) === S.cur);
  const n = trs[Math.max(0, Math.min(trs.length - 1, i + d))];
  if (n) { select(Number(n.dataset.id), false); $('#rows tr[aria-selected=true] .rowbtn').focus(); }
});
$('#drawerBg').addEventListener('click', closePane);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && document.body.classList.contains('pane-open') && !document.querySelector('dialog[open]')) closePane();
});
$('#q').addEventListener('input', e => { S.q = e.target.value.trim().toLowerCase(); rows(); });
$$('input[name=f]').forEach(r => r.addEventListener('change', () => { S.filter = r.value; rows(); }));

$('#checkAll').addEventListener('click', async () => {
  const r = await Promise.allSettled(S.projects.map(pull));
  const bad = r.filter(x => x.status === 'rejected');
  toast(bad.length ? `${bad[0].reason.message} (${bad.length} of ${r.length} failed)` : `Checking ${r.length} projects`, bad.length ? 'error' : 'ok');
  reload();
});

$('#pane').addEventListener('click', async e => {
  const t = s => e.target.closest(s), p = byId(S.cur);
  if (!p) return;
  try {
    if (t('[data-pane-close]')) return closePane();
    if (t('[data-sig]')) return signerDialog(p);
    if (t('[data-edit]')) return addDialog(p);
    if (t('[data-assign]')) return assignDialog(p);
    if (t('[data-install]')) {
      const d = S.devices.find(x => x.id === Number(t('[data-install]').dataset.install));
      t('details.dd').open = false;
      await act('POST', `/api/projects/${p.id}/install`, { device_ids: [d.id] }, `Installing ${p.label} ${p.downloaded_tag || ''} on ${d.name}`);
    } else if (t('[data-check]')) {
      await act('POST', `/api/upstreams/${p.upstream_id}/pull`, { rebuild: false }, `Checking ${p.repo} for a new release…`);
    } else if (t('[data-rx-save]')) {
      await act('PUT', `/api/projects/${p.id}`, { asset_regex: $('[data-rx-edit]', $('#pane')).value }, `Regex saved; re-checking ${p.label}…`);
    } else if (t('[data-unfollow]')) {
      if (!confirm(`Unfollow ${p.label}? Its downloaded APKs are deleted.`)) return;
      await act('DELETE', `/api/projects/${p.id}`, {}, `Unfollowed ${p.label}`);
      S.cur = null;
    } else return;
    reload();
  } catch (_) { /* act showed it */ }
});

// ---- signer dialog (row 51)
function signerDialog(p) {
  $('#sigName').textContent = p.label;
  $('#sigVer').textContent = p.downloaded_tag || '';
  $('#sigOld').textContent = sha(p.signer_sha256);
  $('#sigNew').textContent = sha(p.signer_alert);
  $('#sigAck').checked = false;
  $('#sigOk').disabled = true;
  $('#sigOk').onclick = async () => {
    try {
      await act('POST', `/api/projects/${p.id}/signer/accept`, { signer_sha256: p.signer_alert }, `New signer trusted for ${p.label}`);
      $('#sigDlg').close();
    } catch (_) { /* act showed it */ }
    reload();
  };
  $('#sigDlg').showModal();
}
$('#sigAck').addEventListener('change', e => { $('#sigOk').disabled = !e.target.checked; });

// ---- assign phones dialog (row 26)
const phoneList = (sel, checked) => {
  $(sel).innerHTML = html`${S.devices.length ? S.devices.map(d => html`<label><input type="checkbox" class="cb" value="${d.id}" ${checked.includes(d.id) ? html`checked` : ''}><span>${d.name} <span class="xs muted">· ${d.online ? 'online' : 'offline'}</span></span><span></span></label>`)
    : html`<p class="xs muted">No paired phones. Pair one on the Devices page.</p>`}`;
};
async function assignDialog(p) {
  phoneList('#asgList', p.device_ids);
  if (await ask($('#asgDlg')) !== 'ok') return;
  try {
    await act('PUT', `/api/projects/${p.id}`, { device_ids: $$('#asgList input:checked').map(i => Number(i.value)) }, 'Phones saved');
  } catch (_) { /* act showed it */ }
  reload();
}

// ---- add / edit dialog (rows 38-50)
const A = { repo: null, edit: null, assets: [], dirty: false, seq: 0, timer: 0 };
const msg = (text, bad) => { $('#dlgMsg').className = `xs ${bad ? 'danger' : 'muted'}`; $('#dlgMsg').textContent = text; };
const chan = () => $('input[name=chan]:checked').value;

function setStep(n) {
  $$('#steps li').forEach((li, i) => {
    li.className = i + 1 < n ? 'done' : i + 1 === n ? 'now' : '';
    if (i + 1 === n) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
  });
}

function parseRepo(v) {
  const m = v.trim().match(/^(?:https?:\/\/(?:www\.)?github\.com\/)?([\w.-]+)\/([\w.-]+?)(?:\.git)?\/?$/);
  return m ? `${m[1]}/${m[2]}` : null;
}

function markAssets() {
  const m = A.assets.filter(a => a.matched).length, rx = $('#rx').value;
  $$('#assets tr[data-i]').forEach(tr => { tr.className = A.assets[Number(tr.dataset.i)].matched ? 'match' : 'nomatch'; });
  $('#rx').setAttribute('aria-invalid', 'false');
  $('#rxh').className = `hint ${m ? '' : 'danger'}`;
  $('#rxh').textContent = m ? `Matches ${m} of ${A.assets.length} assets` : rx ? 'Matches no assets. Tick assets or fix the regex.' : 'Tick the assets to follow.';
  $('#saveBtn').disabled = !m;
  setStep(m ? 3 : 2);
}

// Every match decision comes from the server (Python re); JavaScript never evaluates the regex.
async function assets(extra) {
  const n = ++A.seq;
  try {
    const r = await api('POST', '/api/projects/assets', { repo: A.repo, channel: chan(), ...extra });
    if (n !== A.seq) return null;
    return r;
  } catch (e) {
    if (n !== A.seq) return null;
    if (e.message === 'invalid asset_regex') {
      $('#rx').setAttribute('aria-invalid', 'true');
      $('#rxh').className = 'hint danger';
      $('#rxh').textContent = 'Invalid regex';
      $('#saveBtn').disabled = true;
    } else msg(e.message, true);
    return null;
  }
}

function showRelease(r, ticks) {
  A.assets = r.assets;
  $('#relTag').textContent = r.tag;
  $('#relChan').textContent = chan() === 'prerelease' ? 'pre-release' : 'stable';
  $('#relMeta').textContent = `Published ${fmt.abs(r.published_at)} · ${r.assets.length} APK assets (non-APK files hidden)`;
  $('#assets').innerHTML = html`${r.assets.map((a, i) => html`<tr data-i="${i}"><td><input type="checkbox" class="cb" id="as${i}" ${ticks(a) ? html`checked` : ''}></td><td><label for="as${i}" class="mono xs">${a.name}</label></td><td class="xs muted c-size" style="text-align:right">${fmt.bytes(a.size)}</td></tr>`)}`;
  if (r.asset_regex != null) $('#rx').value = r.asset_regex;
  $('#s2').hidden = $('#s3').hidden = false;
  markAssets();
}

async function fetchRelease() {
  const repo = parseRepo($('#url').value);
  if (!repo) { msg('Enter a link like https://github.com/owner/repo', true); $('#url').focus(); return; }
  A.repo = repo;
  msg(`Fetching ${repo}…`);
  const keep = A.edit || A.dirty ? $('#rx').value : '';
  const r = await assets(keep ? { asset_regex: keep } : {});
  if (!r) return;
  msg(`Fetched ${repo}`);
  if (!keep) $('#rx').value = '';
  showRelease(r, a => keep && a.matched);
}

async function derive() {
  const selected = $$('#assets tr[data-i]').filter(tr => $('input', tr).checked).map(tr => A.assets[Number(tr.dataset.i)].name);
  if (!selected.length) {
    A.seq++;
    $('#rx').value = '';
    A.assets.forEach(a => { a.matched = false; });
    return markAssets();
  }
  const r = await assets({ selected });
  if (!r) return;
  A.assets = r.assets;
  $('#rx').value = r.asset_regex || '';
  markAssets();
}

async function retest() {
  const rx = $('#rx').value;
  if (!rx) { A.assets.forEach(a => { a.matched = false; }); return markAssets(); }
  const r = await assets({ asset_regex: rx });
  if (!r) return;
  A.assets = r.assets;
  markAssets();
}

function addDialog(p) {
  A.edit = p || null;
  A.repo = p ? p.repo : null;
  A.assets = [];
  A.dirty = false;
  A.seq++;
  $('#addT').textContent = p ? `Edit ${p.label}` : 'Follow a GitHub project';
  $('#saveBtn').textContent = p ? 'Save' : 'Follow project';
  $('#url').value = p ? `https://github.com/${p.repo}` : '';
  $('#url').disabled = $('#fetchBtn').hidden = !!p;
  $('#rx').value = p ? p.asset_regex : '';
  $(`input[name=chan][value=${p ? p.channel : 'stable'}]`).checked = true;
  $('#newName').value = p ? p.display_name || '' : '';
  $('#newAuto').checked = false;
  $('#autoField').hidden = $('#phField').hidden = !!p;
  phoneList('#phl', []);
  $('#s2').hidden = $('#s3').hidden = true;
  $('#saveBtn').disabled = true;
  msg('');
  setStep(1);
  $('#addDlg').showModal();
  if (p) fetchRelease(); else $('#url').focus();
}

$('#addBtn').addEventListener('click', () => addDialog(null));
$('#fetchBtn').addEventListener('click', fetchRelease);
$('#url').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); fetchRelease(); } });
$('#assets').addEventListener('change', () => {
  if (A.dirty && !confirm('Replace your edited regex with one derived from the ticked assets?')) return;
  A.dirty = false;
  derive();
});
$('#rx').addEventListener('input', () => {
  A.dirty = true;
  clearTimeout(A.timer);
  A.timer = setTimeout(retest, 300);
});
$$('input[name=chan]').forEach(r => r.addEventListener('change', async () => {
  if (!A.repo) return;
  const rx = $('#rx').value;
  const r = await assets(rx ? { asset_regex: rx } : {});
  if (r) showRelease(r, a => a.matched);
}));

$('#saveBtn').addEventListener('click', async () => {
  const body = { asset_regex: $('#rx').value, channel: chan(), display_name: $('#newName').value.trim() || null };
  $('#saveBtn').disabled = true;
  try {
    if (A.edit) {
      await api('PUT', `/api/projects/${A.edit.id}`, body);
      toast(`Saved ${A.edit.label}`);
    } else {
      const r = await api('POST', '/api/projects', { ...body, repo: A.repo, auto_push: $('#newAuto').checked, device_ids: $$('#phl input:checked').map(i => Number(i.value)) });
      S.cur = r.id;
      toast(`Following ${A.repo} · downloading the latest release`);
    }
    $('#addDlg').close();
    await reload();
    if (!A.edit) select(S.cur, true);
  } catch (e) {
    msg(e.message, true);
    $('#saveBtn').disabled = false;
  }
});

(async () => {
  shell();
  await load();
  every(15000, () => {
    if (document.querySelector('dialog[open], details.dd[open]') || document.activeElement.closest('[data-rx-edit]')) return;
    reload();
  });
})().catch(e => toast(e.message, 'error'));
