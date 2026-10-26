# Candidate 3 — designer report

## Direction
- The focus is on what needs doing, not on browsing. Every page is a master–detail split, like a mail client: the list is on the left and the selected item is on the right.
- Apps opens with "Needs you" first (failed builds, missing URL), then building, update available and idle. Problem rows show the fix inline (a paste-URL field, a "turn failing patch off" button).
- In Marketplace the right pane is a 3-step enable flow: pick one bundle → set patches → download source.

## Deliverables
- web/marketplace.html — done. All apps with a category select (real categories and counts), search, sort, and an Enabled / Not enabled filter. Boost for Reddit is open with its 4 real bundles, each labelled Official catalog / Built-in / Custom. Real patch names with switches, defaults versus your changes. The "APKMirror page not found → paste URL" state keeps Enable disabled until the URL is valid.
- web/apps.html — done. Columns: app, version, bundle, status; covers idle, building, failed, needs URL and update available. Check-update / rebuild / download are SVG icon buttons with tooltips and aria-labels. The YouTube settings panel is open: change bundle, patch switches with options, version mode Auto/Pinned/Latest plus allow nearest, Follow-project dependencies, download URLs.
- web/schedules.html — done. One global interval with next run, what each run does, and recent runs; OTA schedule list plus an editor.
- web/restyle-sample.html — done. Build history restyled as job list + job detail. A note says how Devices, Follow projects, Settings and Login map to the same style.
- android/screens.html — done, light only, 6 phone screens: Marketplace list, app detail and enable, paste-URL bottom sheet, Apps with icon actions, app settings, Schedules. Navigation bar has Marketplace / Apps (badge) / Schedules. A switch at the top of the page flips between the fallback palette (teal, API 29–30) and a dynamic-colour sample.
- tokens.md — done. Fluent 2 and Material 3 tokens, plus the 37-icon list. README.md — done.
- Light-only change applied: no dark styles, dark toggle or prefers-color-scheme rules.

## Checks
- I rendered every page in headless Chrome at 1440 px and at 390 px (mobile list and detail) and fixed the overflow bugs I found.
- All key text/background colour pairs pass WCAG AA (4.85:1 or higher).
- No external src/href/url() references. Scratch files are deleted.

## Open questions
1. Android today has a Settings screen (server, login, logout). The requirements list only three tabs, so I put an account avatar in the top bar that opens those settings. Confirm that this is acceptable.
2. The patch names in the YouTube settings panel are made up, because the built-in bundle's patch list was not available offline. Other apps use real catalog data.
3. Nav links to Devices, Follow projects and Settings point to "#" because those pages were not part of this brief.
