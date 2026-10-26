# Polish batch (8 fixes + Android rebuild) — Design

Date: 2026-10-26. Base: `main` at `ea95acb`.

## End state

The 9 fixes below are committed on `main`, and the debug APK built from that HEAD is installed on phone `10AFCF269C007FB`.

## Requirements

1. **Followed-project apps in Apps.** The Apps API also returns the apps of Followed projects, with a source marker. The web Apps page and the Android Apps list both show them. For these apps, show only Download (the APK) and Install (to the phone). Hide update, patch, build, rebuild and schedule actions. The Followed projects page stays as it is.
2. **Followed projects buttons.** "Install to phone" and "Check now" use the shared Fluent button classes from `app.css`, and the padding is fixed. If Android has the same buttons, they use Material3 components.
3. **Devices "Pair devices" stays pending.** Find the root cause, report it, and fix the cause. Do not patch the display.
4. **Web icons.** Replace every path in the icon sprite (`local/webui/static/app.js`) with the matching official Fluent UI System Icons SVG path (this includes the broken rebuild wrench). Inline hand-drawn SVGs in pages use the sprite. Do not add a package or a build step.
5. **Apps search box and buttons.** The search box uses one shared Fluent input style (the light fill covers the whole box, no half-blue background). The two buttons next to it use the shared rounded Fluent button classes.
6. **Marketplace controls.** "Refresh catalog", "Add custom source", the "All origins" select and the "Most bundles" select use the shared rounded Fluent button and select classes.
7. **Schedules Repeat and At.** These controls use the same shared Fluent classes.
8. **Android server URL.** Settings has a "Server URL" row. It opens a dialog to edit the URL. Before saving, the app calls `/api/health` on the new URL and checks that it is reachable and compatible. On failure it shows the error and does not save. On success it saves, clears the login state and returns to the login screen.

9. **Marketplace auto-load.** Scrolling the Marketplace list loads the next page automatically when the end comes into view (web: `IntersectionObserver` on a sentinel; Android: load when the last item becomes visible). Remove the manual "Load more" button. Applies to web and Android.

Rule for items 2 and 4–7: delete per-page hand-made control styles. All controls use one set of component classes in `app.css`.

Then rebuild the debug APK and install it on `10AFCF269C007FB`. Do not push. Do not create a `companion-v*` tag.

## Process

1. Preflight: 3 sonnet readers (web styles and icons; backend Apps/Followed data flow and the pairing-pending cause; Android Apps list, Followed projects, Settings, server URL storage and login flow).
2. Planning: 6 opus architects (web icons; web controls; backend Apps merge; backend pairing fix; Android Apps list and Followed projects; Android server URL). Output: two rounds with file ownership.
3. Implementation: 2 rounds, 3–5 implementers each. One owner per file at a time. Round 1: the parts with no dependency. Round 2: Apps list display that needs the round 1 backend, and the rest.
4. Review: one reviewer for the backend architecture only. No test runner. No final UI review: the user tests the UI.

## Tests

Add no tests. Delete existing UI-type tests (layout, style, icon, button wiring) in the areas this batch changes. Keep backend logic tests and make them pass. Each implementer runs the related tests once.

## Out of scope

Push, release tag, companion release, the earlier marketplace parking lot.
