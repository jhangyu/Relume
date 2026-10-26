# Candidate 3 — design tokens (light only)

Source of truth: `web/c3.css` (web) and `android/m3.css` (Android). `inline.py` copies them into each page.
Layers: primitive value → semantic token (Fluent 2 / MD3 name) → component use. Components use only semantic tokens.

## Web — Fluent 2 (webLightTheme names)

### Colour
| Token | Value | Use |
|---|---|---|
| colorNeutralBackground1 | #ffffff | panes, cards, inputs |
| colorNeutralBackground2 | #fafafa | option blocks, log pane |
| colorNeutralBackground3 | #f5f5f5 | app canvas |
| colorNeutralBackground4 | #f0f0f0 | nav drawer |
| colorNeutralBackground1Hover / Selected | #f5f5f5 / #ebebeb | row hover / selected neutral |
| colorNeutralForeground1 / 2 / 3 | #242424 / #424242 / #616161 | text primary / secondary / captions (6.2:1 on white) |
| colorNeutralForegroundDisabled | #8a8a8a | disabled controls only |
| colorNeutralStroke1 / 2 / 3 | #d1d1d1 / #e0e0e0 / #f0f0f0 | control border / pane divider / row divider |
| colorNeutralStrokeAccessible | #616161 | input bottom border, switch outline |
| colorNeutralBackgroundInverted | #292929 | tooltip |
| colorBrandBackground (+Hover, Pressed) | #0f6cbd (#115ea3, #0c3b5e) | primary button, switch on, selection bar |
| colorBrandBackground2 | #ebf3fc | selected row, selected bundle, changed patch |
| colorBrandForeground1 / 2 | #0f6cbd / #115ea3 | links, icon hover |
| colorBrandStroke1 / 2 | #0f6cbd / #b4d6fa | selected outline / pipeline line |
| colorStatusSuccess Background1 / Foreground1 / Border1 | #f1faf1 / #0e700e / #9fd89f | Idle, built |
| colorStatusWarning Background1 / Foreground3 / Border1 | #fff9f5 / #8a3707 / #fdcfb4 | Needs URL, warnings |
| colorStatusDanger Background1 / Foreground1 / Border1 | #fdf3f4 / #b10e1c / #eeacb2 | Failed |
| colorPaletteBlue Background2 / Foreground2 | #ebf3fc / #0f548c | origin label "Official catalog" |
| colorPaletteTeal Background2 / Foreground2 | #e9f7f8 / #00666d | origin label "Built-in" |
| colorPalettePurple Background2 / Foreground2 | #f7f0fb / #5c2e91 | origin label "Custom" |
| monogram tiles | #c43501 #b10e1c #ad1a72 #5c2e91 #0f6cbd #00666d #0e700e #005b70 #242424 | app icon placeholder, white text ≥ 5.4:1 |

Status text: Needs you = danger/warning, Building = brand, Update available = brand, Idle = success. All text pairs ≥ 4.5:1 (checked).

### Type (Fluent type ramp; font = Segoe UI Variable → system stack, mono = Cascadia Mono → system mono)
| Token | Size/line | Weight | Use |
|---|---|---|---|
| caption1 | 12/16 | 400 | metadata, hints |
| body1 | 14/20 | 400 | default |
| body1Strong | 14/20 | 600 | row titles, buttons |
| subtitle2 | 16/22 | 600 | section titles |
| subtitle1 | 20/28 | 600 | page title, panel title |
| title3 | 24/32 | 600 | app name in detail |
| title2 | 28/36 | 600 | "next run" time |

### Spacing, radius, elevation, motion
- spacingHorizontal XS 4 · S 8 · M 12 · L 16 · XL 20 · XXL 24 · XXXL 32 (px). Row padding 8×12, pane padding 20×24, page gutter 16.
- borderRadius Small 2 · Medium 4 (controls) · Large 6 (bundle option, patch list) · XLarge 8 (split surface, cards) · Circular (badges, pills).
- shadow2 (selected nav item) · shadow4 (split surface, cards) · shadow8 (tooltip) · shadow28 (mobile nav drawer).
- durationFast 150 ms (hover), durationNormal 200 ms (switch, drawer), curveEasyEase, curveDecelerateMid. `prefers-reduced-motion` turns transitions off.
- Breakpoints: 1100 px (nav collapses to 56 px icon rail), 900 px (split view shows one pane: list, then detail with back button), 640 px (nav becomes an overlay drawer; touch targets 40 px, switches 44×24).

## Android — Material Design 3 (sys token names), light only

Dynamic colour (API 31+): `dynamicLightColorScheme(context)`; the UI uses only roles below, so it re-tints with the wallpaper (sample scheme in `android/screens.html`, "Dynamic colour sample").
Fallback (API 29–30 or no dynamic colour): static scheme, seed #00897B teal, tertiary rose for colour variety.

| Role | Fallback | Dynamic sample (orange wallpaper) | Use |
|---|---|---|---|
| primary / onPrimary | #006A63 / #FFFFFF | #8F4C00 / #FFFFFF | filled button, switch on, sub-headers |
| primaryContainer / on | #9DF2E7 / #00201D | #FFDCC2 / #2E1500 | FAB, "next run" card, Building pill |
| secondary / on | #4A635F / #FFFFFF | #735A47 / #FFFFFF | — |
| secondaryContainer / on | #CCE8E2 / #05201C | #FFDCC4 / #291709 | nav indicator, selected chip, selected bundle, Built-in label |
| tertiary / on | #8B4A62 / #FFFFFF | #5B6237 / #FFFFFF | — |
| tertiaryContainer / on | #FFD9E3 / #3A0720 | #E0E7B1 / #191E00 | Update available, Custom label, avatar |
| error / errorContainer / on | #BA1A1A / #FFDAD6 / #410002 | same | Failed card, badge |
| surface / onSurface / onSurfaceVariant | #F4FBF8 / #161D1B / #3F4946 | #FFF8F5 / #211A15 / #52443B | background, text |
| surfaceContainerLow / Container / High / Highest | #EEF5F2 / #E8EFEC / #E3EAE7 / #DDE4E1 | #FCF2EC / #F6ECE6 / #F0E6E0 / #EAE0DA | cards, nav bar, search bar, switch track |
| outline / outlineVariant | #6F7976 / #BEC9C5 | #85746A / #D7C3B7 | text-field border / dividers, chips |
| custom "warning" (harmonised) | #7A5900, container #FFDF9E / on #261A00 | same | Needs URL |
| custom "success" (harmonised) | #2A6A26, container #ACF49F / on #002203 | #3F6A1F, #BEF297 / #0B2000 | Idle |
| custom "catalog" | container #D6E3FF / on #001B3E | #FFDBCB / #341100 | Official catalog label |

App monogram tiles rotate through primary/secondary/tertiary/error/warning containers (dynamic-colour style, no fixed hues).

- Type (MD3 type scale, Roboto): headlineSmall 24/32 · titleLarge 22/28 · titleMedium 16/24 w500 · titleSmall 14/20 w500 · bodyLarge 16/24 · bodyMedium 14/20 · bodySmall 12/16 · labelLarge 14/20 w500 · labelMedium 12/16 w500.
- Shape: extraSmall 4 (text field) · small 8 (chips, status pills) · medium 12 (cards, monogram) · large 16 (FAB, big monogram) · extraLarge 28 (bottom sheet top, search bar) · full (buttons, switch).
- Elevation: tonal only (surfaceContainer levels); level 3 shadow on FAB and phone-frame only.
- Motion: emphasized cubic-bezier(.2,0,0,1) 300 ms (sheet, screen change), short4 200 ms (switch, state layers). State layers 8 % hover / 12 % pressed.
- Spacing: 4-dp grid; list item 16 dp side padding, 72 dp two-line height, 48 dp icon-button touch target, nav bar 80 dp.
- Components: NavigationBar (3 items, badge on Apps), SearchBar, FilterChip, ListItem, Card (filled/outlined), SegmentedButton (version mode), Switch, RadioButton, Checkbox, OutlinedTextField, ExposedDropdownMenu, ModalBottomSheet (paste URL), ExtendedFloatingActionButton, LinearProgressIndicator, Snackbar for check-update feedback.

## Icon set (inline SVG, `icons.svg`)
24×24 grid, round caps; stroke 1.5 on web (Fluent "Regular" feel, drawn at 20 px), stroke 2 on Android (Material Symbols "Outlined" weight, 24 dp). Same paths on both platforms; Android ports them to `ImageVector`/`PathParser` like today's `object Icons`.

`i-store` Marketplace · `i-apps` Apps · `i-builds` Builds · `i-phone` Devices · `i-follow` Follow projects · `i-calendar` Schedules · `i-settings` Settings / app settings · `i-search` · `i-sort` · `i-chev-r` · `i-chev-d` · `i-back` · `i-menu` · `i-check-update` check for update · `i-rebuild` rebuild · `i-download` download APK · `i-more` · `i-star` GitHub stars · `i-link` paste URL / Needs URL · `i-warning` · `i-error` failed / needs you · `i-ok` idle · `i-check` · `i-clock` · `i-up` update available · `i-spinner` building · `i-close` · `i-plus` · `i-info` · `i-globe` official catalog · `i-play` enable / run now · `i-push` OTA · `i-retry` · `i-stop` stop build · `i-edit` · `i-pin` pinned version · `i-bolt` auto version / logo.

Icon buttons always carry `aria-label` (and a tooltip on web: `data-tip`, shown on hover and keyboard focus).
