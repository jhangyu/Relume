import { $, api, fmt, html } from '/static/app.js';

const MIN_PASSWORD_LENGTH = 10;
const form = $('#f'), user = $('#u'), pw = $('#p'), pw2 = $('#p2'), err = $('#err'), go = $('#go');
let setupMode = false;
const cooldownTimers = new Map(); // one cooldown timer per button

$('#pwt').addEventListener('click', e => {
  const hidden = pw.type === 'password';
  pw.type = pw2.type = hidden ? 'text' : 'password';
  e.currentTarget.textContent = hidden ? 'Hide' : 'Show';
  e.currentTarget.setAttribute('aria-pressed', hidden);
});

function showError(text) {
  err.querySelector('span').textContent = text;
  err.hidden = false;
}

const minSec = s => `${Math.floor(s / 60)} min ${s % 60} s`;

function setMode(setup) {
  setupMode = setup;
  $('#title').textContent = setup ? 'Create admin account' : 'Sign in';
  $('#sub').textContent = setup
    ? 'First start: choose the username and password of the single admin account.'
    : 'Single admin account for this build server.';
  $('#cf').hidden = $('#ph').hidden = !setup;
  pw2.required = setup;
  pw.autocomplete = setup ? 'new-password' : 'current-password';
  go.lastChild.textContent = setup ? 'Create account' : 'Sign in';
}

function busy(on) {
  go.disabled = on;
  go.setAttribute('aria-busy', on);
  go.lastChild.textContent = on ? (setupMode ? 'Creating…' : 'Signing in…') : (setupMode ? 'Create account' : 'Sign in');
}

function lockButton(btn, seconds) {
  btn.disabled = true;
  clearTimeout(cooldownTimers.get(btn));
  cooldownTimers.set(btn, setTimeout(() => { btn.disabled = false; }, seconds * 1000));
}

async function boot() {
  try {
    const st = await api('GET', '/api/auth/state');
    setMode(st.setup_required);
  } catch (_) {
    showError('Server unreachable');
  }
}

form.addEventListener('submit', async e => {
  e.preventDefault();
  if (setupMode) {
    if (pw.value.length < MIN_PASSWORD_LENGTH) return showError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
    if (pw.value !== pw2.value) return showError('Passwords do not match.');
  }
  busy(true);
  try {
    await api('POST', setupMode ? '/api/auth/setup' : '/api/auth/login',
      { username: user.value, password: pw.value, remember: $('#remember').checked });
    location.href = '/';
  } catch (ex) {
    busy(false);
    if (ex.status === 400) {
      showError(ex.data?.error || 'Invalid username or password format.');
    } else if (ex.status === 401) {
      showError(`Wrong username or password. ${ex.data.attempts_left} attempts left before a 5-minute lockout.`);
    } else if (ex.status === 409) {
      // setup closed (someone else created the account) or login attempted before any account exists
      await boot();
      showError(setupMode ? 'No account exists yet. Create it below.' : 'An account already exists. Sign in instead.');
    } else if (ex.status === 429) {
      const s = ex.data.retry_after;
      showError(`Too many attempts. Try again in ${minSec(s)}.`);
      lockButton(go, s);
    } else {
      showError('Server unreachable');
    }
  }
});

boot();

(async () => {
  const srv = $('#srv');
  try {
    const h = await api('GET', '/api/health');
    srv.innerHTML = html`<dt>host</dt><dd style="margin:0">${location.host}</dd><dt>status</dt><dd style="margin:0">Server up · ${h.app}</dd><dt>version</dt><dd style="margin:0">${h.version}</dd><dt>up since</dt><dd style="margin:0">${fmt.abs(h.started_at)}</dd>`;
  } catch (_) {
    srv.innerHTML = html`<dt>status</dt><dd style="margin:0">Server unreachable</dd>`;
  }
})();
