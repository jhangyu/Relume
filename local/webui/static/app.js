// Shared helpers for every page (spec ui §1). Ported from mockup apps.html:158-171.
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ESC[c]);

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
export function toggleTheme() {
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
