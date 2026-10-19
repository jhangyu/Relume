# Merge notes 1 — index, login, apps, app-detail

Sources: A = designer-fd ("Patch Bay"), B = designer-uxpm ("Workbench Ledger"), C = designer-styling ("Workbench", base of `final/`).
Rule: every feature, interaction or information element from A or B is added in C's visual language. "Skip" only for pure theme decoration, or when C already has the same thing.
Page CSS goes in a second `<style>` tag. The shared C style block is unchanged, so it stays the same in all 8 files.

## App list (`apps.html`) — 17 add, 7 skip

| # | Feature (source) | Decision | Where / reason |
|---|---|---|---|
| 1 | Status tally cards filter the list when clicked; `aria-pressed` (B) | add | apps.html:183 |
| 2 | Search works on app, package and source (A, B) | add | apps.html:186, filter logic :329 |
| 3 | Status filter works and stays in sync with the tally; "Not built" option (A segmented, B tally) | add | apps.html:187 |
| 4 | "No items match" empty state; filtered groups open, empty groups hide | add | apps.html:317, :329 |
| 5 | Per-group status summary (counts of update / building / failed / ok) (A group LED row) | add | apps.html:190 (also each group) |
| 6 | Building item shows a progress % and bar (A, B) | add | apps.html:218 |
| 7 | Building item gets a "View log" action in place of the disabled Rebuild (A, B) | add | apps.html:221 |
| 8 | Status badge links to the build log (B) | add | apps.html:218, :270 |
| 9 | Failure reason inline, with build number and log link (A, B) | add | apps.html:270 |
| 10 | Failed item: Rebuild becomes "Retry" (B) | add | apps.html:273 |
| 11 | Download offers the last good / previous build when the current one failed or is building (B) | add | apps.html:222, :274 |
| 12 | "Not built" state: never-built item, "Build" action, Download disabled (A, TikTok · icysymmetra) | add | apps.html:308 |
| 13 | Variant (arch) in the package line, for example arm64-v8a (A, B) | add | apps.html:215 |
| 14 | A group collapsed by default with its summary still visible (A Beta bench, B Utilities) | add | apps.html:274 |
| 15 | Reorder works on touch: pointer-event drag on the handle, with mouse DnD and Alt+↑/↓ kept (A's README lists this as missing) | add | apps.html:337, hint :317 |
| 16 | Hint that order saves immediately and items can move between groups (B) | add | apps.html:317 |
| 17 | Sidebar server status: worker state and queue, disk usage, host arch (B rail foot, A host LED) | add | apps.html:176, app-detail.html:175. Text agreed with merge-designer-2 and identical in all 6 app pages. |
| — | Column header row (B) | skip | C already labels each cell ("Built app", "Patches · local → upstream"). |
| — | Drop-indicator line while dragging (B) | skip | C moves the row live while dragging, which already shows where it lands. |
| — | "Update upstream" in primary colour when an update exists (B) | skip | C already disables Update when there is nothing to pull. That gives the same signal without extra accent buttons. |
| — | "upstream checked 04:00 · 3 new" in the top bar (A) | skip | Already in the "Updates available" card ("upstream checked 04:00 today"). |
| — | Group patch-cable colour, rack screws, LEDs (A) | skip | Theme decoration only. Status is carried by badges and the group summary. |
| — | Mobile bottom tab bar (A, B) | skip | C's off-canvas drawer is the chosen responsive pattern. Both give the same navigation. |
| — | Rail numbering 01–05 (B) | skip | Decoration only. |

## App detail (`app-detail.html`) — 24 add, 3 skip

| # | Feature (source) | Decision | Where / reason |
|---|---|---|---|
| 1 | Header badge names the available version, "v1.5.0 available" (A) | add | app-detail.html:182 |
| 2 | Download in the header (A, B) | add | app-detail.html:184 |
| 3 | "Installed on" devices with installed version per device, plus Manage devices (B) | add | app-detail.html:185 |
| 4 | Included count in the tab label, "133/142" (B) | add | app-detail.html:190 |
| 5 | "N changed" count on the Options tab (B) | add | app-detail.html:190 |
| 6 | Patch file the list comes from, `morphe-patches-1.4.0.mpp` (A) | add | app-detail.html:191 |
| 7 | All on / All off (A); required patches stay on | add | app-detail.html:192 |
| 8 | Show filter: All / Included / Excluded / Has options (A, B) | add | app-detail.html:194 |
| 9 | Patch filter search works (A, B) | add | app-detail.html:194, logic :227 |
| 10 | Live "N / 142 included" counter (A) | add | app-detail.html:195 |
| 11 | Per-patch tags update live: default on / default off / required / included by you / excluded by you (B) | add | app-detail.html:196, :227 |
| 12 | "Options" button on a patch row jumps to that patch's options (A inline options drawer) | add | app-detail.html:196 (opens the Options tab and highlights the matching fieldset, so fields are not duplicated) |
| 13 | Note: new upstream patches follow the source default (B) | add | app-detail.html:197 |
| 14 | Pending-change diff (+ include / − exclude) against `src/options/morphe.json` (A) | add | app-detail.html:198 |
| 15 | Sticky save bar with live unsaved count, Discard / Save / Save & rebuild (A, B) | add | app-detail.html:222 |
| 16 | Option type tags (string / enum / boolean / color) and "changed" marker (B) | add | app-detail.html:201 |
| 17 | Patch default shown for each option (B) | add | app-detail.html:201 |
| 18 | Colour picker option, Theme › dark background (B) | add | app-detail.html:207 |
| 19 | Validated option with format hint, Spoof app version (B) | add | app-detail.html:212 |
| 20 | Warning when an option's patch is excluded: "kept but unused" (B) | add | app-detail.html:211 |
| 21 | Third version policy, "Latest on APKMirror (ignore compatibility)", with warning (B) | add | app-detail.html:218, :220 |
| 22 | Pinned-version list tagged "patches recommend / compatible / last built OK / not in patch list" (A, B) | add | app-detail.html:219 |
| 23 | Auto policy text: resolves today and changes when patches update (A, B) | add | app-detail.html:216 |
| 24 | History: "All builds →" link, and a failed row opens its log (A, B) | add | app-detail.html:221, :222 |
| — | History in a side column next to the editor (B) | skip | Layout only. C keeps history as a tab, and the same data is there. |
| — | Options stored per item (B) | skip | Conflicts with the real repo: `src/options/<source>.json` is per source. C's "shared by every item from the morphe source" is correct. |
| — | More sample options (video quality, start page, package name) (A) | skip | Sample data only. Every option type A shows (text, select) is already present. |

## Login (`login.html`) — 6 add, 3 skip

| # | Feature (source) | Decision | Where / reason |
|---|---|---|---|
| 1 | Show / hide password toggle (B) | add | login.html:169 |
| 2 | Forgot-password hint: reset through the container CLI (A, B) | add | login.html:174 |
| 3 | Server summary: host, arch, item count, paired devices (B plate, A "14 apps · 2 phones") | add | login.html:176 |
| 4 | App version in the footer (A "v1.0") | add | login.html:176 |
| 5 | Busy state on submit (B `aria-busy`) | add | login.html:165 |
| 6 | `aria-invalid` on the password field in the error state (B) | add | login.html:173 |
| — | "Patch. Build. Push." poster panel (B) | skip | Branding decoration only. |
| — | Jack / LED face plate (A) | skip | Theme decoration only. |
| — | Password "admin" enters, anything else shows the error (A) | skip | C's "Preview error state" button already covers the same demo. |

## Index (`index.html`) — 3 add, 2 skip

| # | Feature (source) | Decision | Where / reason |
|---|---|---|---|
| 1 | Lede saying what the series is: final merged series in C's language (A, B) | add | index.html:162 |
| 2 | Palette swatches (B), built from C's live tokens so they follow the theme toggle | add | index.html:163 |
| 3 | Richer per-screen descriptions for my 4 screens (A, B) | add | index.html:163. Build log, schedules, devices and settings text comes from merge-designer-2. |
| — | Rack-unit cards with cable colours (A) | skip | Decoration only. |
| — | Large poster title (B) | skip | Decoration only. |

## Checks
- No `http(s)://` in any `final/*.html`.
- Headless Chrome (Playwright, Chrome): index, login, apps and app-detail show no JS errors. They have no horizontal overflow in light and dark at 1440, 500 and 390 px.
- Interaction checks pass: tally/search/select filter, touch drag, Alt+↓, patch toggle → save bar + diff + tag, Has-options filter, All off keeps required patches on, Options jump, version policies, password toggle, error state.
