# Candidate 1 "Console" — design tokens (light only)

Source of truth for values: `web/fluent.css` (`:root`) and `android/md3.css` (`body[data-palette=…]`). Web names follow `@fluentui/tokens` webLightTheme; Android names are Compose `ColorScheme` fields. No dark theme, no theme toggle, no `prefers-color-scheme`.

## Colour — web (Fluent 2)

| Use | Fluent token | Value |
|---|---|---|
| Canvas | colorNeutralBackground2 | #fafafa |
| Card, grid, panel | colorNeutralBackground1 | #ffffff |
| Row hover / selected nav | colorNeutralBackground1Hover / …Selected | #f5f5f5 / #ebebeb |
| Nav rail | colorNeutralBackground4 | #f0f0f0 |
| Log pane, info bar | colorNeutralBackground3 | #f5f5f5 |
| Text primary / secondary / tertiary | colorNeutralForeground1 / 2 / 3 | #242424 / #424242 / #616161 |
| Borders | colorNeutralStroke1 / 2 / 3 | #d1d1d1 / #e0e0e0 / #f0f0f0 |
| Input underline | colorNeutralStrokeAccessible | #616161 |
| Primary action, focus bar, switch on | colorBrandBackground (+Hover, Pressed) | #0f6cbd (#115ea3, #0c3b5e) |
| Selected row, toggle pill on | colorBrandBackground2 / colorBrandStroke2 | #ebf3fc / #b4d6fa |
| Status: up to date | colorStatusSuccess Background1/Foreground1/Border1 | #f1faf1 / #0e700e / #9fd89f |
| Status: needs URL, stale catalog | colorStatusWarning Background1/Foreground3/Border1 | #fff9f5 / #8a3707 / #fdcfb4 |
| Status: failed | colorStatusDanger Background1/Foreground1/Border1 | #fdf3f4 / #b10e1c / #eeacb2 |
| Status: update available, building | brand tint (Background2 + Foreground2) | #ebf3fc / #115ea3 |
| Origin "Official catalog" | colorNeutralBackground5 + Foreground2 | #ebebeb / #424242 |
| Origin "Built-in" | colorPaletteTealBackground2 / Foreground2 | #e9f7f8 / #006e72 |
| Origin "Custom" | colorPaletteBerryBackground2 / Foreground2 | #fbf2fa / #9b2a8c |

Origin labels are square (radius 2) and status badges are round (circular), so the two are never confused.

## Colour — Android (Material 3, colourful)

Dynamic colour (API 31+) takes the scheme from the wallpaper (`dynamicLightColorScheme`). The two "dynamic" palettes in the mockups are samples only. The fallback for API 29–30 is a fixed `lightColorScheme` from the seed #2B6AE8 (today's accent in `ui/Theme.kt`).

| Role | Dynamic sample "warm" | Dynamic sample "cool" | **Fallback (API 29–30)** |
|---|---|---|---|
| primary / onPrimary | #9a4521 / #fff | #006a63 / #fff | **#2559bd / #fff** |
| primaryContainer / on… | #ffdbcd / #380d00 | #9ef2e7 / #00201d | **#dae2ff / #001a43** |
| secondary | #77574b | #4a635f | **#585e71** |
| secondaryContainer / on… | #ffdbcf / #2c160d | #cce8e2 / #05201c | **#dce2f9 / #151b2c** |
| tertiary | #695e2f | #47617a | **#7b4e7f** |
| tertiaryContainer / on… | #f2e2a7 / #221b00 | #cee5ff / #001d32 | **#ffd6fe / #310a37** |
| surface | #fff8f6 | #f4fbf8 | **#faf8ff** |
| surfaceContainerLow / … / Highest | #fff1ec … #f0dfd8 | #eef5f2 … #dde4e1 | **#f4f3fa, #eeedf4, #e8e7ef, #e3e2e9** |
| onSurface / onSurfaceVariant | #231a16 / #53433e | #161d1c / #3f4947 | **#1a1b21 / #44464f** |
| outline / outlineVariant | #85736d / #d8c2bb | #6f7977 / #bec9c6 | **#757780 / #c5c6d0** |

Fixed for all palettes: error #ba1a1a, errorContainer #ffdad6 / onErrorContainer #410002. Extended colour **warning** (harmonise to primary in code with `MaterialColors.harmonize`): warning #7c5800, warningContainer #ffdea6, onWarningContainer #271900 — used only for "Needs URL".

Colour mapping of states (Android): up to date = surfaceContainerHighest; building = primaryContainer + LinearProgressIndicator; update available = tertiaryContainer; failed = errorContainer; needs URL = warningContainer. App tiles rotate primaryContainer / secondaryContainer / tertiaryContainer / primary / tertiary for colour.

All text pairs above were checked: ≥ 4.5:1 (lowest: web log timestamp #707070 on #f5f5f5 = 4.54).

## Type

| Web (Fluent ramp) | Size/line | Use | Android (MD3) | Size/line |
|---|---|---|---|---|
| title3 → used as subtitle1 | 20/28 600 | page title | titleLarge | 22/28 |
| subtitle2 | 16/22 600 | card title | titleMedium | 16/24 500 |
| body1 | 14/20 400 | grid cells, body | bodyLarge / bodyMedium | 16/24, 14/20 |
| caption1 | 12/16 | header cells, meta, hints | bodySmall / labelMedium | 12/16 |
| caption2-ish | 11/14 mono | package names, repo slugs | labelSmall | 11/16 500 |

Fonts: web `"Segoe UI Variable Text","Segoe UI",-apple-system,…,"Noto Sans TC"`; mono `"Cascadia Mono",Consolas,ui-monospace`. Android Roboto / Roboto Mono (system). No web fonts. Numbers in tables use `tabular-nums`.

## Spacing, size, radius

- Web spacing = Fluent `spacingXXS…XXXL` (2, 4, 6, 8, 10, 12, 16, 20, 24, 32). Density: grid header 32 px, row 36 px (`--row-h`), sub-grid 32 px; buttons 32 px, small 24 px; icon buttons 28–32 px; on ≤ 760 px all targets ≥ 44 px.
- Android: 4 dp grid; list rows 64–72 dp; touch targets 48 dp; nav bar 80 dp; search bar 56 dp; chips 32 dp.
- Radius web: borderRadiusSmall 2 (origin labels), Medium 4 (buttons, inputs), Large 6, XLarge 8 (cards), Circular (badges, pills). Android: shapeXS 4 (text fields), S 8 (chips, labels), M 12 (cards, tiles), L 16 (FAB, large tile), XL 28 (sheets), Full (buttons, search bar).

## Elevation

- Web: shadow4 cards, shadow8 tooltip, shadow28 overlay panel (docked panel on ≥ 1281 px has no shadow, only a left border).
- Android: tonal surfaces carry hierarchy (surfaceContainer* levels). Shadows only on FAB (level 3) and bottom sheet (level 1).

## Motion

- Web: durationFaster 100 ms (hover/switch), durationNormal 200 ms (chevron, switch knob), durationGentle 250 ms + curveDecelerateMid (panel slide-in); `prefers-reduced-motion` turns animation off.
- Android: standard easing (0.2,0,0,1) 200–300 ms for state changes; emphasized decelerate (0.05,0.7,0.1,1) 400–500 ms for sheet and screen enter.

## Icon set (inline SVG, `icons.js`)

One 24-unit stroke geometry for both platforms: web draws it at stroke 1.5 (Fluent "Regular" look), Android at stroke 2 (Material Symbols Rounded look). Used as `<svg class="i" aria-hidden="true"><use href="#i-NAME"/></svg>`; every icon-only button carries `aria-label` (Android: `contentDescription`) and, on web, a tooltip (`data-tip`).

store · apps · history · phone · branch · calendar · settings · search · filter · sort · chevronRight · chevronDown · back · close · check · add · **sync** (check for update) · **wrench** (rebuild / retry) · **download** (download APK) · more · moreVert · menu · star · link · paste · warning · error · info · ok · arrowUp · play · edit · delete · package · external · account · pin · keyboard · bell · dot
