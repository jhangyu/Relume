# Polish batch: web UI preflight (read-only)

Base: `main` at 3abe409 working tree. All paths are under `local/webui/static/` unless stated. Spec: `docs/superpowers/specs/2026-10-26-polish-batch-design.md` (req 2, 4, 5, 6, 7).

## 1. Icon sprite (`app.js:115-155`, injected by `shell()` at `app.js:225`)

Every symbol is `viewBox 0 0 24 24 fill=none stroke=currentColor` with hand-drawn stroke paths. `.i` (`app.css:70`) sets 20px and `stroke-width:1.5` (the stroke width only matters for stroke icons; official icons are filled shapes, so the symbol attributes must become `fill=currentColor`, no stroke).
Helper: `icon(name, cls)` at `app.js:157` emits `<svg class="i cls"><use href="#i-name"/>`. `STATUS` (`app.js:104-112`) also picks icon names by key: sync, history, error, sparkle, link, check.

Official name = `assets/<Folder Name>/SVG/ic_fluent_<name>_<16|20|24>_regular.svg`. I checked every "official name" below with a raw-file request: all return HTTP 200 (20_regular).

| Sprite id (app.js line) | Used at | Official Fluent name |
|---|---|---|
| store (117) | nav `app.js:229`, `index.js:94`, `schedules.html:47` | store_microsoft |
| apps (118) | nav `app.js:230` | apps |
| schedule (119) | nav `app.js:231` | clock |
| builds (120) | nav `app.js:233`, `builds.js:138` | window_console |
| devices (121) | nav `app.js:234`, `schedules.js:98` | phone |
| follow (122) | nav `app.js:235`, `apps.js:341` | branch_fork (or branch) |
| settings (123) | nav `app.js:236`, `apps.js:82` | settings |
| search (124) | `index.html:81`, `apps.html:81,110`, `builds.html:38`, `index.js:95,165`, `apps.js:351`, `schedules.html:49` | search |
| sort (125) | no use found | arrow_sort (delete if unused) |
| filter (126) | no use found | filter (delete if unused) |
| chevron-down (127) | no use found | chevron_down (delete if unused) |
| chevron-right (128) | no use found | chevron_right (delete if unused) |
| back (129) | no use found | arrow_left (delete if unused) |
| close (130) | `app.js:239`, `apps.html:103`, `builds.html:32`, `index.js:221` | dismiss |
| add (131) | `apps.html:83`, `schedules.html:56` | add |
| check (132) | `STATUS`, toasts (`apps.html:127`, `builds.html:43`, `schedules.html:70`), `index.js:55,81,223,229`, `apps.html:108` | checkmark |
| sync (133) | `apps.js:79`, `apps.html:82`, `index.html:82`, `index.js:94` | arrow_sync |
| rebuild (134) | `apps.js:80`, `builds.js:142`, `apps.html:123`, `schedules.html:50` | wrench |
| download (135) | `apps.js:81,350`, `builds.html:31`, `builds.js:139-140` | arrow_download |
| link (136) | `apps.js:351,353`, `index.js:210,212` | link |
| paste (137) | `index.js:210` | clipboard_paste |
| warning (138) | `apps.js:233-234`, `index.js:37` | warning |
| error (139) | `index.js:37,134` | error_circle |
| info (140) | `apps.js:232`, `index.js:162-163` | info |
| star (141) | `index.js:28,55,152` | star |
| sparkle (142) | `STATUS` + `index.js:53` | sparkle |
| more (143) | no use found (pages draw their own dots, see §2) | more_horizontal |
| play (144) | `schedules.html:45`, `schedules.js:102` | play |
| edit (145) | `apps.js:357`, `schedules.js:103` | edit |
| delete (146) | `index.js:135`, `schedules.js:104` | delete |
| history (147) | `STATUS` queued/not_built | history |
| package (148) | `apps.js:218,305,334`, `schedules.html:48` | box |
| github (149) | `index.html:82-83,104` | NO official match (Fluent has no brand icons). Pick a neutral one: branch_fork or code. Needs architect decision. |
| bell (150) | `schedules.html:51` | alert |
| shield (151) | no use found | shield_checkmark (delete if unused) |
| pin (152) | `apps.js:318` | pin |
| menu (153) | all pages' header menu button (e.g. `apps.html:77`) | navigation |

Dynamic names (not greppable by literal) exist through `icon(s.icon)` from `STATUS`; also check `grep "icon(" *.js` for template-computed names before deleting "no use found" symbols.

**Broken rebuild wrench (`app.js:134`)**: the hand-drawn path is a closed stroke outline `M14.7 6.3a4 4 0 0 0 5 5l-8.9 8.9a2.1 2.1 0 0 1-3-3l8.9-8.9a4 4 0 0 0-2-2Z` plus a second `M14.7 6.3 17 4` segment. The head is a single 4-radius arc drawn as an outline, and the extra segment sticks out as a spike from the head. With `stroke-width:1.5` at 20px the shape reads as a blob with a tail, not a wrench. Cause = geometry drawn by hand, not a CSS bug. I did not render it (no browser run), so the exact visual is from reading the path. Replacing it with `wrench` fixes it.

## 2. Inline `<svg>` outside the sprite

All of these are icons (none is a logo or illustration), all hand-drawn with `fill=none stroke=currentColor stroke-width=2`. Replace each with `icon('name')`.

| Location | Shows | Sprite name |
|---|---|---|
| `index.html:117`, `devices.html:44`, `settings.html:50`, `projects.html:145`, `login.html:19` (toasts) | check | check (`apps/builds/schedules.html` toasts already use the sprite) |
| `settings.html:27` | send arrow, "Send test" button | send (not in sprite, add) |
| `settings.html:38`, `login.html:14`, `projects.js:17` (const `WARN`) | warning triangle | warning |
| `login.html:16` | lock, "Sign in" button | lock_closed (add) |
| `projects.html:111`, `projects.js:97` | close X | close |
| `projects.html:119`, `projects.js:104` | info / error circle | info / error |
| `projects.js:99` | send arrow, "Install to phone" | send |
| `projects.js:103` | three dots (filled), "More actions" | more |

Problem: `login.html` has no `shell()` call, so the sprite is not injected there. The login toast, warning and lock icons need the sprite injected on that page, or `login.js` must call a small injector. (`login.js` imports from `app.js`? not checked: confirm before planning.)
Non-icon SVGs: `favicon.svg` (file, keep). The logo is a text "R" tile (`app.css:91`, `app.js:227`), not an SVG.

## 3. Getting official path data without a package

Raw files are fetchable (checked with curl, HTTP 200):
- `https://raw.githubusercontent.com/microsoft/fluentui-system-icons/main/assets/Wrench/SVG/ic_fluent_wrench_24_regular.svg`
- `https://raw.githubusercontent.com/microsoft/fluentui-system-icons/main/assets/Search/SVG/ic_fluent_search_20_regular.svg`
- Folder names with spaces use `%20`: `.../assets/Store%20Microsoft/SVG/ic_fluent_store_microsoft_20_regular.svg` (200).
- Directory listing: `https://api.github.com/repos/microsoft/fluentui-system-icons/contents/assets/Wrench/SVG` (200, lists 16/20/24 × regular/filled/color).

File shape: `<svg width viewBox="0 0 N N" fill="none"><path d="..." fill="#212121"/></svg>`. Each size has its own viewBox (`0 0 16 16`, `0 0 20 20`, `0 0 24 24`) and the path is drawn for that grid, so a symbol must keep the viewBox of the file it came from. Some icons have several `<path>` elements (rewrite `fill="#212121"` to `currentColor` or drop it and set `fill=currentColor` on `<symbol>`).
Size fit: default `.i` is 20px (`app.css:70`), `.i-16` 16px (`:71`), `.i-24` 24px (`:72`). Easiest: use the `_20_regular` path with `viewBox 0 0 20 20` for every symbol (matches the default). Icons shown at `.i-16` (search inputs, `builds.html:32,38`, `apps.html:81,110`, `index.html:104`) will scale down; if exact 16 grid is wanted, add separate `-16` symbols. Remove `stroke-width:1.5` from `.i` and `.badge .i` stroke-width (`app.css:156`), `.toast svg` etc. are fill-compatible.

## 4. Shared component classes in `app.css`

- Button: `.btn` (`:107`; 32px, radius 4px, min-width 96px) with `.primary` `.subtle` `.large` `.small` `.circular` (`:110-118`) and `[disabled]` (`:117`). `.btn.circular` = fully rounded.
- Icon button: `.icon-btn` (`:119`; 36px square, radius 4px) with `.outline` `[disabled]`. There is NO `.small` rule for `.icon-btn` (pages use `icon-btn small` but it does nothing).
- Tooltip: `[data-tip]` (`:125`).
- Input wrapper: `.input` (`:129`; a flex box with border, height 32px, radius 4px; the inner `<input>/<select>` is borderless via `.input input` `:131`), `.input.large` (40px, radius 6px `:134`), `.input.search` (circular radius, padding 0 16px `:135`). `.input:focus-within` (`:130`) turns the bottom border 2px brand blue.
- Select: `select.dropdown` (`:139`; own border/height 32px/radius 4px, `width:100%`) inside wrapper `.select` (`:140`; draws the chevron via `::after`).
- Field: `.field` `.label` `.hint` (`:136-138`).
- Toggles: `.switch` (`:144`), checkbox/radio base (`:152`), `.pill` (toggle chip, circular, 32px, `:193`), `.tab`/`.tablist` (`:221-224`).
- Badges/status: `.badge` (+`ok warn err brand lav gold`), `.origin`, `.msgbar` (+`warn err info ok`), `.dot`, `.progress`.
- Surfaces: `.card`, `.card-pad`, `.card-head`, `.drawer*`, `dialog`, `.dlg-actions`, `.menu` / `.dd`, `.table`, `.toast`.
- Not in `app.css` but used everywhere: `.alert` (see §6), `.seg`.

## 5. Controls that do not use shared classes, with cause

**Apps search + two buttons (`apps.html:81-83`)**
- Search: `<label class="input search hide-m" style="width:260px">`. It is the shared class, so the defect comes from the shared CSS + pill shape: `.input.search` is `border-radius:10000px` (`app.css:135`) while `.input:focus-within` (`:130`) switches `border-bottom` to a 2px brand-blue line and drops the bottom padding. On a fully rounded box that blue bottom border follows the curve and looks like the lower half is blue. Probable cause of "half blue". I did not see it in a browser; low-confidence on the exact look. Also `apps.html:57` `.input:invalid{border-bottom-color:red}` is a page override of the same border.
- Buttons: `#checkall` and the "Add from Marketplace" `<a class="btn primary">` carry inline `style="min-width:0"` (and `text-decoration:none` on the link). `.btn` radius is 4px (`:107`) while the search is a pill, and below 900px the text spans are `hide-m` so only the 20px icon remains: 12px padding + `min-width:0` = a small rounded-square. Fix direction: `btn circular` for both, drop inline styles, use one icon-only variant class if wanted.
- Row action buttons (`apps.js:79-82`) are `.icon-btn` 36px, radius 4px (square), override `.acts .icon-btn.outline` in `apps.html:30`.

**Marketplace (`index.html`)**
- Search `:81` is `.input.search.large.q-wrap` (40px high, pill) and `.q-wrap{width:min(420px,40vw)}` is page CSS (`index.html` style).
- "Refresh catalog" `:82` and "Add custom source" `:83`: `.btn` + inline `style="min-width:0"`; 32px high, 4px radius next to a 40px high pill search, so heights and corners do not match.
- "All origins" `:92` and "Most bundles" `:93`: `.select > select.dropdown` with inline `style="width:168px"` / `"width:176px"`; 32px high, 4px radius. The category chips beside them are `.pill` (circular), so selects look square next to round chips. No `.dropdown.circular`-like variant exists.
- Other page-only styles in the `index.html` `<style>` block: `.feature .chip-w`, `.toolbar`, `.cats`. `index.js:221` "Close app details" is `btn subtle` with inline `justify-content:flex-start;min-width:0;padding:0`.

**Schedules "Repeat" and "At" (`schedules.js:22-24`)**
- Repeat: `.field > .select > select.dropdown` with inline `style="width:140px"`. At: `<span class="input" style="width:120px"><input type="time">`. "Every (hours)": inline `width:100px`. Retries (`schedules.html:63`): inline `width:90px`. So widths are inline per control, and the select (`.dropdown`, own border) and the input (`.input` wrapper, border on span) are two different constructions that share neither height rule nor focus rule: `.input:focus-within` blue bottom line does not apply to `select.dropdown`.
- Segmented "Run every" control `.seg` (`schedules.html:6-11`) is a page-local copy, and a different copy exists in `projects.html:8` (28px high, different background). Two diverging `.seg` definitions, neither in `app.css`.
- "On days" checkboxes are bare `label.row` + checkbox (`schedules.js:25`).

**Followed projects (`projects.js:99,102`)**
- "Install to phone": `<summary class="btn primary small">`; "Check now": `<button class="btn small">`.
- Padding problem cause: `.btn.small` (`app.css:116`) is `height:24px; padding:0 8px; font-size:12px; min-width:64px; radius 2px`, but the leading icon is still a `.i` of 20px (the 12px small font does not shrink icons; no `.btn.small .i` rule) and the hand-drawn send icon has no size class, so a 20px icon sits in a 24px, 8px-padded button: it fills the height and crowds the label. "Check now" has no icon so it looks short next to it. `summary` is also not a `button` (UA summary box model, plus `.dd>summary{list-style:none}` at `app.css:256`).
- Toolbar filter `projects.html:100`: `<input class="input">` directly on the `<input>` (not the wrapper pattern), and `.toolbar .input{height:2rem;max-width:240px}` override at `projects.html:14`. Bare `.input` on an `<input>` loses the inner-input resets from `.input input`.
- `icon-btn small` (`projects.js:97,103`, `projects.html:111`) has no matching rule: renders 36px.

## 6. Other hand-made controls

- `.alert` (message box) is defined in the page `<style>` of `projects.html`, `settings.html`, `devices.html`, and `login.html` (4 copies); `app.css` has `.msgbar` instead (`:183`) that the other pages use.
- `builds.html:38` search: `<label class="input search" style="width:240px">`; `builds.html:32,41` `btn small`.
- `devices.html:36` filter select `class="dropdown" style="width:auto"` with no `.select` wrapper (so no chevron); `devices.html:14` `.btn.subtle:first-child{margin-left:-12px}` negative-margin hack; `devices.js:44` `style="margin-left:auto"`, `devices.js:64,76` `btn small`.
- Pair/OTP digit inputs `devices.html:33,38`: `.otp input{width:40px;...}` page CSS (also copied into `settings.html`).
- `settings.html:29,30,37`: inline widths on `.input` (`style="width:110px"` etc.). `settings.html` `.alist li>.btn` rules (`:13`), `settings.js:148` `btn small danger` (no `.btn.danger` rule exists in `app.css`; `.danger` text color is a page rule in builds, settings, devices, projects).
- `apps.js:250` textarea `class="input mono"` with inline `height:auto;padding:6px 10px`; `apps.js:256` `<input type=color>` with inline size; `apps.js:335` `.chip` and `apps.html:55` `.chips` are page-only; `apps.js:352` `btn sm` (no such class: `.sm` is a font-size utility).
- Menu items inside `.menu` (`projects.js:100,103`): plain `<button>` with inline `style="opacity:.5"` for disabled instead of the `[disabled]` style.
- `builds.html:39` and `index.js:75` `.pill` filter chips are shared (OK). `.feature`, `.app`, `.bundle`, `.bopt`, `.mode`, `.rcard` (radio cards) are page-only card-buttons (not form controls; likely out of scope).
- Inline `style="..."` counts (attribute occurrences per file): apps.html 6, index.html 12, projects.html 13, schedules.html 10, settings.html 15, devices.html 8, login.html 7, builds.html 4, apps.js 11, index.js 11, projects.js 13, schedules.js 6, builds.js 4, devices.js 4, app.js 4.

## 7. Existing tests touching static UI

Only one file reads the static folder: `local/webui/tests/test_ui_static.py` (executed through `scripts/test.py` python leg). Other test files mention `.html` only as URL text or fixtures (`test_device_notify.py:97,111,117`, `test_apklocate.py`), and `test_core_router.py:145-149` mounts a temp static dir to test serving of `icon-192.png` and `manifest.webmanifest` (backend serving test, keep). `scripts/` has no UI test.

`test_ui_static.py` tests (line):
- `StaticRulesTest.test_negative_fixture_is_flagged` (22) and `test_js_uses_html_template_only` (28): XSS-safety scan of `*.js` (innerHTML must use `html\`\``). Security rule; not layout/icon. Keep (they will still pass).
- `test_no_network_refs_or_mockup_names` (35): no external URLs; keep.
- `test_pages_have_nav_and_logout` (42): checks nav hrefs in `app.js` and `<aside class="side">` + `id="toast"` in each page (wiring/layout). Candidate to delete.
- `test_every_page_links_manifest` (54): manifest link per page (asserts exactly 8 pages). Wiring; candidate to delete.
- `test_no_dialog_form_min_width` (60): style rule for dialog forms (style test). Delete.
- `test_projects_page_uses_project_api` (65): greps `projects.js` for API strings (wiring). Delete.
- `test_settings_lists_signer_event` (70): greps settings html/js (wiring). Delete.
- `test_no_dead_dependency_model_leftovers` (74): dead-term grep. Keep or delete, not touched by this batch.
- `PageIdsTest.test_every_id_a_page_script_looks_up_exists_in_its_html` (82): each `$('#id')` in a page script exists in its html. Wiring that guards exactly the kind of break this batch can cause when markup is replaced; judgment call.
- `PageIdsTest.test_builds_page_has_no_mockup_leftovers_and_streams_the_log` (93): wiring. Delete (builds page not in this batch, so optional).
- `StaticServeTest.test_every_page_and_asset` (101): serves every page and each `/static/...` reference with 200 (auth redirect + asset reachability). Backend serving; keep.

Spec rule: delete UI-type tests in areas the batch changes. Strictly in-batch (icons/controls/styles): `test_no_dialog_form_min_width`, `test_pages_have_nav_and_logout`, `test_projects_page_uses_project_api`. The lead decides the rest.

## Uncertain

- Visual causes (half-blue search, square buttons, wrench look, cramped small buttons) are derived by reading the CSS/markup, not from a browser render.
- Which "no use found" sprite symbols are really unused (dynamic `icon(...)` names): confirm before deletion.
- Whether `login.js` can call a sprite injector (login has no `shell()`).
- Fluent folder/file names were confirmed to exist, but visual fit of each choice (store_microsoft, window_console, branch_fork for GitHub) is a taste call.
