# Polish batch: web controls plan (spec requirements 2, 5, 6, 7)

Base: `main` at 3abe409 working tree. Paths are under `local/webui/static/` unless stated. Inputs: spec `docs/superpowers/specs/2026-10-26-polish-batch-design.md`, contract `docs/logs/2026-10-26/polish-contract.md`, preflight `docs/logs/2026-10-26/polish-preflight-web.md` §4–7. Line numbers are at 3abe409; match on the quoted text, not the number.

## 0. Control standard (decision)

- Buttons, icon-only buttons in toolbars, text inputs, search boxes and selects: Fluent 2 **medium, shape "rounded"** = height 32px, radius 4px (`--borderRadiusMedium`), white fill, 1px `--colorNeutralStroke1` border, bottom border `--colorNeutralStrokeAccessible`.
- Focus on inputs and selects: one shared rule, a 2px brand line at the bottom made of `border-bottom-color` + 1px inset shadow. No padding change, so nothing moves on focus. The global outline is switched off for these two, because the line is the focus indicator.
- Search box = the same `.input` box with a search icon. Pill shape removed. "Light fill" = the white fill of the Fluent outline input, over the whole box. I did not choose the grey "filled-darker" look: on the `#fafafa` header it reads as no box at all.
- Toolbar buttons whose label hides below 900px become a 32×32 icon-only square (Fluent icon-only medium button). This is done by one shared rule, not by inline `min-width:0`.
- Circular shape is kept only for filter chips (`.pill`) and badges. These are Fluent tag/toggle chips, not toolbar controls. The Marketplace category chips stay circular next to the 4px selects. This is the normal Fluent pairing.
- Followed projects "Install to phone" and "Check now" move from small (24px) to medium (32px), like every other action button. This fixes the 20px icon in a 24px button.

## 1. Shared classes in `app.css` after the change

Only lines 106–141 (Button + Inputs block), the `@media (max-width: 900px)` block at 226–235, and the "Relume additions" part after line 253 change. **Do not touch** line 70–72 (`.i`), 156 (`.badge .i`), 249–253 (`.toast`): the icon implementer owns those.

Change:
- `:107` `.btn` — no change. `:108` `.btn:hover` — add `text-decoration: none;` (a link styled as `.btn` must not underline on hover; this replaces the inline `text-decoration:none` on `apps.html:83`).
- `:122` `.icon-btn.outline` — becomes the brand-tinted outline (moved from `apps.html:28`): `border-color: var(--colorBrandStroke2); background: var(--colorNeutralBackground1); color: var(--colorBrandForeground1);`. The two users (Rebuild when an update is available, `apps.js:80`; Locate APKMirror, `apps.js:351`) are both call-to-action buttons. No markup change.
- `:130` `.input:focus-within` → replace the whole rule with
  `.input:focus-within, select.dropdown:focus { outline: 0; border-bottom-color: var(--colorBrandForeground1); box-shadow: inset 0 -1px 0 var(--colorBrandForeground1); }`
- `:134` `.input.large` — delete (its only user, the Marketplace search, becomes medium).
- `:135` `.input.search` → `.input.search { width: min(320px, 40vw); }` (delete the circular radius and the `0 16px` padding).

Add (after `:141`, in the Inputs block):
```css
.input:invalid, .input[aria-invalid=true] { border-color: var(--colorStatusDangerForeground1); }
textarea.input { height: auto; padding: 6px 10px; }
input[type=color] { width: 44px; height: 32px; padding: 2px; border: 1px solid var(--colorNeutralStroke1); border-radius: var(--borderRadiusMedium); background: var(--colorNeutralBackground1); }
.input.narrow, .select.narrow { width: 128px; }
.otp { display: flex; gap: 8px; }
.otp input { width: 40px; text-align: center; padding: 0; }
/* Segmented choice (radio group drawn as joined toggle buttons), 32px outer height */
.seg { justify-self: start; display: inline-flex; flex-wrap: wrap; gap: 2px; padding: 2px; border-radius: var(--borderRadiusMedium); background: var(--colorNeutralBackground3); box-shadow: inset 0 0 0 1px var(--colorNeutralStroke2); }
.seg label { position: relative; }
.seg input { position: absolute; opacity: 0; inset: 0; margin: 0; cursor: pointer; }
.seg span { display: inline-flex; align-items: center; height: 28px; padding: 0 12px; border-radius: var(--borderRadiusMedium); font-weight: 600; color: var(--colorNeutralForeground2); }
.seg input:checked + span { background: var(--colorBrandBackground); color: #fff; }
.seg input:focus-visible + span { outline: 2px solid var(--colorNeutralForeground1); outline-offset: 2px; }
```
Add in the 900px media block (`:226–235`):
```css
  .header .btn:has(> .hide-m) { min-width: 0; width: 32px; padding: 0; }
  .input.search { width: auto; flex: 1; }
```
Add after `.menu a:hover, .menu button:hover` (`:260`):
```css
.menu button[disabled] { color: var(--colorNeutralForegroundDisabled); background: none; cursor: not-allowed; }
.danger { color: var(--colorStatusDangerForeground1); }
```
Delete nothing else. `.btn.small`, `.btn.large`, `select.dropdown`, `.select`, `.pill`, `.msgbar` stay as they are.

Page-local copies and where they go:
| Page copy | Goes to |
|---|---|
| `.alert` (projects, settings, devices, login) | deleted; markup uses `.msgbar` (`warn`/`err`/`info`) |
| `.seg` (schedules.html:6–11, projects.html:8) | deleted; shared `.seg` above |
| `.otp` (devices, settings, projects) | deleted; shared `.otp` (settings and projects copies have no user) |
| `.danger` (builds, settings, devices, projects) | deleted; shared `.danger` |
| `.q-wrap` (index.html:22, :68) | deleted; shared `.input.search` width |
| `.toolbar .input` (projects.html:14 and inside the 768px block at :91) | deleted; shared `.input.search` |
| `.rx[aria-invalid=true]` (projects.html:78) | deleted; shared `.input[aria-invalid=true]` |
| `.input:invalid` (apps.html:57) | deleted; shared |
| `.acts .icon-btn.outline` (apps.html:28) | deleted; shared `.icon-btn.outline` |
| `.chips`, `.chip` (apps.html:55–56) | deleted; markup uses `.row wrap` + `.pill` |
| `.card .dlg-actions .btn.subtle:first-child/last-child` negative margins (devices.html:14) | deleted, no replacement |

## 2. Markup edits per page

Notation: "→" = replace the quoted text with the new text. Keep all ids, `data-*` and aria attributes unless stated.

### apps.html (req 5)
- `:28` delete the `.acts .icon-btn.outline {...}` line. `:55–57` delete the `.chips`, `.chip`, `.input:invalid` lines.
- `:81` `<label class="input search hide-m" style="width:260px">` → `<label class="input search hide-m">`.
- `:82` → `<button class="btn" type="button" id="checkall" aria-label="Check all for updates" title="Check all for updates"><svg class="i" aria-hidden="true"><use href="#i-sync"/></svg><span class="hide-m">Check all for updates</span></button>` (inline style and the duplicate `sr-only` span removed; the screen reader read the label twice on desktop).
- `:83` → `<a class="btn primary" href="/" aria-label="Add from Marketplace" title="Add from Marketplace"><svg class="i" aria-hidden="true"><use href="#i-add"/></svg><span class="hide-m">Add from Marketplace</span></a>`.

### apps.js (handoff, see §3: this file has another owner)
- `:250` textarea: delete ` style="height:auto;padding:6px 10px"` (shared `textarea.input`).
- `:256` colour input: delete ` style="width:2.75rem;height:2rem;padding:2px"` (shared `input[type=color]`).
- `:335` `<div class="chips">` → `<div class="row wrap">`; `<label class="chip">` → `<label class="pill">`.

### index.html (req 6)
- `<style>`: delete `.q-wrap { ... }` (`:22`) and `.q-wrap { width: auto; flex: 1; }` (inside the 900px block, `:68`).
- `:81` `class="input search large q-wrap"` → `class="input search"`; the search icon stays `class="i"` → make it `class="i i-16"` like the other search boxes.
- `:82` and `:83` delete ` style="min-width:0"` (shared mobile rule makes them icon squares).
- `:91` delete ` style="width:168px"`; `:92` delete ` style="width:176px"` (the selects size to their longest option).
- `:117` toast: hand-drawn check SVG → `<svg class="i" aria-hidden="true"><use href="#i-check"/></svg>` (only if the lead gives the inline-SVG swap to this slice, see open decision 1).

### index.js (req 6)
- `:165` `<label class="input grow" style="min-width:180px;max-width:320px">` → `<label class="input search">`.
- `:221` `<button class="btn subtle" type="button" data-act="close" style="justify-content:flex-start;min-width:0;padding:0" aria-label="Close app details">${icon('close')}Close</button>` → wrap it in `<div>` and drop the style: `<div><button class="btn subtle" type="button" data-act="close" aria-label="Close app details">${icon('close')}Close</button></div>`. The wrapper stops the flex column from stretching the button.

### schedules.html (req 7)
- `<style>`: delete the six `.seg ...` lines (`:6–11`).
- `:64` `<span class="input" style="width:90px">` → `<span class="input narrow">`.

### schedules.js (req 7)
- `:22` `<span class="select"><select class="dropdown" id="${p}-f" style="width:140px">` → `<span class="select narrow"><select class="dropdown" id="${p}-f">`.
- `:23` `<span class="input" style="width:100px">` → `<span class="input narrow">`.
- `:24` `<span class="input" style="width:120px">` → `<span class="input narrow">`.
Result: Repeat, Every (hours), At and Retries are all 128×32, 4px, same border, same focus line.

### projects.html (req 2 + cleanup)
- `<style>`: delete `.otp...` (`:7`), `.seg...` (`:8`), `.danger` (`:9`), `.alert...` (`:10`), `.toolbar .input` (`:14`), `.rx[aria-invalid=true]` (`:78`); in the 768px line (`:91`) delete the `.toolbar .input{max-width:none;flex:1 1 100%}` part only.
- `:95` `#addBtn`: delete ` style="min-width:0"`.
- `:100` `<input id="q" class="input" type="search" placeholder="Filter by name or owner/repo" aria-label="Filter projects">` → `<label class="input search"><svg class="i i-16" aria-hidden="true"><use href="#i-search"/></svg><input id="q" type="search" placeholder="Filter by name or owner/repo" aria-label="Filter projects"></label>`. `projects.js:175` reads `$('#q')`: the id stays on the `<input>`.
- `:111` `class="icon-btn small"` → `class="icon-btn"` (`.small` has no rule; it renders 36px today, so no visual change).
- `:119` `<div class="alert" style="background:...">` → `<div class="msgbar info">`.

### projects.js (req 2)
- `:97` `class="icon-btn small pane-close"` → `class="icon-btn pane-close"`.
- `:99` `<summary class="btn primary small">` → `<summary class="btn primary">`.
- `:100` delete `style="opacity:.5"` (keep `disabled`; shared `.menu button[disabled]`).
- `:102` `<button class="btn small" type="button" data-check>` → `<button class="btn" type="button" data-check>`.
- `:103` `<summary class="small icon-btn"` → `<summary class="icon-btn"`; delete `style="opacity:.55"` on the disabled Unfollow button.
- `:104`, `:107` `class="alert err"` → `class="msgbar err"`; `:105` `class="alert warn"` → `class="msgbar warn"`. `:104` `btn small` "Save & check" stays.
Result: Install to phone and Check now are 32px medium buttons; the 20px send icon has 6px clearance top and bottom and 12px side padding.

### settings.html (cleanup)
- `<style>`: delete `.otp...` (`:7`), `.danger` (`:9`), `.alert...` (`:10`); in `:13` replace `.alist li>.btn,.alist li>.badge{flex:none}` with `.alist li>:last-child{flex:none}` (same effect, no control selector).
- `:29` `style="width:110px"` → add `narrow` to the class: `class="input narrow"`. `:30` same for `style="width:130px"`.
- `:37` `style="flex:1 1 240px;min-width:0"` → add `grow` to the class: `class="input mono grow"`.
- `:38` `class="alert warn"` → `class="msgbar warn"`.

### devices.html (cleanup)
- `<style>`: delete `.otp...` (`:7`), `.danger` (`:9`), `.alert...` (`:10`), the whole `:14` line (negative margins).
- `:36` `<label class="select"><select class="dropdown" id="of" style="width:auto" ...>` → delete ` style="width:auto"`. (The `.select` wrapper is already there; the preflight's "no wrapper" note is wrong.)

### devices.js (cleanup)
- `:31` `<a class="btn subtle danger" href="/settings.html" style="margin-left:auto">` → put `<span class="grow"></span>` before the link and delete the style.
- `:44` same for the `data-unpair` button.
- `:40` `class="alert warn"` → `class="msgbar warn"`.

### login.html (cleanup)
- `<style>`: delete the `.alert...` rule (keep `.auth`).
- `:12` password box → `<span class="input"><input id="p" type="password" autocomplete="current-password" required aria-describedby="err ph"><button type="button" class="btn subtle small" id="pwt" aria-controls="p" aria-pressed="false">Show</button></span>` (delete the `position:relative` div and both inline styles; the shared `.input input` reset makes the inner field borderless).
- `:14` `class="alert err"` → `class="msgbar err"`.
- `:16` `<button class="btn primary" id="go" style="width:100%;height:2.5rem">` → `<button class="btn primary large" id="go">` (the `.stack` grid already stretches it to full width).

### builds.html (cleanup)
- `<style>`: delete `.danger` (`:16`); `:18` `.table td:last-child .dd,.table td:last-child .icon-btn{vertical-align:middle}` → `.table td:last-child>*{vertical-align:middle}`.
- `:38` `<label class="input search" style="width:240px">` → `<label class="input search">`.

Out of scope (not form controls, listed so nobody widens the batch): card-buttons `.feature`, `.app`, `.bundle`, `.bopt`, `.mode`, `.rcard`, `.rowbtn`, `.sum`; `button.link` in apps.html; `.chip-w` on feature cards; layout inline styles on non-control elements (`div`, `p`, `section`, `fieldset`).

## 3. Files touched and slices

| File | Region | Slice |
|---|---|---|
| `app.css` | 106–141, 226–235, after 253 only (not 70–72, 156, 249–253) | 1 |
| `apps.html` | `<style>` lines 28, 55–57; header 81–83 | 1 |
| `index.html` | `<style>` 22, 68; header 81–83; toolbar 91–92; Load more line 97 (§6); toast 117 (open decision 1) | 1 |
| `index.js` | 165, 221, 352 (§6) | 1 |
| `schedules.html` | `<style>` 6–11; 64 | 1 |
| `schedules.js` | 22–24 | 1 |
| `projects.html`, `projects.js` | see §2 | 2 |
| `settings.html`, `devices.html`, `devices.js`, `login.html`, `builds.html` | see §2 | 2 |
| `apps.js` | 250, 256, 335 | handoff to the apps.js owner (open decision 2) |
| `local/webui/tests/test_ui_static.py` | §5 | 2 (open decision 3) |

- **Slice 1, "shared classes + Apps, Marketplace, Schedules" (req 5, 6, 7):** app.css, apps.html, index.html, index.js, schedules.html, schedules.js.
- **Slice 2, "Followed projects + other pages" (req 2 + rule cleanup):** projects.html, projects.js, settings.html, devices.html, devices.js, login.html, builds.html, test_ui_static.py.
- No file is in both slices. Both can run in parallel in round 1: slice 2 only uses class names that slice 1 adds, and the final state is the same in any order.
- `app.css` is shared with the icon implementer in the same round. Regions do not overlap. If an Edit fails because the file changed, Read again and redo the edit; never rewrite the whole file.

## 4. Mechanical acceptance (run from repo root after both slices and the apps.js handoff)

```sh
S=local/webui/static
# A. no inline style on a control (two attribute orders); expect no output
grep -nE '<(button|a|input|select|summary|label|span|textarea|div)[^>]*class="[^"]*\b(btn|icon-btn|input|dropdown|select|pill|seg|msgbar)\b[^"]*"[^>]*style=' $S/*.html $S/*.js
grep -nE '<(button|a|input|select|summary|label|span|textarea|div)[^>]*style="[^"]*"[^>]*class="[^"]*\b(btn|icon-btn|input|dropdown|select|pill|seg|msgbar)\b' $S/*.html $S/*.js
grep -nE 'disabled style=|type="color"[^>]*style=' $S/*.js          # expect no output
# B. no page-local control CSS in any page <style>; expect no output
grep -nE '\.(btn|icon-btn|input|dropdown|seg|alert|chips?|otp|danger|q-wrap|pill)([^-a-zA-Z0-9_][^{]*)?\{|aria-invalid' $S/*.html
# C. no dead class tokens; expect no output (".chip-w" on feature cards is not matched on purpose)
grep -nE 'class="([^"]* )?(alert|chip|q-wrap)[" ]|icon-btn small|small icon-btn|input search large' $S/*.html $S/*.js
# D. shared rules exist; expect each count >= 1
for r in '^\.seg ' '^\.otp ' '^\.danger ' '\.input\.narrow' 'select\.dropdown:focus' '\.header \.btn:has' '\.menu button\[disabled\]'; do printf '%s ' "$r"; grep -cE "$r" $S/app.css; done
# E. search box is not a pill; expect no output
grep -nE '\.input\.search[^}]*Circular' $S/app.css
# F. Followed buttons are medium; expect no output
grep -nE 'summary class="btn primary small"|class="btn small" type="button" data-check' $S/projects.js
```
G. Tests: before deleting anything, run `python3 scripts/test.py --legs python` once with the edits in place (the id-lookup test is the one-off check that no `$('#id')` lost its element); then delete the §5 tests and run it again; both green.

## 5. UI-type tests to delete in `local/webui/tests/test_ui_static.py`

Delete (style, layout or wiring tests in pages this batch changes):
- `test_pages_have_nav_and_logout` (`:42`) and the `NAV` constant (`:7`, no other user).
- `test_no_dialog_form_min_width` (`:60`).
- `test_projects_page_uses_project_api` (`:65`).
- `test_settings_lists_signer_event` (`:70`).
- `PageIdsTest` whole class (`:81–97`): `test_every_id_a_page_script_looks_up_exists_in_its_html` (id wiring) and `test_builds_page_has_no_mockup_leftovers_and_streams_the_log` (wiring; builds.html changes).

Keep: `test_negative_fixture_is_flagged`, `test_js_uses_html_template_only` (XSS safety), `test_no_network_refs_or_mockup_names`, `test_no_dead_dependency_model_leftovers`, `StaticServeTest` (serving). `test_every_page_links_manifest` is untouched by controls; the icon plan decides it.
No new tests.

## 6. Marketplace auto-load (spec requirement 9, web part) — slice 1

Today: `index.html:97` `<div style="text-align:center;margin-top:8px"><button class="btn" id="more" type="button" hidden>Load more</button></div>`; `index.js:98` (in `renderGallery`) `$('#more').hidden = S.apps.length >= S.total;`; `index.js:352` click handler `S.page += 1; loadList(false)...`. The page fetch is `loadList(reset)` (`index.js:111–128`); it already drops overtaken responses through `seq`. There is no CSS rule for the button (only the inline style on its wrapper).
Existing bug that the new code must not keep: on a failed load the click handler has already done `S.page += 1`, so the next try skips a page.

Edits:
- `index.html:97` → `<div id="more" aria-hidden="true"></div>` (empty sentinel after the gallery; button, wrapper and inline style deleted). The id stays `more`, so `renderGallery` needs no change.
- `index.js:98` — no change. `#more` is `hidden` at the last page, and a hidden element never intersects, so loading stops at the last page.
- `index.js:352` → replace the click-handler line with:
```js
// Next page when the sentinel after the gallery comes near the viewport.
let paging = false;
const pager = new IntersectionObserver(([e]) => { if (e.isIntersecting) loadMore(); }, { rootMargin: '400px' });
function loadMore() {
  if (paging || S.apps.length >= S.total) return;
  paging = true;
  const want = S.page + 1;
  S.page = want;
  loadList(false).then(() => {
    paging = false;
    pager.unobserve($('#more')); pager.observe($('#more'));   // observe() re-reports: loads again if the sentinel is still in view
  }, err => {
    paging = false;
    if (S.page === want) S.page = want - 1;   // a filter reset in between owns S.page; otherwise retry the same page
    console.error('load more:', err);
    toast(err.message, 'error');
  });
}
pager.observe($('#more'));
```
- Double loads: the `paging` flag. A filter change during a page load still goes through `loadList(true)`; `seq` drops the late page.
- Errors and retry: the error is logged as before, and also shown as a toast, like the existing first-load failure (`index.js:405–408`). The page number goes back to where it was. After a failure the sentinel is not re-observed, so a persistent error does not loop. The retry is to scroll the end of the list out of view and back. That is the same user action as before, a scroll in place of a click.
- No new CSS, no new test. Acceptance: `grep -n 'Load more\|addEventListener(.click.*#more\|\$(.#more.).addEventListener' local/webui/static/index.*` → no output. `grep -c 'IntersectionObserver' local/webui/static/index.js` → 1. The user checks the scrolling in the browser.

index.js regions touched in total (slice 1): 165, 221 (§2) and 352 (this section). index.html: line 97 in addition to §2.

## Open decisions for the lead

1. **Inline SVG icons in pages this plan edits** (projects.html/js, settings.html, devices.html, login.html, index.html toast). The icon plan also edits these lines. One owner per file: recommend the controls slices also swap those SVGs to `<use href="#i-NAME"/>` / `icon('NAME')` (names: check, warning, close, info, error, more, send, lock, sync, add; `projects.js` must then import `icon`). The icon implementer keeps `app.js`, the `.i` region of `app.css`, and the login sprite injection (`login.js`). `send` and `lock` must exist in the sprite.
2. **apps.js** has 3 one-line edits (§2). Recommend the Followed-cards implementer applies them, or slice 1 applies them after that implementer reports.
3. **test_ui_static.py**: the icon plan may delete tests too. Recommend one owner (slice 2) deletes the union.
