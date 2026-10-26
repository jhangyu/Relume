# Candidate 2 "Storefront" — design tokens

Light theme only on both platforms. Web tokens use Fluent 2 names (`@fluentui/tokens`, `webLightTheme`); Android tokens use Material 3 names (`MaterialTheme.colorScheme`, `md.sys.*`). CSS sources: `web/fluent.css`, `android/m3.css`.

## Web — Fluent 2

### Colour
| Fluent token | Value | Use |
|---|---|---|
| colorNeutralBackground1 | #ffffff | cards, drawer, dialog |
| colorNeutralBackground2 | #fafafa | page |
| colorNeutralBackground3 / 4 / 5 | #f5f5f5 / #f0f0f0 / #ebebeb | toolbar wells, nav rail, nav hover |
| colorNeutralForeground1 / 2 / 3 | #242424 / #424242 / #616161 | text, secondary text, captions |
| colorNeutralForegroundDisabled | #bdbdbd | disabled icon buttons |
| colorNeutralStroke1 / 2 / 3 | #d1d1d1 / #e0e0e0 / #f0f0f0 | control border, card border, row divider |
| colorNeutralStrokeAccessible | #616161 | input bottom stroke, switch track |
| colorBrandBackground (+Hover, +Pressed) | #0f6cbd (#115ea3, #0c3b5e) | primary button, switch on, step numbers |
| colorBrandBackground2 | #ebf3fc | selected bundle/mode card, pressed pill |
| colorBrandForeground1 / 2 | #0f6cbd / #115ea3 | links, nav indicator, official-catalog label |
| colorBrandStroke2 | #b4d6fa | brand badge border |
| colorStatusSuccess Background1 / Foreground1 / Border1 | #f1faf1 / #0e700e / #9fd89f | Up to date, Built-in label |
| colorStatusWarning … | #fff9f5 / #bc4b09 / #fdcfb4 | Needs URL, APKMirror not found |
| colorStatusDanger … | #fdf3f4 / #b10e1c / #eeacb2 | Failed |
| colorPaletteLavender Background2 / Foreground2 / BorderActive | #f0effd / #3c32a6 / #a6a0f5 | Custom label, Update available, "changed by you" |
| colorPaletteMarigold Background2 / Foreground2 | #fef7e6 / #7a4f00 | "Has options" |

Store category accents (Fluent shared palette; drive app tiles, card tint, filter dots). `--c1 → --c2` gradient, `--tint` card wash, `--ink` category text:
Social berry #b4009e→#8764b8 · Video cranberry #c50f1f→#da3b01 · Music pumpkin #ca5010→#a4262c · Tools teal #03787c→#005b70 · Productivity royal/brand #4f6bed→#0f6cbd · Personalization grape #5c2e91→#8764b8 · Maps green #0e700e→#038387 · News navy #0027b4→#4f6bed · Lifestyle gold #8f6a00→#ca5010. White monogram on every stop ≥ 4.5:1; `--ink` on `--tint` ≥ 6.5:1.

### Type (Fluent ramp; font stack `Segoe UI Variable`, Segoe UI, system-ui, -apple-system, Roboto, Noto Sans TC; mono Cascadia Mono, Consolas, Menlo)
Caption1 12/16 · Body1 14/20 · Body1Strong 14/20 600 · Subtitle2 16/22 600 · Subtitle1 20/28 600 · Title3 24/32 600 · Title2 28/36 600 · Title1 32/40 600 (hero only).

### Spacing, radius, elevation, motion
- spacingXXS 2 · XS 4 · S 8 · M 12 · L 16 · XL 20 · XXL 24 · XXXL 32 (px). Gallery gap 16, page padding 32 desktop / 16 mobile.
- borderRadiusSmall 2 · Medium 4 (buttons, inputs) · Large 6 (large controls) · XLarge 8 (cards, bundle cards) · 2XLarge 12 (app cards) · 3XLarge 16 (feature banners, expanded panel) · Circular (pills, search box).
- shadow2 (selected nav) · shadow4 (cards rest) · shadow8 (feature rest) · shadow16 (card hover, expanded app, tooltip) · shadow28 (feature hover) · shadow64 (drawer, dialog).
- durationFast 150 ms (hover, tooltip) · durationNormal 200 ms (card lift, switch) · durationGentle 250 ms (drawer, expand); curveDecelerateMid cubic-bezier(0,0,0,1) for entrances, curveEasyEase cubic-bezier(.33,0,.67,1) for state changes.

### Components used
NavDrawer (inline, collapses to overlay below 900 px), SearchBox, ToggleButton (circular pills for category/status filter), Dropdown, Card, Badge (tint), Switch, Radio, Checkbox, MessageBar (warning/success), ProgressBar, Tooltip, OverlayDrawer (app settings), Dialog, DataGrid-style table, Button (primary/secondary/subtle; icon-only buttons with `aria-label` + Tooltip).

## Android — Material Design 3 (light)

Dynamic colour on API 31+ (`dynamicLightColorScheme(context)`); fallback `lightColorScheme(...)` below on API 29–30. Values for the sample dynamic scheme are a green-wallpaper example only. Final fallback values should be regenerated from seed #0F6CBD with Material Theme Builder; the ones below are hand-tuned and AA-checked.

| ColorScheme role | Fallback (seed #0F6CBD) | Dynamic sample (green wallpaper) |
|---|---|---|
| primary / onPrimary | #2e5faa / #ffffff | #3b6939 / #ffffff |
| primaryContainer / onPrimaryContainer | #d7e3ff / #12458f | #bcf0b4 / #235024 |
| secondary / onSecondary | #565f71 / #ffffff | #52634f / #ffffff |
| secondaryContainer / onSecondaryContainer | #dae2f9 / #3e4759 | #d5e8cf / #3b4b39 |
| tertiary / onTertiary | #874b83 / #ffffff | #38656a / #ffffff |
| tertiaryContainer / onTertiaryContainer | #ffd7f3 / #6b3468 | #bcebf0 / #1e4d52 |
| error / errorContainer / onErrorContainer | #ba1a1a / #ffdad6 / #93000a | same |
| surface / onSurface / onSurfaceVariant | #f9f9ff / #191c20 / #43474e | #f7fbf1 / #181d17 / #424940 |
| surfaceContainerLowest … Highest | #ffffff, #f3f3fa, #ededf4, #e7e8ee, #e2e2e9 | #ffffff, #f1f5eb, #ebefe6, #e6e9e0, #e0e4da |
| outline / outlineVariant | #73777f / #c3c6cf | #72796f / #c2c8bd |

Extended colours (custom, harmonized to primary in code with `MaterialColors.harmonize`): success #2e6a3b, successContainer #b2f1bb, onSuccessContainer #12512a; warning #7a5900, warningContainer #ffdf9e, onWarningContainer #5c4200.

Role mapping: Update available = tertiaryContainer; Building = primaryContainer + LinearProgressIndicator; Failed = errorContainer; Needs URL = warningContainer; Up to date = successContainer; Idle = surfaceContainerHighest. Origin labels: official catalog = primaryContainer, built-in = successContainer, custom = tertiaryContainer.

- Type (MD3 type scale, Roboto / system): headlineSmall 24/32 · titleLarge 22/28 · titleMedium 16/24 500 · titleSmall 14/20 500 · bodyLarge 16/24 · bodyMedium 14/20 · bodySmall 12/16 · labelLarge 14/20 500 · labelMedium 12/16 500.
- Shape: extraSmall 4 (text fields) · small 8 (filter chips, status chips) · medium 12 (cards) · large 16 (bundle cards, FAB, large button) · extraLarge 28 (featured carousel items, schedule hero, bottom sheet) · full (buttons, search bar, switch).
- Elevation: level1 (elevated card) · level3 (FAB); tonal surfaces (surfaceContainer*) carry most hierarchy.
- Motion: standard cubic-bezier(.2,0,0,1) 300 ms (switch, selection); emphasizedDecelerate cubic-bezier(.05,.7,.1,1) 400 ms (bottom sheet, screen enter).
- Spacing: 4 dp grid; screen margins 16; list item 72 (two-line) / 56 (one-line); touch targets 48.
- Components: SearchBar, FilterChip, AssistChip, HorizontalMultiBrowseCarousel (featured), ListItem, Card (filled/outlined), RadioButton cards, Switch, SegmentedButton (version mode, OTA repeat), OutlinedTextField, Button (filled/tonal/outlined/text), IconButton (standard/filled/tonal) with `contentDescription` + `TooltipBox` on long-press, LinearProgressIndicator, ExtendedFloatingActionButton, ModalBottomSheet, NavigationBar (Marketplace / Apps with Badge / Schedules). Phone settings/account (server, background check, notifications, cleanup) open from the avatar in the top app bar.

## Icon set (`icons.js`, inline SVG `<symbol>`, 24×24, round caps, currentColor)
store, apps, schedule, builds, devices, follow, settings, search, sort, filter, chevron-down, chevron-right, back, close, add, check, sync (check for update), rebuild, download, link, paste, warning, error, info, star, sparkle, more, play, edit, delete, history, package, github, bell, shield, pin, menu.

Drawn once and stroked at 1.5 px on web (Fluent System Icons "Regular" weight) and 2 px on Android (Material Symbols Rounded weight). Implementation: web keeps inline `<symbol>` sprite in `app.js` (no external files, test_ui_static.py:35-40); Android ports the same paths into the existing `object Icons` SVG-path set (`Components.kt:97-115`), so no icon dependency is added.
