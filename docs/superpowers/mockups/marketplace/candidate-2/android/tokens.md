# Candidate 2 · Android tokens (Material Design 3, light only)

Source of truth for the Android mockups (`m3.css`, `icons-material.js`). Names are Compose `ColorScheme` roles; pastel accents are an extended colour set.

## 1. Surfaces: neutral grey, no hue

All surface roles have R = G = B. They are part of the one fixed scheme in section 2.

| Role | Value | Used for |
|---|---|---|
| surface | `#F8F8F8` | screen and top app bar background |
| surfaceContainerLowest | `#FFFFFF` | cards, bundle options, interval card |
| surfaceContainerLow | `#F3F3F3` | bottom sheet |
| surfaceContainer | `#EEEEEE` | navigation bar, bottom action bar |
| surfaceContainerHigh | `#E8E8E8` | search bar, system dialog, neutral pill |
| surfaceContainerHighest | `#E2E2E2` | switch track (off) |
| onSurface | `#1C1C1C` | primary text |
| onSurfaceVariant | `#474747` | secondary text, icons |
| outline | `#767676` | field borders, neutral chip and interval chip borders, switch (off) |
| outlineVariant | `#C8C8C8` | dividers only (decorative; component borders use outline for 3:1) |
| inverseSurface / inverseOnSurface | `#303030` / `#F1F1F1` | snackbar |
| error / onError | `#BA1A1A` / `#FFFFFF` | error text, badge |
| errorContainer / onErrorContainer | `#FFDAD6` / `#93000A` | Failed pill |

## 2. Action and control roles: fixed soft palette, hue = meaning

Dynamic colour is off on every API level: the theme always uses this fixed scheme (`lightColorScheme(...)`), so a wallpaper cannot re-tint the app and API 29–30 get the same palette as API 31+. One code path, no fallback branch. Recommended over "dynamic for primary only", because a wallpaper-driven primary would bring back one dominant hue.

| Role | Value | Meaning | Used for |
|---|---|---|---|
| primary / onPrimary | `#6E5FD4` / `#FFFFFF` (4.9:1) | build on the server (violet) | filled buttons (Next, Enable and build, Save & rebuild, Run now, Create), switches |
| primaryContainer / onPrimaryContainer | `#E6E1FF` / `#34248C` (9.4:1) | build | tonal rebuild icon button; build icons (`#34248C` glyph) |
| secondary / onSecondary | `#2F6EAE` / `#FFFFFF` (5.3:1) | choose and edit (sky) | text and outlined buttons (Save, Reset, Cancel), radio, checkbox, focused field, focus ring |
| secondaryContainer / onSecondaryContainer | `#DBE9FF` / `#0B3D91` (8.2:1) | choose | selected "All" chip, segmented selection, selected interval chip, Apps tab indicator; sync and search glyphs |
| tertiary / onTertiary | `#B85A35` / `#FFFFFF` (4.6:1) | send to the phone (coral) | Install and Update buttons |
| tertiaryContainer / onTertiaryContainer | `#FFE1D5` / `#7D2A0E` (7.7:1) | send to the phone | OTA schedule FAB |

Text-colour buttons on grey: `#2F6EAE` is ≥ 4.57:1 on every surface (lowest on `#EEEEEE`).

Compose:
- `MaterialTheme(colorScheme = RelumeLight)`, where `RelumeLight = lightColorScheme(primary = …, surface = …)` with section 1 and 2 values. No `dynamicLightColorScheme()` call.
- Extended colours (section 3) go in an `@Immutable data class RelumeColors` provided by a `staticCompositionLocalOf`.
- Per-component overrides: `FilledTonalIconButton` → primaryContainer. Install/Update `Button(colors = ButtonDefaults.buttonColors(containerColor = tertiary))`. `OutlinedButton(border = BorderStroke(1.dp, secondary))`, `TextButton` content = secondary. `RadioButton`/`Checkbox`/`SegmentedButton`/`OutlinedTextField` focused colours = secondary. Each `NavigationBarItem` gets its own `indicatorColor` (section 3).
- Filter chips: unselected border = outline (`#767676`, 4.3:1); category chip border = the category's on-container at 70 % over white (≥ 3.6:1), label = on-container; selected = the category container.

## 3. Pastel accent palette (items only)

Soft multicolour: each hue is a light container with a dark text colour on it. Shared with the web half.

| Token | Container | On-container | Contrast |
|---|---|---|---|
| blue | `#DBE9FF` | `#0B3D91` | 8.2:1 |
| green | `#D4F3DC` | `#14532D` | 7.7:1 |
| amber | `#FFEDC2` | `#6B4500` | 7.3:1 |
| coral | `#FFE1D6` | `#8A2C0D` | 7.0:1 |
| pink | `#FDE0EF` | `#8A1650` | 7.4:1 |
| lavender | `#E8E0FF` | `#4A2A8F` | 8.2:1 |
| teal | `#D0F2EE` | `#0F5249` | 7.6:1 |

Where colour goes:

| Item | Mapping |
|---|---|
| Build status pill | Up to date = green, New version = lavender, Building / Installing = blue, Needs URL = amber, Failed = errorContainer, Idle / Not installed = surfaceContainerHigh |
| Install state on this phone | Installed + version = green, older version on phone (Update) = lavender, Installing = blue, Not installed = neutral |
| Category tag, app tile, run step icon | social = pink→lavender, video = coral→amber, music = amber→coral, tools = teal→blue, productivity = blue→teal, personalization = lavender→pink, maps = green→teal, news = blue→lavender. Tile = two-container gradient; glyph = first hue's on-container |
| Origin label | Official catalog = blue, Built-in = teal, Custom = pink |
| Interval | Selected interval chip = secondaryContainer; "Every 6 hours" tag = blue |
| Navigation indicator (per tab) | Marketplace = amber, Apps = blue (secondaryContainer), Schedules = teal |
| Icon glyphs by meaning | build = `#34248C`, sync / search = `#0B3D91`, add link / link field icon = amber `#6B4500`; back, sort, more, paste stay onSurfaceVariant |
| Linear progress | blue track `#DBE9FF`, indicator `#0B3D91` (same as the Building pill) |
| OTA schedule tag | Daily = amber, Weekly = lavender |
| "Changed by you" | pink |

Every text pair passes WCAG AA (≥ 4.5:1); glyph on the second gradient hue also passes (lowest 6.9:1).

## 4. Type scale: every role one step down

Each role uses the next smaller size of the MD3 scale (24 → 22 → 16 → 14 → 12 → 11). 11 sp is the floor.

| Role in mockup | MD3 default | Now | Size / line / weight / tracking |
|---|---|---|---|
| headlineSmall slot (detail title, sheet title) | headlineSmall 24 | titleLarge-size | 22 / 28 / 400 / 0 |
| titleLarge slot (top app bar title, hero name) | titleLarge 22 | titleMedium | 16 / 24 / 500 / 0.15 |
| titleMedium slot (card app name) | titleMedium 16 | titleSmall | 14 / 20 / 500 / 0.1 |
| titleSmall slot (section header, bundle name) | titleSmall 14 | labelLarge-size | 12 / 16 / 500 / 0.5 |
| bodyLarge slot (list headline, field text, search) | bodyLarge 16 | bodyMedium | 14 / 20 / 400 / 0.25 |
| bodyMedium slot (list supporting text) | bodyMedium 14 | bodySmall | 12 / 16 / 400 / 0.4 |
| bodySmall slot (meta, helper text) | bodySmall 12 | labelSmall-size | 11 / 16 / 400 / 0.4 |
| labelLarge slot (buttons, chips, segmented) | labelLarge 14 | labelMedium | 12 / 16 / 500 / 0.5 |
| labelMedium slot (nav label, pills, tags) | labelMedium 12 | labelSmall | 11 / 16 / 500 / 0.5 |

Compose: define `Typography(...)` with these values in the slots on the left, so components keep their default slot and pick up the smaller size. Large button (bottom action) uses 14 sp.

## 5. Shape, spacing, components

- Corners: 4 / 8 / 12 / 16 / 28 / full (MD3 defaults).
- Spacing grid: 4 dp base. Screen edge and list insets 16 dp; gaps 8 or 16 dp; section header 48 dp tall.
- Touch targets: 48 dp minimum. Icon buttons are 48 dp; a trailing icon button is pulled out 12 dp so its glyph lines up with the 16 dp content edge.
- Top app bar 64 dp (4 dp side padding). Navigation bar 72 dp. Search bar 56 dp. Outlined text field 56 dp (16 dp text inset, 12 dp leading-icon inset).
- List item (MD3): 16 dp leading, 16 dp gaps, 24 dp trailing; 56 dp one-line, 72 dp two-line. Leading (tile 40 dp, radio, checkbox, icon) and trailing (switch, pill, chevron) are vertically centred on the text block. Inset list group = white container, 16 dp radius, dividers from the text start.
- Radio button 20 dp (2 dp ring, 10 dp dot), checkbox 18 dp (2 dp corner); the whole list row is the 48 dp target. Switch 52 x 32 dp. Filter chip 32 dp (16 dp padding, 8 dp with leading icon). Buttons 40 dp; full-width bottom action 48 dp.
- App tile 40 dp in lists and cards, 64 dp in the app detail header.

## 6. Icons: official Material Symbols Rounded

Weight 400, grade 0, optical size 24, FILL 0 unless noted. Path data in `icons-material.js` is copied unchanged from the official repository (viewBox `0 -960 960 960`).

Source: `https://raw.githubusercontent.com/google/material-design-icons/master/symbols/web/<name>/materialsymbolsrounded/<name>_24px.svg` (FILL 1: `<name>_fill1_24px.svg`).
Repository: https://github.com/google/material-design-icons. License: Apache License 2.0.
In the app, import the same official SVGs as vector drawables.

| Sprite id | Official name | Variant | Used for |
|---|---|---|---|
| `ms-storefront` / `ms-storefront-fill1` | storefront | FILL 0 / 1 | Marketplace tab (fill 1 when selected) |
| `ms-apps` | apps | FILL 0 | Apps tab |
| `ms-schedule` / `ms-schedule-fill1` | schedule | FILL 0 / 1 | Schedules tab, interval tag |
| `ms-search` | search | FILL 0 | search, locate APKMirror page, run step |
| `ms-sort` | sort | FILL 0 | sort and origin filter |
| `ms-check` | check | FILL 0 | selected chip, status pills |
| `ms-chevron_right` | chevron_right | FILL 0 | open app detail |
| `ms-arrow_back` | arrow_back | FILL 0 | back |
| `ms-warning` | warning | FILL 0 | APKMirror page not found |
| `ms-link` | link | FILL 0 | URL fields, Needs URL pill |
| `ms-content_paste` | content_paste | FILL 0 | paste URL |
| `ms-build` | build | FILL 0 | rebuild, run step |
| `ms-sync` | sync | FILL 0 | check for updates |
| `ms-error` | error | FILL 0 | Failed pill |
| `ms-system_update` | system_update | FILL 0 | Update on phone |
| `ms-install_mobile` | install_mobile | FILL 0 | Install button |
| `ms-progress_activity` | progress_activity | FILL 0 | Installing pill |
| `ms-add_link` | add_link | FILL 0 | add custom source |
| `ms-more_vert` | more_vert | FILL 0 | OTA schedule menu |
| `ms-play_arrow` | play_arrow | FILL 0 | Run now |
| `ms-add` | add | FILL 0 | new OTA schedule FAB |
| `ms-inventory_2` | inventory_2 | FILL 0 | check bundles step |
| `ms-notifications` | notifications | FILL 0 | notify step |
| `ms-expand_more` | expand_more | FILL 0 | dropdown field |
