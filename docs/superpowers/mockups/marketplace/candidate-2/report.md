# Candidate 2 — designer report

## Deliverables (all done)
- web/marketplace.html: featured banners, a card gallery, category pills, search, sort and an origin filter. Expanding a card opens a panel in its row: 5 bundles with official catalog / built-in / custom labels, patch toggles, the "APKMirror page not found → paste URL" state, and Enable, which stays disabled until a valid listing URL is typed. There is also a dialog to add a custom source.
- web/apps.html: 6 enabled apps showing all 5 statuses. Check-update, rebuild, download and settings are SVG icon buttons with tooltips and aria-labels. The open settings drawer has: enable switch, bundle, patches + options, Auto/Pinned/Latest + nearest, Follow-project dependencies, URLs.
- web/schedules.html: hero card for the global interval, the 5-step run pipeline, OTA schedules and a dialog to create one.
- web/restyle-sample.html: Builds (queue, live log, history) plus small samples of Devices, Followed projects and Settings.
- android/{marketplace,apps,schedules}.html: 8 MD3 phone screens with a navigation bar (Marketplace / Apps / Schedules). Each page switches between dynamic colour and the fallback palette (light only).
- tokens.md (both platforms, Fluent 2 / MD3 names, 37-icon list), README.md (9 lines), icons.js (shared inline SVG sprite).

## Checks
- No external URLs. No dark-mode rules. Every icon button has an aria-label.
- 30 colour pairs pass WCAG AA.
- Headless Chrome screenshots of every page. Mobile was checked at 500 px (the smallest width headless Chrome allows).
- Mock content uses real data from bundles.json. YouTube is not in the community catalog, so it is shown as built-in.

## Open questions
1. Marketplace blocks Enable until a URL exists. The alternative is to enable at once and show "Needs URL" in Apps. Apps uses that state only for an app whose URL broke later.
2. On Android, the phone's own settings (server, account, background check) sit behind the avatar in the top bar. Builds and Install get no tab.
3. The fallback MD3 colours are hand-tuned. Regenerate them from seed #0F6CBD with Material Theme Builder when implementing.
4. Scratch files went to .claude/tmp/c2* because a hook blocks /tmp. They are now deleted. .claude/tmp/c2.py is not mine and was left in place.
