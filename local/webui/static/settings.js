import { $, $$, act, api, fmt, html, shell, toast } from '/static/app.js';

const FIELDS = {
  telegram: { bot_token: '#tg-token', chat_id: '#tg-chat' },
  discord: { webhook_url: '#dc-url' },
  ntfy: { server_url: '#nt-url', topic: '#nt-topic', token: '#nt-token' },
};
const Z = { settings: null, items: [], notify: null };

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
  $('#disk').innerHTML = html`${parts.map(([n, v]) => html`<div class="row between xs"><span>${n}</span><span class="mono">${fmt.bytes(v)}</span></div><div class="progress"><i style="width:${Number(d.total ? (100 * v) / d.total : 0).toFixed(1)}%"></i></div>`)}`;
  const cls = { authenticated: 'b-ok', unauthenticated: 'b-warn', rejected: 'b-err', absent: 'b-mute' }[g.state] || 'b-mute';
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
$('#a-save').addEventListener('click', () => saveSettings().catch(() => {}));
async function saveToken(v) {
  Z.settings = await act('PUT', '/api/settings', { github_token: v }, v ? 'Token saved' : 'Token removed');
  $('#gh-token').value = '';
  renderSettings();
}
$('#gh-save').addEventListener('click', () => { if ($('#gh-token').value.trim()) saveToken($('#gh-token').value.trim()).catch(() => {}); });
$('#gh-clear').addEventListener('click', () => saveToken('').catch(() => {}));
$('#gh-recheck').addEventListener('click', () => loadSettings(true).then(() => toast('Re-checked')).catch(() => {}));
$('#gh-cli').addEventListener('click', async () => {
  try {
    const { upstreams } = await api('GET', '/api/upstreams');
    const cli = upstreams.filter(u => u.kind === 'cli');
    for (const u of cli) await act('POST', `/api/upstreams/${u.id}/pull`, {}).catch(() => {});
    toast(`CLI update queued (${cli.length})`);
  } catch (e) { toast(e.message, 'error'); }
});
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

(async () => {
  shell();
  const cat = await api('GET', '/api/catalog');
  Z.items = [...cat.groups.flatMap(g => g.items), ...cat.ungrouped];
  await Promise.all([loadNotify(), loadSettings(false)]);
  checkPw();
})().catch(e => toast(e.message, 'error'));
