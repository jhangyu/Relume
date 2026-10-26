# Candidate 3 — "Triage + split view" (light only)

- Direction: work-first, not browse-first. Every page is one master–detail split (list left, the selected item right), like a mail client; Apps opens on "Needs you" (failed, needs URL) above building, updates and idle.
- Marketplace: the detail pane is a three-step enable flow (bundle → patches → download source); step 3 shows the "APKMirror page not found → paste URL" state and keeps Enable disabled until a valid URL.
- Apps: real columns (app, version, bundle, status, 3 icon actions) with inline fixes under problem rows (paste URL, turn failing patch off); app settings open in the right pane, not a new page.
- Schedules: one card shows the next run, the interval and what a run does; OTA schedules list + editor beside it.
- Android: same model one pane at a time; Navigation bar Marketplace / Apps (badge = needs you) / Schedules; URL entry is a bottom sheet; toggle fallback vs dynamic palette at the top of `android/screens.html`.
- Trade-offs: split view needs ≥ 900 px for two panes (below that, list then detail with Back); dense rows favour returning users over first-time browsing; less visual "store" appeal than a card gallery.
- Files: `web/*.html`, `android/screens.html`, `tokens.md`, `icons.svg`. Shared CSS/icons are inlined into each page; after editing `web/c3.css`, `android/m3.css` or `icons.svg` run `python3 inline.py .`. Mobile detail demo: append `#detail` to a web URL.
