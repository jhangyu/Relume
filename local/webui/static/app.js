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

const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
export const fmt = {
  abs: iso => (iso ? new Date(iso).toLocaleString('en', { dateStyle: 'medium', timeStyle: 'short' }) : '—'),
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


// Build states of spec §6.2 (`app.status.state`) and the badge class each one wears.
export const STATUS = {
  building: { label: 'Building', cls: 'brand', icon: 'sync' },
  queued: { label: 'Queued', cls: '', icon: 'history' },
  failed: { label: 'Failed', cls: 'err', icon: 'error' },
  update_available: { label: 'Update available', cls: 'lav', icon: 'sparkle' },
  needs_download_url: { label: 'Needs URL', cls: 'warn', icon: 'link' },
  not_built: { label: 'Not built', cls: '', icon: 'history' },
  up_to_date: { label: 'Up to date', cls: 'ok', icon: 'check' },
};

// Root icons.js set as one inline sprite, injected once by shell().
const SPRITE = html`
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
<symbol id="i-store" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9h16l-1 11H5L4 9Z"/><path d="M8 9V7a4 4 0 0 1 8 0v2"/></symbol>
<symbol id="i-apps" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><rect x="13.5" y="13.5" width="7" height="7" rx="2"/></symbol>
<symbol id="i-schedule" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></symbol>
<symbol id="i-builds" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7 9 3 3-3 3M12.5 15H17"/></symbol>
<symbol id="i-devices" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="6.5" y="2.5" width="11" height="19" rx="2.5"/><path d="M11 18.5h2"/></symbol>
<symbol id="i-follow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="9" r="2.5"/><path d="M6 8.5v7M18 11.5c0 4-6 2.5-10.5 5"/></symbol>
<symbol id="i-settings" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7.1 19.7l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3 14.2H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.3 7.1l-.1-.1A2 2 0 1 1 7 4.2l.1.1A1.6 1.6 0 0 0 9.8 3.1V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.2 2.7h.1a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.1.2Z"/></symbol>
<symbol id="i-search" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m20 20-4.8-4.8"/></symbol>
<symbol id="i-sort" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4v16M3.5 16.5 7 20l3.5-3.5M17 20V4M13.5 7.5 17 4l3.5 3.5"/></symbol>
<symbol id="i-filter" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M7 12h10M10 18h4"/></symbol>
<symbol id="i-chevron-down" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></symbol>
<symbol id="i-chevron-right" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></symbol>
<symbol id="i-back" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M11 6l-6 6 6 6"/></symbol>
<symbol id="i-close" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6 6 18"/></symbol>
<symbol id="i-add" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7.5"/></symbol>
<symbol id="i-sync" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 0 1-14.3 4.9M4 12A8 8 0 0 1 18.3 7.1"/><path d="M18.5 3v4.3h-4.3M5.5 21v-4.3h4.3"/></symbol>
<symbol id="i-rebuild" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 0 0 5 5l-8.9 8.9a2.1 2.1 0 0 1-3-3l8.9-8.9a4 4 0 0 0-2-2Z"/><path d="M14.7 6.3 17 4"/></symbol>
<symbol id="i-download" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></symbol>
<symbol id="i-link" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/></symbol>
<symbol id="i-paste" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="4.5" width="14" height="16.5" rx="2.5"/><path d="M9 4.5V3.5h6v1M9 11h6M9 15h4"/></symbol>
<symbol id="i-warning" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 4.2 2.9 17.5A2 2 0 0 0 4.6 20.5h14.8a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0Z"/><path d="M12 9.5v4M12 17h.01"/></symbol>
<symbol id="i-error" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/></symbol>
<symbol id="i-info" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/></symbol>
<symbol id="i-star" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8L3.5 9.7l5.9-.9L12 3.5Z"/></symbol>
<symbol id="i-sparkle" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3c.6 4.6 2.4 6.4 7 7-4.6.6-6.4 2.4-7 7-.6-4.6-2.4-6.4-7-7 4.6-.6 6.4-2.4 7-7Z"/><path d="M19 15.5c.3 1.9 1 2.6 2.5 3-1.5.4-2.2 1.1-2.5 3-.3-1.9-1-2.6-2.5-3 1.5-.4 2.2-1.1 2.5-3Z"/></symbol>
<symbol id="i-more" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="5.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18.5" cy="12" r="1.2"/></symbol>
<symbol id="i-play" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4.5v15l12.5-7.5L7 4.5Z"/></symbol>
<symbol id="i-edit" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4Z"/><path d="m13.5 6.5 4 4"/></symbol>
<symbol id="i-delete" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9.5 7V4.5h5V7M6 7l1 13h10l1-13M10 11v5M14 11v5"/></symbol>
<symbol id="i-history" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 12a8.5 8.5 0 1 0 2.5-6L3.5 8.5"/><path d="M3.5 4v4.5H8M12 8v4.5l3 1.8"/></symbol>
<symbol id="i-package" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/></symbol>
<symbol id="i-github" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M9 19c-4 1.3-4-2-6-2.5M15 21v-3.5a3 3 0 0 0-.9-2.4c3-.3 6.1-1.5 6.1-6.6a5.2 5.2 0 0 0-1.4-3.6 4.8 4.8 0 0 0-.1-3.6s-1.1-.3-3.7 1.4a12.6 12.6 0 0 0-6.6 0C5.8 1.1 4.7 1.4 4.7 1.4a4.8 4.8 0 0 0-.1 3.6A5.2 5.2 0 0 0 3.2 8.6c0 5 3.1 6.3 6.1 6.6a3 3 0 0 0-.9 2.4V21"/></symbol>
<symbol id="i-bell" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15L6 16Z"/><path d="M10 20.5a2 2 0 0 0 4 0"/></symbol>
<symbol id="i-shield" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 5 6v5.5c0 4.5 3 8 7 9.5 4-1.5 7-5 7-9.5V6l-7-3Z"/><path d="m9 12 2 2 4-4"/></symbol>
<symbol id="i-pin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4h6l-1 6 3 3H7l3-3-1-6ZM12 13v7"/></symbol>
<symbol id="i-menu" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6.5h16M4 12h16M4 17.5h16"/></symbol>
</svg>
`;

export const icon = (name, cls = '') => html`<svg class="i ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

// Store category name -> accent key of app.css `[data-cat=…]` (unknown names stay neutral grey).
const CATS = [['social', /social|communicat/i], ['video', /video|photo|entertain/i], ['music', /music|audio/i],
  ['productivity', /product|business|finance|education/i], ['tools', /tool|util/i], ['personalization', /personal/i],
  ['maps', /map|navig|travel/i], ['news', /news|media|book/i], ['lifestyle', /life|health|shop/i]];
export const catSlug = name => (CATS.find(([, re]) => re.test(name || '')) || [''])[0];

export function iconTile(it, cls = '') {
  const letters = (it.name || it.label || it.package || '?').split(/[\s·]+/).filter(Boolean).slice(0, 2)
    .map(w => w[0]).join('').toUpperCase();
  return html`<span class="tile ${cls}" aria-hidden="true" data-l="${letters}" data-cat="${catSlug(it.category)}"><img src="${it.icon_url}" alt="" loading="lazy" data-fallback></span>`;
}

// One delegated handler: a broken icon becomes its letter tile (spec ui §2 "Letter tile when no icon").
document.addEventListener('error', e => {
  const img = e.target;
  if (img.tagName === 'IMG' && img.hasAttribute('data-fallback')) img.replaceWith(document.createTextNode(img.parentNode.dataset.l));
}, true);

async function logout() {
  try { await api('POST', '/api/auth/logout'); } finally { location.href = '/login.html'; }
}

document.addEventListener('click', e => {
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
document.addEventListener('click', e => {
  const m = e.target.closest('.menu [role=menuitem]');
  if (m) m.closest('details.dd').open = false;
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
  $('.side').innerHTML = html`${SPRITE}
<div class="brand"><span class="logo" aria-hidden="true">R</span>Relume</div>
<a href="/">${icon('store')}Marketplace</a>
<a href="/apps.html">${icon('apps')}Apps<span class="count" data-apps-count hidden></span></a>
<a href="/schedules.html">${icon('schedule')}Schedules</a>
<div class="nav-section">Manage</div>
<a href="/builds.html">${icon('builds')}Builds</a>
<a href="/devices.html">${icon('devices')}Devices</a>
<a href="/projects.html">${icon('follow')}Followed projects</a>
<a href="/settings.html">${icon('settings')}Settings</a>
<div class="caption" style="margin-top:auto;padding:0 12px 12px;display:grid;gap:4px" data-shell-status aria-label="Server status"></div>
<div class="foot" style="margin-top:0"><span class="avatar" aria-hidden="true">AD</span><div class="grow"><div class="strong">Admin</div><div class="caption" data-host></div></div>
<button class="icon-btn" type="button" data-logout aria-label="Sign out" title="Sign out">${icon('close')}</button></div>
`;
  const here = location.pathname === '/index.html' ? '/' : location.pathname;
  $$('.side a').forEach(a => {
    if (a.getAttribute('href') === here) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  tabs();
  $$('[data-host]').forEach(el => { el.textContent = location.hostname; });
  const box = $('[data-shell-status]'), count = $('[data-apps-count]');
  const load = async () => {
    const [run, queued, s, apps] = await Promise.all([
      api('GET', '/api/jobs?status=running&limit=100'),
      api('GET', '/api/jobs?status=queued&limit=100'),
      api('GET', '/api/settings'),
      api('GET', '/api/apps'),
    ]);
    const n = run.jobs.length, m = queued.jobs.length, d = s.server.disk;
    box.innerHTML = html`<span class="row"><span class="dot ${n ? 'on' : 'off'}" aria-hidden="true"></span>${n ? 'Worker busy' : 'Idle'} · ${n} running · ${m} queued</span><span>Disk ${fmt.gb(d.total - d.free)} / ${fmt.gb(d.total)} GB · ${s.server.arch}</span>`;
    count.textContent = apps.apps.length;
    count.hidden = false;
    count.setAttribute('aria-label', `${apps.apps.length} enabled apps`);
  };
  const refresh = () => load().catch(err => console.error('shell status:', err));
  refresh();
  every(15000, refresh);
}

export const JOB_BADGE = { success: ['ok', 'Success'], failed: ['err', 'Failed'], running: ['brand', 'Running'], queued: ['', 'Waiting'], cancelled: ['', 'Cancelled'] };

export async function fillDownloads(menu, appId) {
  try {
    const { artifacts } = await api('GET', `/api/apps/${appId}/artifacts`);
    const newest = artifacts.length ? artifacts[0].job_id : null;
    const abiRank = a => (a.abi === 'arm64-v8a' ? 0 : a.abi === 'universal' ? 1 : 2);
    const rows = artifacts.filter(a => a.job_id === newest).sort((x, y) => abiRank(x) - abiRank(y));
    menu.innerHTML = html`${rows.length
      ? rows.map(a => html`<a role="menuitem" href="/api/artifacts/${a.id}/download" download>${a.variant} · ${a.abi} · ${fmt.bytes(a.size)}</a>`)
      : html`<span class="caption" style="padding:8px">No artifact</span>`}`;
  } catch (err) {
    menu.textContent = err.message;
  }
}
