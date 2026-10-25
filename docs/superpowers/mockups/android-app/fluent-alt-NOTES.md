# Fluent 2 (fluent-a.html) and Linear-style minimal (alt-b.html)

Both: 12 frames at 412x915 (sign-in, unreachable, apps L/D, detail L/D, log, install download, system prompt, signature dialog, dependency dialog, settings). Same emerald accent and mono versions as the web UI. Sample data only.

## A. Fluent 2 (fluent-a.html)
- **System:** Microsoft Fluent 2 adapted to Android. Chosen because it is a fully specified, widely used, non-Material system with strong light/dark neutrals and clear form/dialog patterns.
- **Tokens:** neutral grey ramp (light bg #fafafa, card #fff, stroke #e0e0e0, fg #242424; dark bg #141414, card #292929, fg #fff). Brand emerald #0e7a56 light, #3dbf8d dark. Status: success #107c10, warning #bc4b09, danger #b10e1c, info #0f6cbd (dark variants lighter). Radii 4 (controls), 8 (cards), 12 (dialogs). Body 14 sp, title 20 sp semibold, versions in mono.
- **Look:** cards with 1 dp stroke, tinted pill badges, outlined switches (40x22), pivot tabs under the app bar, message bar for connection status, bottom nav with pill indicator.
- **Compose mapping:** no Fluent library for Compose. Build a small `FluentTheme` (CompositionLocal of colors, shapes, type) on `androidx.compose.foundation` plus `material3` primitives only where they are theme-neutral (Scaffold, ModalBottomSheet, AlertDialog shell). Surface/Card via `Surface(border=...)`, LazyColumn for the list.
- **Custom components:** Pivot tab row with underline, tinted Badge, outlined Switch (Compose `Switch` can be restyled but not to this look), MessageBar, step list with progress (install), nav indicator pill, log pane (monospace `LazyColumn`).
- **Trade-offs:** most familiar, highest contrast and clearest hierarchy. Card-per-app is tall: ~4.5 apps visible per screen. Needs a hand-made theme layer, so more upfront work than Material.

## B. Linear/Vercel-style minimal (alt-b.html)
- **System:** Linear/Vercel-like minimal dashboard. Chosen because it is the closest sibling of the existing web UI (zinc neutrals, hairlines, emerald accent) and the densest option for a list of apps.
- **Tokens:** dark bg #08090a, surface #0e0f11, hairline rgba(255,255,255,.08), fg #f4f5f6, muted #868b94, accent #3ecf8e; light bg #fbfbfb, hairline #e8e8ea, accent #0e9f6e. Radii 6 (controls), 8, 12 (dialogs). Body 13.5 sp, 11 sp uppercase section labels, mono for versions and package names. No shadows except dialogs.
- **Look:** no cards, hairline-separated rows grouped by status (Needs attention / Not installed / Up to date), status dot + label instead of badges, small inline switches (28x16) in every row, key-value "properties" detail screen, pinned bottom action dock.
- **Compose mapping:** custom theme only (no Material look). Rows as `LazyColumn` items with `HorizontalDivider`, sticky section headers via `stickyHeader`, dock via `Scaffold(bottomBar)`.
- **Custom components:** compact Switch (28x16, below the 48 dp touch size: row tap area must extend it), status dot label, sticky group header, property row, action dock, same step list and log pane as A. Compact switches need a larger invisible hit area (`minimumInteractiveComponentSize` or `Modifier.clickable` on the whole row).
- **Trade-offs:** densest (all 5 apps and their switches visible without scrolling), closest to web UI, fastest to scan. Smaller text and tiny switches hurt one-hand use and accessibility. Dark-first: light theme is secondary.

## Shared notes
- The Android system install dialog (5b) is drawn by Android and cannot be themed. First install of each package always shows it.
- Dependency dialog (5d) and signature dialog (5c) use the same step and checkbox components as the install screen.
- Cleartext http:// error text (1b) assumes the policy "allow only LAN, localhost, Tailscale".
