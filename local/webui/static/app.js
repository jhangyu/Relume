// Shared helpers for every page (spec ui §1).
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ESC[c]);

class Safe { constructor(s) { this.s = s; } toString() { return this.s; } }
const part = v => (v instanceof Safe ? v.s : Array.isArray(v) ? v.map(part).join('')
  : v == null || v === false ? '' : esc(v));
export function html(strings, ...vals) {
  return new Safe(strings.reduce((acc, s, i) => acc + part(vals[i - 1]) + s));
}

export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];

export async function api(method, path, body) {
  const init = { method, credentials: 'same-origin', headers: {} };
  if (method !== 'GET') {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body ?? {});
  }
  const r = await fetch(path, init);
  const data = (r.headers.get('Content-Type') || '').startsWith('application/json') ? await r.json() : null;
  if (r.status === 401 && location.pathname !== '/login.html') {
    location.href = '/login.html';
    throw Object.assign(new Error('login required'), { status: 401 });
  }
  if (!r.ok) throw Object.assign(new Error(data?.error || `HTTP ${r.status}`), { status: r.status, data });
  return data;
}

export async function act(method, path, body, okMsg) {
  try {
    const d = await api(method, path, body);
    if (okMsg) toast(okMsg);
    return d;
  } catch (e) {
    if (e.status !== 401) toast(e.message, 'error');
    throw e;
  }
}

export function toast(msg, kind = 'ok') {
  const t = $('#toast');
  if (!t) return;
  t.querySelector('span').textContent = msg;
  t.dataset.kind = kind;
  t.classList.add('show');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('show'), 2600);
}

export function logStream(jobId, onText, onEnd) {
  const es = new EventSource(`/api/jobs/${Number(jobId)}/log`);
  es.onmessage = e => onText(e.data + '\n');
  es.addEventListener('end', e => { es.close(); onEnd(e.data); });
  return es;
}

const rtf = new Intl.RelativeTimeFormat([], { numeric: 'auto' });
export const fmt = {
  abs: iso => (iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—'),
  rel: iso => {
    if (!iso) return '—';
    const s = Math.round((Date.parse(iso) - Date.now()) / 1000), a = Math.abs(s);
    if (a < 60) return rtf.format(s, 'second');
    if (a < 3600) return rtf.format(Math.round(s / 60), 'minute');
    if (a < 86400) return rtf.format(Math.round(s / 3600), 'hour');
    return rtf.format(Math.round(s / 86400), 'day');
  },
  bytes: n => {
    if (n == null) return '—';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${i ? n.toFixed(1) : n} ${u[i]}`;
  },
  gb: n => (n == null ? '—' : (n / 1e9).toFixed(1)),
  duration: (a, b) => {
    if (!a) return '—';
    const s = Math.max(0, Math.round(((b ? Date.parse(b) : Date.now()) - Date.parse(a)) / 1000));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
    return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  },
};

export function every(ms, fn) {
  const tick = () => { if (document.visibilityState === 'visible') fn(); };
  const h = setInterval(tick, ms);
  document.addEventListener('visibilitychange', tick);
  return () => { clearInterval(h); document.removeEventListener('visibilitychange', tick); };
}

export function ask(dlg) {
  return new Promise(resolve => {
    dlg.returnValue = '';
    dlg.addEventListener('close', () => resolve(dlg.returnValue), { once: true });
    dlg.showModal();
  });
}

export const STATUS = {
  run: { label: 'Building', cls: 'b-info' },
  fail: { label: 'Failed', cls: 'b-err' },
  upd: { label: 'Update available', cls: 'b-warn' },
  new: { label: 'Not built', cls: 'b-mute' },
  ok: { label: 'Up to date', cls: 'b-ok' },
};

export function itemStatus(it) {
  if (it.active_job) return 'run';
  if (it.last_build && it.last_build.status === 'failed') return 'fail';
  if (it.upstream && it.upstream.latest_tag && it.upstream.latest_tag !== it.upstream.downloaded_tag) return 'upd';
  if (!it.last_success) return 'new';
  return 'ok';
}

export function iconTile(it, cls = '') {
  const letters = (it.label || it.package || '?').split(/[\s·]+/).filter(Boolean).slice(0, 2)
    .map(w => w[0]).join('').toUpperCase();
  return html`<span class="ico ${cls}" aria-hidden="true" data-l="${letters}"><img src="${it.icon_url}" alt="" loading="lazy" style="width:100%;height:100%;border-radius:inherit" data-fallback></span>`;
}

// One delegated handler: a broken icon becomes its letter tile (spec ui §2 "Letter tile when no icon").
document.addEventListener('error', e => {
  const img = e.target;
  if (img.tagName === 'IMG' && img.hasAttribute('data-fallback')) img.replaceWith(document.createTextNode(img.parentNode.dataset.l));
}, true);

const root = document.documentElement;
function toggleTheme() {
  root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('relume-theme', root.dataset.theme); } catch (_) { /* private mode */ }
}

async function logout() {
  try { await api('POST', '/api/auth/logout'); } finally { location.href = '/login.html'; }
}

document.addEventListener('click', e => {
  if (e.target.closest('[data-theme-toggle]')) toggleTheme();
  if (e.target.closest('[data-logout]')) logout();
  if (e.target.closest('[data-nav-toggle]')) document.body.classList.toggle('nav-open');
  else if (document.body.classList.contains('nav-open') && !e.target.closest('.side')) document.body.classList.remove('nav-open');
  $$('details.dd[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
});
document.addEventListener('click', e => {
  const p = e.target.closest('[data-proxy]');
  if (!p) return;
  const target = $(p.dataset.proxy);
  if (p.dataset.value === undefined) target.click();
  else { target.value = p.dataset.value; target.dispatchEvent(new Event('change', { bubbles: true })); }
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  document.body.classList.remove('nav-open');
  $$('details.dd[open]').forEach(d => { d.open = false; });
});

export function tabs(scope = document) {
  $$('[role=tablist]', scope).forEach(tl => {
    const list = $$('[role=tab]', tl);
    const sel = t => {
      list.forEach(x => {
        const on = x === t;
        x.setAttribute('aria-selected', on);
        x.tabIndex = on ? 0 : -1;
        document.getElementById(x.getAttribute('aria-controls')).hidden = !on;
      });
      t.focus();
    };
    list.forEach((t, i) => {
      t.addEventListener('click', () => sel(t));
      t.addEventListener('keydown', e => {
        const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (d) sel(list[(i + d + list.length) % list.length]);
      });
    });
  });
}

export function shell() {
  $('.side').innerHTML = html`
<div class="brand"><span class="logo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 12l-8.5 8.5a2.1 2.1 0 0 1-3-3L12 9"/><path d="M17.6 15L22 10.6 13.4 2 9 6.4z"/></svg></span>Relume <span class="src" style="margin-left:auto">LAN</span></div>
<nav class="nav stack-sm" style="gap:2px"><a href="/"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/></svg>App list</a><a href="/projects.html"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>Followed projects</a><a href="/builds.html"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 17l6-6-6-6M12 19h8"/></svg>Builds</a><a href="/schedules.html"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>Schedules</a><a href="/devices.html"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="2"/><path d="M11 18h2"/></svg>Devices</a><a href="/settings.html"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/></svg>Settings</a></nav>
<div class="xs muted stack-sm" style="margin-top:auto;padding:0 .5rem .75rem;gap:.25rem" data-shell-status aria-label="Server status"></div><div class="foot" style="margin-top:0"><div class="row" style="flex-wrap:nowrap"><span class="ico ico-sm" style="background:#52525b;width:28px;height:28px">AD</span><div><div class="sm" style="font-weight:500">Admin</div><div class="xs muted" data-host></div></div></div>
<button class="btn btn-ghost btn-icon btn-sm" type="button" data-logout aria-label="Sign out" title="Sign out"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg></button></div>
`;
  const here = location.pathname === '/index.html' ? '/' : location.pathname;
  $$('.side .nav a').forEach(a => {
    if (a.getAttribute('href') === here) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  tabs();
  $$('[data-host]').forEach(el => { el.textContent = location.hostname; });
  const box = $('[data-shell-status]');
  if (!box) return;
  const load = async () => {
    const [run, queued, s] = await Promise.all([
      api('GET', '/api/jobs?status=running&limit=100'),
      api('GET', '/api/jobs?status=queued&limit=100'),
      api('GET', '/api/settings'),
    ]);
    const n = run.jobs.length, m = queued.jobs.length, d = s.server.disk;
    box.innerHTML = html`<span class="row" style="gap:.5rem;flex-wrap:nowrap"><span class="dot ${n ? 'on' : ''}" aria-hidden="true"></span>${n ? 'Worker busy' : 'Idle'} · ${n} running · ${m} queued</span><span class="mono">Disk ${fmt.gb(d.total - d.free)} / ${fmt.gb(d.total)} GB · ${s.server.arch}</span>`;
  };
  load().catch(() => {});
  every(15000, () => load().catch(() => {}));
}

export const catalogItems = cat => [...cat.groups.flatMap(g => g.items), ...cat.ungrouped];

export const JOB_BADGE = { success: ['b-ok', 'Success'], failed: ['b-err', 'Failed'], running: ['b-info', 'Running'], queued: ['b-mute', 'Waiting'], cancelled: ['b-mute', 'Cancelled'] };

export async function fillDownloads(menu, itemId) {
  try {
    const { artifacts } = await api('GET', `/api/items/${itemId}/artifacts`);
    const newest = artifacts.length ? artifacts[0].job_id : null;
    const abiRank = a => (a.abi === 'arm64-v8a' ? 0 : a.abi === 'universal' ? 1 : 2);
    const rows = artifacts.filter(a => a.job_id === newest).sort((x, y) => abiRank(x) - abiRank(y));
    menu.innerHTML = html`${rows.length
      ? rows.map(a => html`<a role="menuitem" href="/api/artifacts/${a.id}/download" download>${a.variant} · ${a.abi} · ${fmt.bytes(a.size)}</a>`)
      : html`<span class="xs muted" style="padding:.5rem">No artifact</span>`}`;
  } catch (err) {
    menu.textContent = err.message;
  }
}
