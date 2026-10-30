import { $, $$, act, api, confirmDialog, fmt, html, icon, shell, toast } from '/static/app.js';

const FIELDS = {
  telegram: { bot_token: '#tg-token', chat_id: '#tg-chat' },
  discord: { webhook_url: '#dc-url' },
  ntfy: { server_url: '#nt-url', topic: '#nt-topic', token: '#nt-token' },
};
const Z = { settings: null, items: [], notify: null, appDevs: [] };

function showChannel(t) {
  $$('[data-ch]').forEach(p => { p.hidden = p.dataset.ch !== t; });
}

// Secret fields hold the server's masked value only; it is re-sent as shown so the stored secret is kept.
async function loadNotify() {
  const n = await api('GET', '/api/notify');
  Z.notify = n;
  const type = n.type || 'telegram';
  $(`[name=ch][value=${type}]`).checked = true;
  showChannel(type);
  for (const [k, sel] of Object.entries(FIELDS[type])) $(sel).value = (n.config && n.config[k]) || '';
  $('#n-en').checked = !!n.enabled;
  const ev = new Set(n.type ? n.events : ['upstream.new_version', 'build.succeeded', 'build.failed', 'ota.done', 'ota.failed', 'project.signer_changed']);
  $$('[name=ev]').forEach(c => { c.checked = c.value === 'ota' ? ev.has('ota.done') || ev.has('ota.failed') : ev.has(c.value); });
  $('#n-last').textContent = n.last_test ? `Last test: ${fmt.abs(n.last_test.at)} · ${n.last_test.ok ? 'delivered' : `failed (${n.last_test.detail})`}` : 'Last test: never';
}

async function saveNotify() {
  const type = $('[name=ch]:checked').value;
  const config = {};
  for (const [k, sel] of Object.entries(FIELDS[type])) if ($(sel).value.trim()) config[k] = $(sel).value.trim();
  const events = $$('[name=ev]:checked').flatMap(c => (c.value === 'ota' ? ['ota.done', 'ota.failed'] : [c.value]));
  await act('PUT', '/api/notify', { type, enabled: $('#n-en').checked, config, events }, 'Notification channel saved');
  await loadNotify();
}

function renderEstimate() {
  const s = Z.settings, keep = Number($('#keep').value) || 0, d = s.server.disk;
  const apks = Z.items.reduce((n, it) => n + Math.max(1, (it.abis || []).length), 0) * keep;
  const avg = d.artifacts_count ? d.artifacts / d.artifacts_count : 0;
  $('#est').textContent = `≈ ${apks} APKs${avg ? ` (~${fmt.bytes(apks * avg)})` : ''}`;
}

function renderSettings() {
  const s = Z.settings, d = s.server.disk, g = s.github;
  $('#keep').value = s.artifact_retention;
  $('#hist-n').value = s.job_history_count;
  $('#base').value = s.base_url;
  renderEstimate();
  const parts = [['APKs', d.artifacts], ['Download cache', d.apk_cache], ['Patches', d.upstream], ['Logs', d.logs], ['Free', d.free]];
  $('#disk').innerHTML = html`${parts.map(([n, v]) => html`<div class="row between xs"><span>${n}</span><span class="mono">${fmt.bytes(v)}</span></div><div class="progress"><span style="width:${Number(d.total ? (100 * v) / d.total : 0).toFixed(1)}%"></span></div>`)}`;
  const cls = { authenticated: 'ok', unauthenticated: 'warn', rejected: 'err', absent: '' }[g.state] || '';
  $('#gh-state').className = `badge ${cls}`;
  $('#gh-state').textContent = g.state + (g.error ? ` · ${g.error}` : '');
  $('#gh-hint').textContent = g.token_hint || (g.state === 'absent' ? 'No token set' : 'empty token');
  $('#gh-path').textContent = g.path;
  $('#gh-exp').textContent = g.expires_at || '—';
  $('#gh-scopes').textContent = g.scopes || '—';
  $('#gh-gauge').style.width = `${g.limit ? Math.round((100 * g.remaining) / g.limit) : 0}%`;
  $('#gh-reset').textContent = g.limit ? `${g.remaining} / ${g.limit} left · resets ${fmt.rel(g.reset_at)}` : '—';
  $('#gh-src').textContent = { ui: 'set (saved here)', file: 'set (secret file)' }[g.source] || 'not set';
  $('#gh-clear').hidden = g.source !== 'ui';
  $('#gh-60').hidden = g.state !== 'unauthenticated';
  const gp = s.google_play;
  $('#gp-state').className = `badge ${gp.set ? 'ok' : ''}`;
  $('#gp-state').textContent = gp.set ? 'set' : 'not set';
  $('#gp-email').textContent = gp.email || 'No account set';
  $('#gp-mail').value = gp.email || '';
  $('#gp-clear').hidden = !gp.set;
}

async function loadSettings(recheck) {
  Z.settings = await api('GET', `/api/settings${recheck ? '?recheck=1' : ''}`);
  renderSettings();
}

async function saveSettings() {
  const s = Z.settings, body = {};
  const keep = Number($('#keep').value), hist = Number($('#hist-n').value), base = $('#base').value.trim();
  if (keep !== s.artifact_retention) body.artifact_retention = keep;
  if (hist !== s.job_history_count) body.job_history_count = hist;
  if (base !== s.base_url) body.base_url = base;
  if (!Object.keys(body).length) return toast('Nothing changed');
  Z.settings = await act('PUT', '/api/settings', body, 'Saved');
  renderSettings();
}

function score(p) {
  let n = 0;
  if (p.length >= 10) n++;
  if (p.length >= 14) n++;
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) n++;
  if (/\d/.test(p)) n++;
  if (/[^A-Za-z0-9]/.test(p)) n++;
  return Math.min(4, n);
}

function checkPw() {
  const a = $('#pw-new').value, b = $('#pw-new2').value, s = score(a);
  const word = ['Weak', 'Weak', 'Fair', 'Good', 'Strong'][s];
  $('#pw-meter').value = s;
  $('#pw-meter').title = word;
  $('#pw-meter-l').textContent = a ? word : '';
  const err = a && a.length < 10 ? 'At least 10 characters' : b && a !== b ? 'Passwords do not match' : '';
  $('#pw-err').textContent = err;
  $('#pw-err').hidden = !err;
  $('#pw-save').disabled = !!err || !a || a !== b || !$('#pw-old').value;
}

$$('[name=ch]').forEach(r => r.addEventListener('change', () => showChannel(r.value)));
$('#n-save').addEventListener('click', () => saveNotify().catch(() => {}));
$('#n-test').addEventListener('click', async () => {
  try {
    const r = await api('POST', '/api/notify/test', {});
    toast(r.ok ? 'Delivered' : `Failed: ${r.detail}`, r.ok ? 'ok' : 'error');
  } catch (e) { toast(e.message, 'error'); }
  loadNotify().catch(() => {});
});
$('#keep').addEventListener('input', renderEstimate);
$('#cleanup').addEventListener('click', async () => {
  try {
    const p = await api('GET', '/api/cleanup');
    if (!p.bytes) { toast('Nothing to clean up'); return; }
    const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
    const extra = html`<dl class="dlg-facts"><div><dt>Builds of apps not enabled</dt><dd>${plural(p.artifact_items, 'app')}</dd></div><div><dt>Cached download files</dt><dd>${plural(p.cache_files, 'file')}</dd></div></dl>`;
    if (!await confirmDialog({ title: `Delete ${fmt.bytes(p.bytes)}?`, body: 'An app rebuilds when you enable it again.', extra, action: 'Delete' })) return;
    const r = await api('POST', '/api/cleanup', {});
    toast(`Freed ${fmt.bytes(r.bytes)}`);
    await loadSettings(false);
  } catch (e) { toast(e.message, 'error'); }
});
$('#a-save').addEventListener('click', () => saveSettings().catch(() => {}));
async function saveToken(v) {
  Z.settings = await act('PUT', '/api/settings', { github_token: v }, v ? 'Token saved' : 'Token removed');
  $('#gh-token').value = '';
  renderSettings();
}
$('#gh-save').addEventListener('click', () => { if ($('#gh-token').value.trim()) saveToken($('#gh-token').value.trim()).catch(() => {}); });
$('#gh-clear').addEventListener('click', () => saveToken('').catch(() => {}));
async function saveGooglePlay(v) {
  Z.settings = await act('PUT', '/api/settings', { google_play: v }, v ? 'Google Play account saved' : 'Google Play account removed');
  $('#gp-token').value = '';
  renderSettings();
}
$('#gp-save').addEventListener('click', () => {
  const email = $('#gp-mail').value.trim(), aas_token = $('#gp-token').value.trim();
  if (!email || !aas_token) return toast('Email and AAS token are both required', 'error');
  saveGooglePlay({ email, aas_token }).catch(() => {});
});
$('#gp-clear').addEventListener('click', () => saveGooglePlay(null).catch(() => {}));
$('#gh-recheck').addEventListener('click', () => loadSettings(true).then(() => toast('Re-checked')).catch(() => {}));
['#pw-old', '#pw-new', '#pw-new2'].forEach(s => $(s).addEventListener('input', checkPw));
$('#pw-save').addEventListener('click', async () => {
  try {
    const r = await api('POST', '/api/auth/password', { old: $('#pw-old').value, new: $('#pw-new').value });
    toast(`Password changed · ${r.signed_out} other session${r.signed_out === 1 ? '' : 's'} signed out`);
    ['#pw-old', '#pw-new', '#pw-new2'].forEach(s => { $(s).value = ''; });
    checkPw();
  } catch (e) {
    toast(e.status === 403 ? 'Current password is wrong' : e.message, 'error');
  }
});

async function loadCatalog() {
  const feed = await api('GET', '/api/catalog/status');
  const errs = [...feed.feeds.filter(f => f.last_error).map(f => `Catalog feed ${f.feed}: ${f.last_error}`),
    ...feed.sources.filter(s => s.last_error).map(s => `Source ${s.repo}: ${s.last_error}`)];
  const skipped = feed.feeds.flatMap(f => f.skipped.map(s => `${f.feed} skipped ${s.where}${s.repo ? ` (${s.repo})` : ''}: ${s.error}`));
  const n = skipped.length;
  $('#catalog-notice').innerHTML = html`${errs.length ? html`<div class="msgbar err">${icon('error')}<div class="grow"><div class="strong">The last catalog refresh failed</div>${errs.map(e => html`<div>${e}</div>`)}</div></div>` : ''}${n ? html`<div class="msgbar warn">${icon('warning')}<div class="grow"><details><summary>${n} catalog ${n === 1 ? 'entry' : 'entries'} skipped as invalid</summary>${skipped.map(e => html`<div class="mono">${e}</div>`)}</details></div></div>` : ''}${errs.length || n ? '' : html`<p class="sm muted">No catalog errors or skipped entries.</p>`}`;
}

async function loadAppDevices() {
  const { devices } = await api('GET', '/api/devices');
  const apps = Z.appDevs = devices.filter(d => d.kind === 'app');
  $('#app-devs').innerHTML = html`${apps.length ? apps.map(d => html`<li data-app-dev="${d.id}"><div><div class="sm" style="font-weight:500">${d.name}</div><div class="xs muted">${d.model} · Android ${d.android_release} · created ${fmt.abs(d.created_at)} · last seen ${fmt.rel(d.last_seen_at)}</div></div>${d.has_token ? html`<button class="btn small danger" type="button" data-revoke="${d.id}">Revoke</button>` : html`<span class="badge">revoked</span>`}</li>`) : html`<li><span class="sm muted">No phones have signed in with the app.</span></li>`}`;
}

$('#app-devs').addEventListener('click', async e => {
  const b = e.target.closest('[data-revoke]');
  if (!b) return;
  const d = Z.appDevs.find(x => x.id === Number(b.dataset.revoke));
  const body = html`<strong>${d.name}</strong> is signed out of the app and must log in again.`;
  const extra = html`<p class="caption dlg-sub">${d.model} · Android ${d.android_release} · last seen ${fmt.rel(d.last_seen_at)}</p>`;
  if (!await confirmDialog({ title: 'Revoke this phone?', body, extra, action: 'Revoke' })) return;
  await act('DELETE', `/api/devices/${b.dataset.revoke}/token`, {}, 'Token revoked');   // act() toasts a failure and rethrows
  await loadAppDevices();
});

(async () => {
  shell();
  Z.items = (await api('GET', '/api/apps')).apps;
  await Promise.all([loadNotify(), loadSettings(false), loadAppDevices(), loadCatalog()]);
  checkPw();
})().catch(e => toast(e.message, 'error'));
