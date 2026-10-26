# Web UI review against the candidate-2 mockups (round 1)

**Verdict: FAIL**. There are 21 must-fix and 13 minor differences.

The shared token and component CSS matches the mockups. `app.css:1-234` is the mockup `fluent.css` with only `.nav` renamed to `.side`. The inline CSS of the Marketplace, Apps and Schedules pages matches too, apart from small additions. Most of the problems are in markup, in JS formatting, and in the kept pages (Builds, Devices, Settings, Login).

## Method
- Headless Chrome was driven over the DevTools protocol. It ran against a scratch server on a copy of `.claude/tmp/mp-migrate/data`, with login bypassed and motion turned off. Every page and state was captured at 1440 px and 390 px.
- Element geometry and computed styles were dumped for each page and compared with the mockup: widths, heights, padding, gap, font, colour, radius, and the vertical offset of each child from its row centre (dy).
- Tooling: `.claude/tmp/fe-review-web/{serve.py,cdp.mjs,measure.js,mkjobs.py,cmp.py,crop.py}`. Re-run with `python3 serve.py ../mp-migrate/data jobs-1440a.json` (also `-1440b`, `-390a`, `-390b`).
- Output: screenshots in `shots/`, metrics in `m/`, side-by-side crops in `crops/`. All paths below are relative to `.claude/tmp/fe-review-web/`.
- Empty-catalog state: a copy of the database with `store_app` cleared (`data-empty/`), captured in `shots/impl-mp-emptycat-*.png`.

Rows, icon+text pairs, bundle cards, patch rows, switches, table rows, drawer head and filter pills are all centred with dy = 0, the same as the mockup. The alignment defects are the specific items listed below.

---

## Group A: `local/webui/static/app.js` (shared, affects every page)

**A1. must-fix. Dates and relative times come out in the browser locale (Chinese) inside English UI.**
- Mockup: "2 h ago", "Today 14:00", "3 d ago".
- Implementation: "1 分鐘前", "19 小時後", "2026年10月27日 上午9:00", "3 天前", "前天".
- Seen on every page: Marketplace stats and bundle cards, Apps header, Schedules next and last run, Builds, Devices, Settings, Projects, Login.
- Code: `app.js:59` uses `Intl.RelativeTimeFormat([], …)` and `app.js:61` uses `toLocaleString([], …)`. `schedules.js:168` has the same problem.
- Fix: pass `'en'` (the pages are `lang="en"`). This also fixes the 390 px Schedules "Next run" value, which currently wraps onto two lines.
- Screenshots: `crops/impl-top.png`, `shots/impl-sched-1440.png`.

**A2. must-fix. Status badges have the wrong colour and no icon.**
- Mockup:
  - "Update available" is a lavender badge (`.badge.lav`, #3c32a6 on #f0effd) with a sparkle icon. `tokens.md` assigns Lavender to "Update available".
  - Every status badge has a 14 px icon: check, sparkle, error, link, clock.
- Implementation:
  - "Update available" is orange `.badge.warn` (measured colour rgb(188,75,9)).
  - There are no icons. `.st .badge` has no svg child, while the mockup's has `svg w14`.
- Code: `app.js:104-112`. Set `cls: 'lav'` for update_available and add an `icon` field to each entry: up_to_date check, update_available sparkle, failed error, needs_download_url link, not_built and queued history. `apps.js:64` already renders `st.icon`.
- Screenshots: `shots/impl-apps-1440.png` vs `shots/mock-apps-1440.png`.

**A3. must-fix. The status lines at the bottom of the sidebar are misaligned.**
- "Idle · 0 running · 0 queued" starts at x=36. The "Disk …" line below it starts at x=20.
- Cause: the idle `.dot` has no colour class, so it is an invisible 8 px box plus an 8 px gap.
- Code: `app.js:257`. Render the dot only when the worker is busy, or give it the `off` class so it is visible.
- Screenshot: `shots/impl-apps-1440.png`, sidebar bottom left.

**A4. minor. The sidebar footer differs from the mockup.**
- Mockup: the footer is only the avatar, "Admin" and the host.
- Implementation adds:
  - a two-line server status block (the Disk line wraps in monospace);
  - a sign-out button using the "close" (X) icon.
- Code: `app.js:237-239`. Suggestion: keep the status block on one line (shorten it or use `.caption`, not `.mono`). Give sign-out its own icon if one exists; otherwise leave it.

**A5. must-fix. The shared `icon()` helper always draws 20 px icons where the mockup uses 16 px (`i-16`).**
- Measured on the Marketplace:
  - feature kicker sparkle: 20 vs 16;
  - icons inside URL inputs (`.field .input svg`): 20 vs 16, which pushes the placeholder 4 px right (input text x39 vs x35);
  - patch-filter search icon: 20 vs 16;
  - `chip-w` star: 20 vs 16.
- Code: `app.js:157`. Add an optional class argument, e.g. `icon(name, 'i-16')`, and use it at `index.js:51`, `:54`, `:159`, `:204` and `:206`.
- Screenshot: `crops/impl-detail.png` vs `crops/mock-detail.png`.

## Group B: `local/webui/static/index.html` + `index.js` (Marketplace)

**B1. must-fix. The search box collapses on mobile.**
- At 390 px the header search is 86 px wide (mockup 244 px) and the placeholder shows only "S".
- Cause: the two header buttons keep `.btn` min-width 96 px each (mockup buttons are 46 px, `style="min-width:0"`).
- Code: `index.html:76-77`. Add `style="min-width:0"` to `#refresh` and `#add-source`.
- Screenshot: `crops/impl-mp-390-top.png` vs `crops/mock-mp-390-top.png`.

**B2. must-fix. "patch" is pluralised as "patchs".**
- Seen in bundle cards ("52 patchs") and the enable bar ("44 patchs on").
- Code: `index.js:27` `plural()` appends "s". Also affects "catalog entry" (`index.js:37`).
- Fix: use a plural-form argument, e.g. `plural(n, 'patch', 'patches')`.
- Screenshot: `crops/impl-detail.png`.

**B3. must-fix. The featured banners are laid out differently and have no description.**
- Mockup, large card: a left column with kicker, title and description paragraph, and the 96 px tile top-aligned with the kicker (tile top 108 px).
- Implementation, large card:
  - The kicker spans the full width (403 px vs 299 px).
  - The tile sits in a row with the title only, so its top is at 138 px, 30 px lower than the kicker.
  - There is no description `<p>`.
- Small cards also have no description. When "Enabled" is added the chips wrap onto two rows.
- Code: `index.js:48-55`.
- Fix:
  - Use the mockup structure: `row(space-between, align-items:flex-start)` containing `grid(kicker, h2, p)` and the tile.
  - Add `<p>` with `a.description`, or a bundle summary when there is no description.
  - Restore the mockup rule `.feature p { opacity:.95; max-width:34ch }`, which was dropped from `index.html:7-12`.
  - Remove the extra `gap:16px` on `.feature` (`index.html:7`).
- Screenshot: `crops/impl-top.png` vs `crops/mock-top.png`.

**B4. must-fix. The download step has no Paste button.**
- Mockup: the APKMirror field is `.row` with the input and a "Paste" button (icon `paste`), so the input is 660 px wide.
- Implementation: a bare input, 764 px wide, with no Paste action.
- Code: `index.js:203-204`. Wrap the input in `<div class="row"><span class="input grow">…</span><button class="btn" data-act="paste">${icon('paste')}Paste</button></div>` and fill it from `navigator.clipboard.readText()`.
- Screenshot: `crops/impl-detail.png`.

**B5. must-fix. On narrow screens the enable bar squeezes its text into an 89 px column.**
- At 390 px the summary text wraps to 8+ lines beside the button. The mockup has the same flaw, but the result is plainly bad spacing.
- Code: `index.html` `@media (max-width:900px)` block. Add `.enable-bar .grow { flex-basis: 100% }` so the button wraps below the text.
- Screenshot: `crops/enable390.png`.

**B6. minor.** The "All" category pill has no count. Mockup: "All 1,207". Code: `index.js:69`. Append `<span class="n">${S.universe}</span>`.

**B7. minor.** Every official bundle card shows "Morphe" as a caption. The mockup shows the engine only for custom module bundles ("Xposed · LSPatch") and a category tag otherwise. Code: `index.js:144`. Show the engine only when it is not `morphe`.

**B8. minor.** The step-2 title reads "Patches". Mockup: "Patches in {bundle} for {app}". Code: `index.js:218`.

**B9. minor.** The Versions fact reads "1.12.12 (1 version)". Mockup: a range, "1.12.10 – 1.12.12". Code: `index.js:196`.

**B10. minor. Empty-catalog state.**
- With no hero there is a 44 px gap above the stats (the stats keep `margin-top:20px`).
- The empty-state icon is 20 px. Fluent empty states use 48 px or more.
- Code: `index.html:13` and `index.js:88`.
- Screenshot: `shots/impl-mp-emptycat-1440.png`.

## Group C: `local/webui/static/apps.html` + `apps.js` (Apps)

**C1. must-fix. There is extra space above the patch filter in the drawer.**
- The empty `#pnote` takes a grid row, adding a 12 px `.dsec` gap.
- Measured: heading centre to filter top is 36 px in the implementation vs 24 px in the mockup.
- Code: `apps.html:105`. Add `#pnote:empty { display:none }`, or set `hidden` when it is empty.
- Screenshot: `shots/impl-apps-drawer-1440.png` vs `shots/mock-apps-drawer-1440.png`.

**C2. must-fix. The drawer footer overflows at 390 px.**
- "Save and rebuild" is clipped at the right edge. The mockup has the same flaw.
- Code: `app.css:212` `.drawer-foot`. Add `flex-wrap: wrap`.
- Screenshot: `crops/drawer390.png`.

**C3. minor.** The patch list in the drawer is not height-limited: `.pt` is 9,225 px for 110 patches. Suggest `max-height` with scrolling, as the Marketplace `.patch-list` does (360 px). Code: `apps.html` inline CSS for `.pt`.

**C4. minor.** The bundle option meta shows the raw engine id ("v1.48.0 · morphe"). Mockup: "· v1.4.2", with "Xposed, LSPatch" for modules. Code: `apps.js:220`. Map the engine through a label table and omit it for morphe.

**C5. minor.** Dependency rows (`.dep`) have no 32 px icon tile. Mockup: input, tile s32, text. Code: `apps.js:342`.

**C6. minor.**
- The skip link uses `class="sr-only"` instead of `.skip` (`apps.html:67`).
- The toast has no status icon svg (`apps.html:123`); the Marketplace has one (`index.html:111`).

**C7. minor.** The Actions column is 160 px vs 128 px in the mockup (`apps.html` `.arow` grid). The other columns shift left by up to 32 px. This is acceptable for the dropdown wrapper; just confirm it is intended.

## Group D: `local/webui/static/schedules.html` + `schedules.js` (Schedules)

**D1. must-fix. The pipeline step titles are not on one baseline.**
- `.stp` grid rows stretch, so titles sit at different heights: "Refresh catalog" at y=437, "Check chosen bundles" at y=432. Measured `span.strong` heights are 25.3 vs 20.
- The mockup has the same stretch.
- Code: `schedules.html:15`. Add `align-content: start` to `.stp`.
- Screenshot: `shots/impl-sched-1440.png`.

**D2. must-fix. The "Notify on result" label breaks apart on mobile.**
- "(channel set in" / "Settings" / ")" are separate flex items, so at 390 px "Settings )" floats at the far right. There is also a stray space before ")" at 1440 px.
- Code: `schedules.html:41`. Wrap the text and link in one `<span>`.
- Screenshot: `crops/sched390.png`.

**D3. must-fix. The server-time caption wraps in the mobile header.**
- At 390 px "Server time …" takes two lines next to the title. It is not in the mockup.
- Code: `schedules.html:34`. Add `hide-m` to `#srvtime`.
- Screenshot: `crops/sched390.png`.

**D4. must-fix. The OTA section header crushes its caption on mobile.**
- At 390 px the caption becomes a one-word-per-line column, 14 lines tall. The mockup does this too, but the implementation's caption is longer.
- Code: `schedules.html:55` `.section-head`. Add `flex-wrap: wrap` and `.section-head .caption { flex-basis: 100% }` inside the 900 px media query, or `hide-m`.
- Screenshot: `crops/sched390.png`.

**D5. minor.** The pipeline captions are fixed descriptions ("Community feed, ETag checked"). Mockup and spec §7.5: the last run's time and result per step ("11:00 · 297 bundles, ETag unchanged"). Code: `schedules.html:46-50`. This may depend on whether the API gives per-step results.

## Group E: kept pages (`builds.html/js`, `devices.html/js`, `settings.html/js`, `login.html`, `app.css`)

**E1. must-fix. The Builds log panel is dark.**
- Mockup (restyle-sample): a light panel on a white card with coloured level words (fetch/ok/warn).
- Implementation: a #1f1f1f block 1,080 px wide and 420 px tall. The design is light only.
- Code: `app.css:272` `.log`. Use `background: var(--colorNeutralBackground1)` or `--colorNeutralBackground2`, colour `--colorNeutralForeground1`, and the mockup's monospace 12/20 type.
- Screenshot: `shots/impl-builds-1440.png` vs `shots/mock-restyle-1440.png`.

**E2. must-fix. Two filters are native unstyled `<select>`s.**
- Builds "All results" (`builds.html:37`) and Devices "OTA history" (`devices.html:32`) use `select class="input"` and show the system arrow.
- Fix: use `<label class="select"><select class="dropdown">` as the Marketplace does.

**E3. must-fix. The Builds layout does not follow the restyle sample.**
- Mockup:
  - a Queue | live-log split card (320 px queue list on the left, log on the right);
  - History with a search box and All/Succeeded/Failed/Cancelled pills;
  - columns App, Bundle, App version, Started, Duration, Result, Actions (icon buttons).
- Implementation:
  - separate Queue and build-detail cards, with a step bar and a "Cancel" button squeezed into the title row;
  - a native-select filter;
  - columns #, App, Trigger, Result, Started, Duration, Artifact, with a "View log" text link.
- Code: `builds.html:25-40`, `builds.js`. This is a structural redo; the lead decides the scope.

**E4. must-fix. The Settings notification-channel picker is unstyled.**
- The classes `rcard`, `rc` and `ico` are not defined anywhere. The result is a native radio jammed against a coloured "TG"/"DC"/"NT" text box with no gap or padding.
- Code: `settings.html:21`. Restyle as `.bopt`-like cards (`.bundle` / `.mode` pattern) or define the classes.
- Screenshot: `shots/impl-settings-1440.png`.

**E5. must-fix. Card titles wrap when the head has a long caption.**
- Examples: "Phones with the / Relume app" (Settings), "Manual / pairing" (Devices). The title is squeezed by the caption.
- Code: the `.card-head` markup in `settings.html:39` and `devices.html:25`. The h2 rule is at `settings.html:5` and `devices.html:5`. Add `.card-head h2 { white-space: nowrap; flex: none }`.
- Screenshot: `shots/impl-settings-1440.png`, `shots/impl-devices-1440.png`.

**E6. must-fix. The Login password field is not full width.**
- The password input is 238 px; the username input is 332 px. The "Show" button sits outside the input, so the right edges do not line up (792 vs 886 px).
- Code: `login.html:12`. Give the input `width:100%` and position "Show" absolutely inside the right padding, as the existing `padding-right:4.5rem` intends.
- Screenshot: `shots/impl-login-1440.png`.

**E7. minor.** Undefined leftover classes: `cb` (`devices`, `projects`, `login`), `logbar` and `src` (`builds`). Settings "Notify on" legend and Devices "Pairing code" label are indented 2 px from their siblings. Code: `settings.html:25`, `devices.html:25-30`.

---

## Must-fix count by file
| File | must-fix |
|---|---|
| app.js (+schedules.js:168 for A1) | 4 (A1, A2, A3, A5) |
| index.html / index.js | 5 (B1–B5) |
| apps.html / app.css:212 | 2 (C1, C2) |
| schedules.html | 4 (D1–D4) |
| builds / devices / settings / login / app.css:272 | 6 (E1–E6) |
| **Total** | **21** |

## Uncertainties
- The Paste button (B4) and per-step pipeline results (D5): spec §7.3 and §7.5 describe the fields but do not name these elements. I treated the mockup ("used as is") as binding for B4. D5 depends on what data the API provides.
- E3 (Builds layout) is a structural change. Spec §7.6 says "restyle + re-pointed calls", and restyle-sample shows the target layout. The lead should confirm the scope.
- B5, C2, D1 and D4 are also present in the mockup itself. I flagged them because the user asked for proper spacing and alignment, not just a match.
- No app in the data is in the needs-URL state, so the Apps "Add URL / Pick one" drawer state could not be captured (job `impl-apps-url-*` failed with "no Add URL row"). The URL section was checked only inside the normal drawer.
- Below-the-fold app icons look blank in full-page shots. They use `loading="lazy"`, so this is a capture artefact, not a defect. The letter-tile fallback on error exists (`app.js:172`).

---

# Round 2 (HEAD 77d425a, which includes 43f845f, 536254a, 2a236ef, d380a85, b22b1e3)

**Verdict: FAIL**. All 21 round-1 must-fix items are fixed. The regression look at 390 px found 5 new must-fix items, all owned by fix-web-b. fix-web-a has no open must-fix.

Method: the same tooling. All jobs were re-run at 1440 and 390 px (`shots/`, `m/`, `crops/r2-*`). The overflow probe used `jobs-dev.json`. This round also shot Devices, Projects and Login at 390 px.

## Round-1 items
- **A1 (dates in Chinese)**: fixed. Every page shows English dates and relative times. The only Chinese left is the native `<input type=time>` display ("上午09:00"), which follows the browser locale (see minor m6).
- **A2 (status badges)**: fixed. "Update available" is now lavender, rgb(60,50,166), with a 14 px sparkle icon.
- **A3, A4 (sidebar footer)**: fixed. The idle dot is visible, both lines start at x=20, and the Disk line fits on one line.
- **A5 (icon sizes)**: fixed. Kicker, URL-input and filter icons are 16 px, and the input text starts at x=35 as in the mockup.
- **B1 (mobile search box)**: fixed. At 390 px the search box is 186 px, with two 46 px icon buttons. The header looks balanced.
- **B2 (plural)**: fixed ("52 patches", "44 patches on").
- **B3 (featured banners)**: fixed. The big tile is top-aligned with the kicker (top 108 px). Descriptions show: the store text at 390 px, the "N bundles for <category>." fallback at 1440 px. The row scrolls.
- **B4 (Paste button)**: fixed. The URL input is 660 px with a 96 px Paste button, matching the mockup.
- **B5 (enable bar on phones)**: fixed. At 390 px the text is full width (286 px) and the button sits below it.
- **B6–B10**: fixed, except the B6 count format (minor m1).
- **C1 (patch filter gap)**: fixed. Heading to filter is now 24 px.
- **C2 (drawer footer)**: fixed. At 390 px "Save and rebuild" wraps to a second line instead of clipping.
- **C3–C7**: fixed.
- **D1 (pipeline titles)**: fixed. All five step titles sit at y=432.
- **D2 (Notify label)**: fixed. Reads "(channel set in Settings)" on one line.
- **D3 (server time on mobile)**: fixed; hidden on mobile.
- **D4 (OTA section header)**: fixed. The caption gets its own full-width line on mobile.
- **D5 (pipeline results)**: satisfied by the "Last update run" line (Succeeded badge and log link), as decided by the lead.
- **E1 (dark log)**: fixed. The log panel is light (`--colorNeutralBackground2`).
- **E2 (native selects)**: fixed. Both filters are styled dropdowns.
- **E3 (Builds layout)**: fixed. Queue | log split card, History with search and All/Succeeded/Failed/Cancelled pills, the restyle-sample columns, and icon actions. At 390 px the table scrolls inside `.tw`, the same as the mockup.
- **E4 (channel picker)**: fixed. The channels are cards with tiles and radio buttons.
- **E5 (card titles wrapping)**: fixed on Settings and Devices at 1440 px.
- **E6 (Login password field)**: fixed. Username and password inputs are the same width, and "Show" sits inside the field.
- **E7 (leftover classes, 2 px indents)**: fixed.

## Remaining must-fix (all fix-web-b)

**R1. Devices at 390 px is 528 px wide: the whole page scrolls sideways.**
- Measured: `scrollWidth` is 528 against a 390 px viewport. Card right edges, "Revoke in Settings" and "Scanned …" are cut off.
- Cause: `.content { display:grid }` has an implicit `auto` column. The OTA history table's min-content width (512 px) sets that column, even though the table sits inside `overflow:auto`.
- Code: `devices.html:4`. Add `grid-template-columns: minmax(0,1fr)`.
- Screenshot: `crops/r2-devices-login390.png`. Probe output: `m/dev-ovf-390.json`.

**R2. The Login card title wraps at 390 px.**
- "Sign in" breaks into "Sign / in" because the caption beside it takes the remaining width.
- Code: `login.html:9`. Add `white-space:nowrap` on the h1 and `flex-wrap:wrap` on the head, or the same `.card-head h2` rule used on the other pages.
- Screenshot: `crops/r2-devices-login390.png`.

**R3. The Settings "Phones with the Relume app" rows are 8 px left of the card edge.**
- The rows start at x=868; the card title and caption start at x=876.
- Code: `settings.html:13` `.alist li` uses `padding: .75rem 1rem`. Change it to `12px 24px`.
- Screenshot: `shots/impl-settings-1440.png`.

**R4. The Schedules "View log" link is grey.**
- It is styled as `.caption`, so it uses the caption colour (#616161) instead of the brand link colour and weight 600 (mockup `.st .caption a`).
- Code: `schedules.js:66`. Use `<span class="caption"><a href=…>View log</a></span>`, or drop the caption class from the link.
- Screenshot: `shots/impl-sched-1440.png`.

**R5. The Followed projects header title wraps at 390 px.**
- "Followed / projects" is squeezed by the ⋯ button and the full-text "Follow project" button.
- Code: `projects.html:95`. On mobile make the primary button icon-only, as Apps does: label inside `hide-m` plus an sr-only copy, and `min-width:0`.
- Screenshot: `crops/r2-proj-apps390.png`.

## Minor
- **m1.** The "All" pill count shows "1207" without a thousands separator. The other counts and the mockup use "1,207". Code: `index.js:75`. Use `S.universe.toLocaleString('en')`. (fix-web-a)
- **m2.** The Builds log pane is blank when the job has no log file. Show "No log output" instead. Code: `builds.js`. The scratch data has no `logs/`, so this was not checked with real output.
- **m3.** The Settings GitHub token input is 170 px wide and clips its placeholder ("Paste a new token to"). Code: `settings.html`, the token field.
- **m4.** On the 390 px Followed projects rows, the phone circle "v" and the on-disk version float in the middle of the row. Readable, but loose. Code: `projects.js` row template.
- **m5.** The Apps "outline" rebuild button keeps a grey icon. In the mockup it is brand blue. Code: `apps.html` inline CSS, `.icon-btn.outline`.
- **m6.** The native time input shows the browser locale ("上午09:00"). This is the platform control; it is acceptable unless the lead wants a text input.

## Must-fix count by owner
| Owner | must-fix |
|---|---|
| fix-web-a (app.js, app.css, index.*) | 0 (minor m1) |
| fix-web-b (devices.html, login.html, settings.html, schedules.js, projects.html) | 5 (R1–R5) |

---

# Round 3 (HEAD da87670, including fd94815)

**Verdict: PASS**. No must-fix items remain.

## Round-2 items
- **R1 (Devices sideways scroll)**: fixed. At 390 px `scrollWidth` is 390 on every page: Marketplace (all four states), Apps, Apps drawer, Schedules, Builds, Devices, Projects, Settings and Login (`m/*390*.json` `__doc`).
- **R2 (Login title)**: fixed. "Sign in" stays on one line at 390 px, and the caption wraps beside it.
- **R3 (Settings phone rows)**: fixed. The rows now start on the card content edge (x=876 at 1440 px).
- **R4 (View log link)**: fixed. "View log" is brand blue.
- **R5 (Followed projects header)**: fixed. At 390 px "Follow project" is an icon-only button and the title fits on one line.
- **m1 (All pill count)**: fixed. Reads "All 1,207".

## Regression look at 390 px
Checked: all pages and states (`crops/r3-a.png`, `crops/r3-b.png`). There is no overflow, no wrapped title and no clipped control. The open minors from round 2 (m2–m6) are unchanged.

New minor:
- **m7.** The Settings phone rows' "Revoke" button now wraps under the text at 1440 px. The extra 16 px of padding leaves too little room on the line. It is left-aligned and tidy, so it is acceptable. Optional fix: `flex-wrap: nowrap` with the text `min-width:0`, which keeps the button at the right edge (`settings.html:13`).

## Must-fix count by owner
| Owner | must-fix |
|---|---|
| fix-web-a | 0 |
| fix-web-b | 0 |

---

# Round 4 (HEAD 8d7928b, polish)

**Verdict: PASS**. Checked at 390 and 1440 px (`crops/r4-1440.png`, `crops/r4-390.png`, `crops/r4-set390b.png`).

- **m7 (Revoke button)**: fixed. "Revoke" stays at the right of the phone row and is vertically centred at both widths.
- **m2 (empty log)**: fixed. The empty log pane reads "No log output".
- **m3 (token input)**: fixed. The token input fills the row: 404 px beside Save at 1440 px, full width at 390 px. The whole placeholder is visible.
- **m4 (Followed projects rows on phones)**: fixed. Each row now has a labelled "On disk" version, with the phone circle right-aligned on the same line. The rows are evenly spaced, and there is no overflow (`scrollWidth` 390).
- **m5 (outline rebuild button)**: fixed. Its icon is brand blue.

Regression look at Settings, Builds, Projects and Apps: no overflow at 390 px, no wrapped titles, and no new misalignment. m6 (native time input locale) stays as accepted.
