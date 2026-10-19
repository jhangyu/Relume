import { $, api, fmt, html } from '/static/app.js';

const form = $('#f'), pw = $('#p'), err = $('#err'), go = $('#go');

$('#pwt').addEventListener('click', e => {
  const hidden = pw.type === 'password';
  pw.type = hidden ? 'text' : 'password';
  e.currentTarget.textContent = hidden ? 'Hide' : 'Show';
  e.currentTarget.setAttribute('aria-pressed', hidden);
});

function showError(text) {
  err.querySelector('span').textContent = text;
  err.hidden = false;
  pw.setAttribute('aria-invalid', 'true');
}

function busy(on) {
  go.disabled = on;
  go.setAttribute('aria-busy', on);
  go.lastChild.textContent = on ? 'Signing in…' : 'Sign in';
}

form.addEventListener('submit', async e => {
  e.preventDefault();
  busy(true);
  try {
    await api('POST', '/api/auth/login', { password: pw.value, remember: $('#remember').checked });
    location.href = '/';
  } catch (ex) {
    busy(false);
    if (ex.status === 401) {
      showError(`Wrong password. ${ex.data.attempts_left} attempts left before a 5-minute lockout.`);
    } else if (ex.status === 429) {
      const s = ex.data.retry_after;
      showError(`Too many attempts. Try again in ${Math.floor(s / 60)} min ${s % 60} s.`);
      go.disabled = true;
      setTimeout(() => { go.disabled = false; }, s * 1000);
    } else {
      showError('Server unreachable');
    }
  }
});

(async () => {
  const srv = $('#srv');
  try {
    const h = await api('GET', '/api/health');
    srv.innerHTML = html`<dt>host</dt><dd style="margin:0">${location.host}</dd><dt>status</dt><dd style="margin:0">Server up · ${h.app}</dd><dt>version</dt><dd style="margin:0">${h.version}</dd><dt>up since</dt><dd style="margin:0">${fmt.abs(h.started_at)}</dd>`;
  } catch (_) {
    srv.innerHTML = html`<dt>status</dt><dd style="margin:0">Server unreachable</dd>`;
  }
})();
