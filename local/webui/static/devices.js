import { $, $$, act, api, ask, catalogItems, every, fmt, html, shell, toast } from '/static/app.js';

const D = { devices: [], items: new Map(), projects: [], last: new Map() };
const label = id => (D.items.get(id) || {}).label || `item ${id}`;
const otpValue = g => $$('input', g).map(i => i.value).join('');

function wireOtp(g) {
  const boxes = $$('input', g);
  boxes.forEach((b, i) => {
    b.addEventListener('input', () => { b.value = b.value.replace(/\D/g, '').slice(0, 1); if (b.value && boxes[i + 1]) boxes[i + 1].focus(); });
    b.addEventListener('keydown', e => { if (e.key === 'Backspace' && !b.value && boxes[i - 1]) boxes[i - 1].focus(); });
    b.addEventListener('paste', e => {
      const d = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 6);
      if (d.length !== 6) return;
      e.preventDefault();
      boxes.forEach((x, k) => { x.value = d[k]; });
      boxes[5].focus();
    });
  });
}

function card(d) {
  const pending = d.assignments.filter(a => a.pending).length + d.projects.filter(x => x.pending).length;
  const last = D.last.get(d.id);
  return html`<section class="card" data-dev="${d.id}"><div class="card-h"><div class="row between"><div class="row"><span class="dot ${d.online ? 'on' : 'off'}" aria-hidden="true"></span><h2><span contenteditable="true" spellcheck="false" data-rename role="textbox" aria-label="Device name" title="Click to rename">${d.name}</span></h2><span class="xs muted">${d.model} · Android ${d.android_release}</span></div>
<label class="row sm"><span class="switch"><input type="checkbox" data-oab ${d.ota_after_build ? html`checked` : ''}><span></span></span>Install after build</label></div>
<p class="xs muted mono">${d.host}:${d.connect_port} · ${(d.abi_list || '').split(',')[0]} · last seen ${fmt.rel(d.last_seen_at)}</p>${d.online ? '' : html`<div class="alert warn"><span>Turn on Wireless debugging on the phone, then Reconnect.</span></div>`}</div>
<div class="card-c stack-sm"><ul class="alist">${d.assignments.map(a => html`<li><span>${a.pending ? '' : '✓'}</span><span>${label(a.item_id)} <span class="xs muted mono">${a.installed ? a.installed.version_name : 'not installed'}${a.available ? ` → ${a.available.version_name}` : ''}</span></span><span class="xs ${a.pending ? 'newer' : 'muted'}">${a.pending ? 'pending' : 'current'}</span></li>`)}</ul>
${d.projects.length ? html`<ul class="alist">${d.projects.map(x => html`<li><span>${x.pending ? '' : '✓'}</span><span>${x.display_name} <span class="xs muted mono">${x.installed_tag || 'not installed'} → ${x.available_tag || '—'}</span><span class="xs muted"> · ${x.assigned ? 'assigned' : html`required by ${x.required_by.map(label).join(', ')} · installs first`}</span>${x.signer_alert ? html` <a class="badge b-err" href="/projects.html">Blocked: signer changed</a>` : ''}</span><span class="xs ${x.pending ? 'newer' : 'muted'}">${x.pending ? 'pending' : 'current'}</span></li>`)}</ul>` : ''}
<p class="xs muted">Last OTA: ${last ? html`${last.detail ? last.detail.outcome || last.status : last.status} · ${fmt.rel(last.finished_at || last.created_at)}` : 'none'}</p></div>
<div class="card-f"><button class="btn" type="button" data-ota ${d.online ? '' : html`disabled title="Device offline: turn on Wireless debugging, then Reconnect"`}>OTA now · ${pending} pending</button><button class="btn btn-outline" type="button" data-reconnect>Reconnect</button><button class="btn btn-ghost" type="button" data-assign>Edit apps…</button><button class="btn btn-ghost danger" type="button" data-unpair style="margin-left:auto">Unpair</button></div></section>`;
}

async function loadDevices() {
  const { devices } = await api('GET', '/api/devices');
  D.devices = devices;
  await Promise.all(devices.map(async d => {
    const { jobs } = await api('GET', `/api/jobs?kind=ota&device_id=${d.id}&limit=1`);
    D.last.set(d.id, jobs[0]);
  }));
  $('#devs').innerHTML = html`${devices.length ? devices.map(card) : html`<p class="sm muted">No paired devices yet.</p>`}`;
  const sel = $('#of').value;
  $('#of').innerHTML = html`<option value="">All devices</option>${devices.map(d => html`<option value="${d.id}">${d.name}</option>`)}`;
  $('#of').value = sel;
}

async function loadDiscovered() {
  const r = await api('GET', '/api/devices/discovered');
  const rows = [...r.pairing.map(p => ({ ...p, mode: 'pair' })), ...r.connect.map(c => ({ ...c, mode: 'conn' }))];
  $('#scan').textContent = `Scanned ${new Date().toLocaleTimeString()}`;
  $('#disc').innerHTML = html`${rows.length ? rows.map(x => html`<li><span class="dot on" aria-hidden="true"></span><span>${x.name} <span class="xs muted mono">${x.host}:${x.port}</span> <span class="badge b-mute plain">${x.mode === 'pair' ? 'Pairing mode' : x.device_id ? `Known: ${(D.devices.find(d => d.id === x.device_id) || {}).name || x.device_id}` : 'Paired'}</span></span>${x.mode === 'pair' ? html`<button class="btn btn-sm" type="button" data-pair="${x.host}" data-port="${x.port}">Pair</button>` : html`<span></span>`}</li>`) : html`<li><span></span><span class="sm muted">No devices discovered — enter IP and ports below</span><span></span></li>`}`;
}

async function loadHistory() {
  const dev = $('#of').value;
  const { jobs } = await api('GET', `/api/jobs?kind=ota&limit=50${dev ? `&device_id=${dev}` : ''}`);
  $('#ota tbody').innerHTML = html`${jobs.map(j => {
    const d = j.detail || { installs: [] };
    const installs = d.installs || [];
    const ok = installs.filter(i => i.result !== 'failed').length;
    const name = (D.devices.find(x => x.id === j.target.device_id) || {}).name || `device ${j.target.device_id}`;
    const outcome = d.outcome === 'skipped_offline' ? 'Skipped · offline' : d.outcome || j.status;
    return html`<tr><td>${fmt.abs(j.created_at)}</td><td>${name}</td><td class="sm muted hide-m">${j.trigger}${d.mode && d.mode !== 'normal' ? ` · ${d.mode}` : ''}</td><td class="sm">${installs.map(i => html`<div>${i.package} ${i.version_name || i.upstream_tag || ''} ${i.result === 'installed' ? '✓' : i.result === 'up_to_date' ? '· already current, skipped' : html`<span style="color:hsl(var(--destructive))">✗ ${i.error_code}</span>`}${i.error_code === 'SIGNATURE_MISMATCH' ? html` <button class="btn btn-ghost btn-sm danger" type="button" data-force="${j.id}" data-install="${i.id}" data-pkg="${i.package}">Uninstall &amp; retry</button>` : ''}${i.error_code === 'VERSION_DOWNGRADE' ? html` <button class="btn btn-ghost btn-sm" type="button" data-down="${j.id}" data-install="${i.id}">Install with downgrade</button>` : ''}</div>`)}</td><td class="mono">${ok} / ${installs.length}</td><td><span class="badge ${j.status === 'success' ? 'b-ok' : j.status === 'failed' ? 'b-err' : 'b-mute'}">${outcome}</span></td></tr>`;
  })}`;
}

async function pair(body) {
  try {
    const r = await api('POST', '/api/devices/pair', body);
    toast(`Paired ${r.device.name}`);
    await loadDevices();
  } catch (e) {
    toast(e.message, 'error');   // e.g. PAIR_FAILED: <adb line>
  }
}

async function reconnect(d) {
  try {
    await api('POST', `/api/devices/${d.id}/connect`, {});
    toast(`${d.name} connected`);
  } catch (e) {
    if (e.status === 409 && e.data && e.data.error === 'DEVICE_OFFLINE') {
      const port = prompt('New connect port shown on the phone (Wireless debugging → IP address & Port)');
      if (port) await act('POST', `/api/devices/${d.id}/connect`, { connect_port: Number(port) }, `${d.name} connected`).catch(() => {});
    } else {
      toast(e.message, 'error');
    }
  }
  await loadDevices();
}

async function editAssignments(d) {
  const mine = new Set(d.assignments.map(a => a.item_id));
  const mineP = new Set(d.projects.filter(x => x.assigned).map(x => x.project_id));
  $('#as-items').innerHTML = html`${[...D.items.values()].map(it => html`<label class="row sm"><input type="checkbox" class="cb" value="${it.id}" ${mine.has(it.id) ? html`checked` : ''}> ${it.label} <span class="xs muted mono">${it.package}</span></label>`)}`;
  $('#as-projects').innerHTML = html`<span class="lbl" style="margin-top:.5rem">Followed projects</span>${D.projects.length ? D.projects.map(p => html`<label class="row sm"><input type="checkbox" class="cb" value="${p.id}" ${mineP.has(p.id) ? html`checked` : ''}> ${p.label} <span class="xs muted mono">${p.package || p.repo}</span></label>`) : html`<p class="xs muted">No followed projects.</p>`}`;
  const dlg = $('#dlg-assign');
  if (await ask(dlg) !== 'ok') return;
  act('PUT', `/api/devices/${d.id}/assignments`, { item_ids: $$('#as-items input:checked').map(i => Number(i.value)), project_ids: $$('#as-projects input:checked').map(i => Number(i.value)) }, 'Apps saved').then(loadDevices).catch(() => {});
}

async function pairDialog(p) {
  $('#pt').textContent = `Pair ${p.dataset.pair}:${p.dataset.port}`;
  $$('#d-otp input').forEach(i => { i.value = ''; });
  const dlg = $('#dlg-pair');
  if (await ask(dlg) !== 'ok') return;
  const body = { host: p.dataset.pair, pair_port: Number(p.dataset.port), code: otpValue($('#d-otp')) };
  if ($('#d-cport').value) body.connect_port = Number($('#d-cport').value);
  pair(body);
}

document.addEventListener('click', async e => {
  const t = sel => e.target.closest(sel);
  const sec = t('[data-dev]');
  const d = sec && D.devices.find(x => x.id === Number(sec.dataset.dev));
  try {
    if (t('[data-pair]')) return pairDialog(t('[data-pair]'));
    if (d && t('[data-reconnect]')) return reconnect(d);
    if (d && t('[data-assign]')) {
      D.projects = (await act('GET', '/api/projects')).projects;
      return editAssignments(d);
    }
    if (d && t('[data-ota]')) await act('POST', `/api/devices/${d.id}/ota`, {}, `OTA queued on ${d.name}`);
    else if (d && t('[data-unpair]')) {
      if (!confirm(`Remove ${d.name}? On the phone, also tap Forget under Wireless debugging.`)) return;
      await act('DELETE', `/api/devices/${d.id}`, {}, 'Device removed');
    } else if (t('[data-force]')) {
      const f = t('[data-force]');
      if (!confirm(`Uninstall ${f.dataset.pkg} and install the patched app? All app data on the phone is lost.`)) return;
      await act('POST', `/api/ota/${f.dataset.force}/force-reinstall`, { install_id: Number(f.dataset.install) }, 'Reinstall queued');
    } else if (t('[data-down]')) {
      const dn = t('[data-down]');
      await act('POST', `/api/ota/${dn.dataset.down}/downgrade`, { install_id: Number(dn.dataset.install) }, 'Downgrade install queued');
    } else return;
    await loadDevices();
    await loadHistory();
  } catch (_) { /* act() showed it */ }
});

document.addEventListener('change', e => {
  const sec = e.target.closest('[data-dev]');
  if (sec && e.target.matches('[data-oab]')) act('PATCH', `/api/devices/${sec.dataset.dev}`, { ota_after_build: e.target.checked }, 'Saved').catch(loadDevices);
});
document.addEventListener('keydown', e => {
  const h = e.target.closest('[data-rename]');
  if (!h || (e.key !== 'Enter' && e.key !== 'Escape')) return;
  e.preventDefault();
  if (e.key === 'Escape') h.textContent = D.devices.find(x => x.id === Number(h.closest('[data-dev]').dataset.dev)).name;
  h.blur();
});
document.addEventListener('focusout', e => {
  const h = e.target.closest('[data-rename]');
  if (!h) return;
  const sec = h.closest('[data-dev]'), name = h.textContent.trim();
  const d = D.devices.find(x => x.id === Number(sec.dataset.dev));
  if (name && name !== d.name) act('PATCH', `/api/devices/${d.id}`, { name }, 'Renamed').then(loadDevices).catch(loadDevices);
});
$('#man').addEventListener('submit', e => {
  e.preventDefault();
  const body = { host: $('#m-host').value.trim(), pair_port: Number($('#m-pport').value), code: otpValue($('#m-otp')) };
  if ($('#m-cport').value) body.connect_port = Number($('#m-cport').value);
  pair(body);
});
$('#ota-all').addEventListener('click', async () => {
  const online = D.devices.filter(d => d.online);
  for (const d of online) await act('POST', `/api/devices/${d.id}/ota`, {}).catch(() => {});
  toast(`OTA queued on ${online.length} device${online.length === 1 ? '' : 's'}`);
});
$('#of').addEventListener('change', () => loadHistory().catch(() => {}));

(async () => {
  shell();
  wireOtp($('#m-otp'));
  wireOtp($('#d-otp'));
  const [cat, s] = await Promise.all([api('GET', '/api/catalog'), api('GET', '/api/settings')]);
  catalogItems(cat).forEach(it => D.items.set(it.id, it));
  $('#hostline').textContent = `Network mode: ${s.server.network_mode || 'unknown'} · discovery runs on every platform; when nothing is found, enter the phone's IP and ports below`;
  await loadDevices();
  await loadHistory();
  await loadDiscovered().catch(() => {});
  every(10000, () => loadDiscovered().catch(() => {}));
  every(15000, () => { if (!document.querySelector('dialog[open]') && !document.activeElement.closest('[data-rename]')) loadDevices().catch(() => {}); });
})().catch(e => toast(e.message, 'error'));
