# Plan: web icons (spec requirement 4)

Goal: every web icon comes from Fluent UI System Icons (20px regular), all page icons go through the one sprite in `app.js`, no package, no build step.
Path data is already fetched: `docs/logs/2026-10-26/polish-icons-paths.md` (URL table + 33 ready-to-paste `<symbol>` lines). The implementer does not re-fetch.
All line numbers below are at HEAD `3abe409`.

## 1. Sprite name → official file

All URLs returned HTTP 200 on 2026-10-26. Base: `https://raw.githubusercontent.com/microsoft/fluentui-system-icons/main/assets/`. Every file has `viewBox="0 0 20 20"` and exactly one `<path>`.

| Sprite id | Fluent file (under base) |
|---|---|
| store | `Store%20Microsoft/SVG/ic_fluent_store_microsoft_20_regular.svg` |
| apps | `Apps/SVG/ic_fluent_apps_20_regular.svg` |
| schedule | `Clock/SVG/ic_fluent_clock_20_regular.svg` |
| builds | `Window%20Console/SVG/ic_fluent_window_console_20_regular.svg` |
| devices | `Phone/SVG/ic_fluent_phone_20_regular.svg` |
| follow | `Branch%20Fork/SVG/ic_fluent_branch_fork_20_regular.svg` |
| settings | `Settings/SVG/ic_fluent_settings_20_regular.svg` |
| search | `Search/SVG/ic_fluent_search_20_regular.svg` |
| close | `Dismiss/SVG/ic_fluent_dismiss_20_regular.svg` |
| add | `Add/SVG/ic_fluent_add_20_regular.svg` |
| check | `Checkmark/SVG/ic_fluent_checkmark_20_regular.svg` |
| sync | `Arrow%20Sync/SVG/ic_fluent_arrow_sync_20_regular.svg` |
| rebuild | `Wrench/SVG/ic_fluent_wrench_20_regular.svg` (fixes the broken wrench) |
| download | `Arrow%20Download/SVG/ic_fluent_arrow_download_20_regular.svg` |
| link | `Link/SVG/ic_fluent_link_20_regular.svg` |
| paste | `Clipboard%20Paste/SVG/ic_fluent_clipboard_paste_20_regular.svg` |
| warning | `Warning/SVG/ic_fluent_warning_20_regular.svg` |
| error | `Error%20Circle/SVG/ic_fluent_error_circle_20_regular.svg` |
| info | `Info/SVG/ic_fluent_info_20_regular.svg` |
| star | `Star/SVG/ic_fluent_star_20_regular.svg` |
| sparkle | `Sparkle/SVG/ic_fluent_sparkle_20_regular.svg` |
| more | `More%20Horizontal/SVG/ic_fluent_more_horizontal_20_regular.svg` |
| play | `Play/SVG/ic_fluent_play_20_regular.svg` |
| edit | `Edit/SVG/ic_fluent_edit_20_regular.svg` |
| delete | `Delete/SVG/ic_fluent_delete_20_regular.svg` |
| history | `History/SVG/ic_fluent_history_20_regular.svg` |
| package | `Box/SVG/ic_fluent_box_20_regular.svg` |
| github | `Code/SVG/ic_fluent_code_20_regular.svg` (neutral stand-in: Fluent has no brand icons; id kept so `index.html` is not touched) |
| bell | `Alert/SVG/ic_fluent_alert_20_regular.svg` |
| pin | `Pin/SVG/ic_fluent_pin_20_regular.svg` |
| menu | `Navigation/SVG/ic_fluent_navigation_20_regular.svg` |
| send (new) | `Send/SVG/ic_fluent_send_20_regular.svg` |
| lock (new) | `Lock%20Closed/SVG/ic_fluent_lock_closed_20_regular.svg` |

Deleted (no literal, `#i-` or dynamic use): `sort`, `filter`, `chevron-down`, `chevron-right`, `back`, `shield`.
Dynamic-name check done: `icon(` with a variable reads only `STATUS` (`app.js:104-112`: sync, history, error, sparkle, link, check), `FILTERS` (`apps.js:9-15`: apps, sparkle, rebuild, error, link), `DL`/`DL_NONE` (`index.js:9-18`: info, check, warning, error), and the literal ternary at `schedules.js:93` (warning, check). None of these names is in the delete list. The only `'filter'` string (`index.js:321`) is a role name, not an icon.
`more` is unused today but becomes used by `projects.js:103` below, so it stays.

## 2. Edits

### `local/webui/static/app.js` (region: lines 113-155 and line 225 only)
1. Replace lines 113-155 (`// Root icons.js set…` comment + `const SPRITE = html\`…\`;`) with one top-level statement that injects the sprite on module load:
   ```js
   // Fluent UI System Icons (20px regular) as one inline sprite; every page imports app.js, so every page gets it.
   document.body.insertAdjacentHTML('afterbegin', html`<svg width="0" height="0" style="position:absolute" aria-hidden="true">
   …the 33 <symbol> lines from polish-icons-paths.md…
   </svg>`);
   ```
   Module scripts run after parsing, so `document.body` exists. The `html\`` wrapper is required: the XSS rule in `test_ui_static.py:6` flags `insertAdjacentHTML(…,` not followed by `html\``.
   Each symbol is `<symbol id="i-X" viewBox="0 0 20 20" fill="currentColor"><path d="…"/></symbol>` (no `stroke*` attributes).
2. Line 225: `$('.side').innerHTML = html\`${SPRITE}` → `$('.side').innerHTML = html\`` (drop `${SPRITE}`; the rest of `shell()` is unchanged).

Why this and not a login-only injector: it is the smallest change that gives `login.html` the sprite (zero edits to `login.js`) and removes the "sprite lives inside the nav" coupling. `login.js:1` already imports from `/static/app.js`.

### `local/webui/static/app.css` (region: line 70 and line 156 only)
- Line 70: `.i { width: 20px; height: 20px; stroke-width: 1.5; flex: none; }` → remove `stroke-width: 1.5;`.
- Line 156: `.badge .i { width: 14px; height: 14px; stroke-width: 2; }` → remove `stroke-width: 2;`.
- Nothing else. `.toast svg` (251, 253) and the color rules (97, 133, 185-190) work with fill as they are.

### Inline SVG → sprite (replace the whole `<svg …>…</svg>` element)
In HTML use `<svg class="i" aria-hidden="true"><use href="#i-NAME"/></svg>` (the form other pages already use). In JS use `${icon('NAME')}`.

| File:line | Now | Becomes |
|---|---|---|
| `index.html:117` | toast check | `#i-check` |
| `devices.html:44` | toast check | `#i-check` |
| `settings.html:27` | send arrow ("Send test") | `#i-send` |
| `settings.html:38` | warning triangle | `#i-warning` |
| `settings.html:50` | toast check | `#i-check` |
| `login.html:14` | warning triangle | `#i-warning` |
| `login.html:16` | lock ("Sign in") | `#i-lock` |
| `login.html:19` | toast check | `#i-check` |
| `projects.html:111` | close X | `#i-close` |
| `projects.html:119` | info circle | `#i-info` |
| `projects.html:145` | toast check | `#i-check` |
| `projects.js:17` | `const WARN = html\`<svg…>\`` | delete the const; its two uses (`projects.js:105`, `:107`) become `${icon('warning')}` |
| `projects.js:97` | close X | `${icon('close')}` |
| `projects.js:99` | send arrow ("Install to phone") | `${icon('send')}` |
| `projects.js:103` | three dots | `${icon('more')}` |
| `projects.js:104` | exclamation circle (check error) | `${icon('error')}` |
| `projects.js:3` | import list | add `icon` |

Keep each `<svg>`'s existing `class` (all are `class="i"`). Do not touch attributes or markup around the `<svg>`.

## 3. Files touched and exact regions (for ownership)

| File | Region owned by icon work | Shared with controls work? |
|---|---|---|
| `local/webui/static/app.js` | lines 113-155 (sprite), line 225 (`${SPRITE}`) | maybe (if controls edit `shell()` markup) |
| `local/webui/static/app.css` | line 70 and line 156, `stroke-width` only | yes |
| `local/webui/static/index.html` | line 117 (toast `<svg>`) only | yes |
| `local/webui/static/projects.html` | lines 111, 119, 145 (`<svg>` elements only) | yes |
| `local/webui/static/projects.js` | lines 3, 17, 97, 99, 103, 104, 105, 107 (`<svg>`/`WARN` only) | yes |
| `local/webui/static/settings.html` | lines 27, 38, 50 | no |
| `local/webui/static/devices.html` | line 44 | no |
| `local/webui/static/login.html` | lines 14, 16, 19 | no |

Not touched: `apps.*`, `schedules.*`, `builds.*`, `index.js`, `settings.js`, `devices.js`, `login.js`, `favicon.svg`, tests.
Suggested order to avoid a shared-file clash: run icon work first (all edits are single-line `<svg>` swaps plus two CSS tokens), or let the controls owner apply the `index.html`/`projects.*`/`app.css` rows from this table.

## 4. Mechanical acceptance (run from `local/webui/static/`)

1. No stroke icons anywhere: `grep -n 'stroke' *.html *.js *.css` → no output.
2. No inline SVG outside the sprite: `grep -n '<svg' *.html *.js | grep -v '<use href="#i-' | grep -v '^app.js:'` → no output; and `grep -c '<svg' app.js` → 2 (sprite container + `icon()` helper).
3. Sprite is all Fluent 20px: `grep -c '<symbol id="i-[a-z-]*" viewBox="0 0 20 20" fill="currentColor"><path d="' app.js` → 33; `grep -c '<symbol' app.js` → 33; `grep -c 'viewBox="0 0 24 24"' *.html *.js` → all 0.
4. Path data matches the fetched files: `grep -o 'd="[^"]*"' ../../../docs/logs/2026-10-26/polish-icons-paths.md | sort -u > /tmp/a; grep -o '<symbol[^>]*><path d="[^"]*"' app.js | grep -o 'd="[^"]*"' | sort -u > /tmp/b; diff /tmp/a /tmp/b` → no output (use a scratch dir inside the project if `/tmp` is blocked).
5. Every referenced icon exists:
   ```sh
   for n in $( (grep -ho '#i-[a-z-]*"' *.html *.js | sed 's/#i-//;s/"//'; grep -ho "icon('[a-z-]*'" *.js | sed "s/icon('//;s/'//"; grep -ho "icon: '[a-z-]*'" *.js | sed "s/icon: '//;s/'//") | sort -u); do grep -q "id=\"i-$n\"" app.js || echo "MISSING $n"; done
   ```
   → no output. (Also covers `DL` names in `index.js:9-18`: info, check, warning, error — all present.)
6. Sprite no longer inside `shell()`: `grep -n 'SPRITE' *.js` → no output.
7. No package or build step: `git status --porcelain` shows no new file under `local/webui/` and no change to any `package.json`, `pyproject.toml` or `Dockerfile`.
8. Python leg green: `python3 scripts/test.py --legs python` (from repo root) → exit 0. `test_js_uses_html_template_only` must stay green (it guards the new `insertAdjacentHTML` call).

## 5. Tests in `local/webui/tests/test_ui_static.py`

No test checks icons, so icon work needs no test change: none of the tests reads `SPRITE`, `<symbol`, `stroke` or inline `<svg`. All current tests keep passing after these edits.
Keep `StaticRulesTest.test_negative_fixture_is_flagged` (22) and `test_js_uses_html_template_only` (28): security rule that the new sprite injection must satisfy.
Deletion of UI-type tests in the batch (`test_pages_have_nav_and_logout` 42, `test_no_dialog_form_min_width` 60, `test_projects_page_uses_project_api` 65) belongs to the controls work and the lead; not part of this plan.

## Uncertain
- Icons shown at `.i-16` (search inputs, `builds.html:32,38`, `apps.html:81,110`, `index.html:104`) and in badges at 14px scale the 20px grid down. Fluent has 16px variants, but adding `-16` symbols is out of scope (YAGNI) unless the user sees blur.
- Visual result not rendered here; the user checks the UI.
