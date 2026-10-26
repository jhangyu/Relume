# Candidate 1 — worker report

## Direction
- Dense power-user console. Web uses Fluent 2 data grids: 36 px rows, sortable headers, a category list with counts, and the `/` key jumps to search. The Marketplace row expands into a bundle picker and a two-column patch list.
- Android uses MD3 with dynamic colour. List rows put the deciding numbers (bundles, patches, stars) on the second line. A navigation bar links Marketplace / Apps / Schedules.
- Status badges and source labels ("Official catalog" / "Built-in" / "Custom") have different shapes so they are never confused. The Apps settings panel sits beside the table on wide screens.

## Deliverables
- web/marketplace.html — done. All apps, category filter, search, sort, and source filter. Boost for Reddit is expanded with its 4 real bundles, source labels and the real 37-patch Patcheddit list with toggles, then "Enable and build". Threads shows the "APKMirror listing not found → paste URL" state inline.
- web/apps.html — done. 6 enabled apps covering all 5 statuses. Each row has check-update / rebuild / download / settings icon buttons with tooltips and aria-labels. The settings panel is open for YouTube: bundle change with a preview of which overrides are kept, patches with options, version mode Auto/Pinned/Latest plus allow nearest, the MicroG-RE Follow-project dependency, and the download URLs.
- web/schedules.html — done. One global interval card showing last-run numbers for each of the 5 run steps, plus recent runs and OTA schedules with an inline editor.
- web/restyle-sample.html — done. The Builds page with a light live log pane, history grid, and written restyle rules for devices, Follow projects, settings and login.
- android/marketplace.html, android/apps.html, android/schedules.html — done, light only. Marketplace list, app detail/enable, the needs-URL bottom sheet, Apps list with icon actions, app settings (2 screens), Schedules, and the OTA editor. Navigation bar on each list screen. A palette switcher shows two dynamic-colour samples and the fallback palette for API 29–30.
- tokens.md, README.md — done.

## Checks
- No external URLs.
- No dark-mode rules, toggle or prefers-color-scheme.
- Every SVG icon is aria-hidden and every icon-only button has a label.
- 27 text/background colour pairs pass WCAG AA (lowest 4.54).
- Every page was screenshotted in headless Chrome at desktop size, and the web Marketplace and Apps pages at 390 px. Layout fixes were applied and the screenshots are deleted.

## Open questions
1. Android today has a Settings tab (server, account, background check). The mockup moves it behind the account avatar in the top bar. Should it stay a fourth tab instead?
2. The Android dynamic-colour and fallback values are hand-picked approximations of MD3 tonal palettes, not generated with Material Theme Builder. The fallback should be regenerated from the seed #2B6AE8 in code.
3. YouTube patch names, versions and the run numbers are illustrative. Catalog apps, bundles, stars and Boost patches come from the real 2026-10-26 bundles.json.
