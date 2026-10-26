# Android front-end review against the candidate-2 mockups (round 1)

## Verdict: FAIL

The build has 32 must-fix differences and 18 minor differences (counted per file in the table below).

## Setup
- Code: HEAD 84c66f6. Debug APK built with `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools android/gradlew -p android :app:assembleDebug --console=plain --no-watch-fs` (RC=0). It was installed with `adb -s 10AFCF269C007FB install -r` (Success).
- Phone: V2507A, serial 10AFCF269C007FB. Size 1260x2800, density override 474, so 1 dp = 2.9625 px. The screen is 425.3 dp wide. The phone's system font scale is 1.12, so all sp text is 12 % larger than the token sizes. I did not count this as a difference.
- Server: new code from `local/`, run on the migrated scratch copy `.claude/tmp/fe-review-android/data` (check.py printed ALL OK). One catalog refresh gave 1209 store apps and 296 bundles. A hook forbids background processes, so the server runs in-process inside each foreground command (`.claude/tmp/fe-review-android/drive.py`, port 47110, `adb reverse` included). It stops when the command ends.
- Mockups: rendered with headless Chrome, element geometry dumped per screen. Files: `.claude/tmp/fe-review-android/mock/{marketplace,apps,schedules}.png` and `.geo.txt`. The mockup phone screen is 340 dp wide. Edge insets, gaps and heights compare 1:1. Widths scale with the screen.
- Screenshots: `.claude/tmp/fe-review-android/shots/<name>.png`, each with a uiautomator dump `<name>.xml`. Measurements below are dp read from the dumps, or from screenshot pixels / 2.9625.
- Not reached:
  - The empty-catalog state (needs a second empty database).
  - The Installing and Installed card states (no server-built APK on macOS).
  - The APKMirror "page not found" warning card (Instagram's lookup state was "manual").

## Must-fix count per file

| File | Must-fix | Minor |
|---|---|---|
| ui/Components.kt | 9 | 2 |
| ui/AppsScreen.kt | 4 | 1 |
| ui/AppModel.kt | 1 | 0 |
| ui/MarketplaceScreen.kt | 5 | 3 |
| ui/MarketAppScreen.kt | 6 | 4 |
| ui/AppSettingsScreen.kt | 3 | 2 |
| ui/SchedulesScreen.kt | 3 | 3 |
| ui/RelumeRoot.kt | 1 | 2 |
| ui/SettingsScreen.kt | 0 | 1 |

## Differences

### ui/Components.kt (shared; fixing these clears many symptoms on every screen)

1. **must-fix · Section headers sit 4 dp right of the content edge (every screen).** Mockup `.sec-label` x = 16 dp, the same as cards, fields and list text. Implemented: "Patch bundle", "Patches", "Version", "Download URLs", "Last run", "OTA push", "Server" and others are at x = 19.9 dp. In the OTA sheet "Repeat" and "Phones" are at x = 28 dp, while the fields are at x = 24 dp. Shots: 30-detail, 40-appsettings, 46-as-version, 50-schedules, 51-ota-sheet, 60-settings. Code: `Components.kt:236` `.padding(horizontal = 4.dp)`. Fix: remove the 4 dp horizontal padding.
2. **must-fix · Status pills, tags and origin labels have the wrong shape and height.** Mockup `.st`/`.tag`: height 24 dp (20 dp inside list rows), corner 8 dp, padding 0 8 dp. `.origin`: 20 dp high, corner 4 dp, uppercase "OFFICIAL", "BUILT-IN", "CUSTOM". Implemented: fully rounded capsule, about 20 dp high, padding 10/3 dp. Origin labels say "Official catalog" and "Built-in" in sentence case. Shots: 11-apps-clean ("Not installed", "New version", "Phone has"), 30-detail ("Official catalog"), 40-appsettings ("Built-in"), 50-schedules ("Every day at 09:00"). Code: `Components.kt:162-170` (Pill), `Components.kt:192-200` (OriginLabel). Fix: `RoundedCornerShape(8.dp)`, `heightIn(min = 24.dp)`, `padding(horizontal = 8.dp)`. OriginLabel: a 4 dp corner, 20 dp height, and an uppercase short label.
3. **must-fix · The category colour is never applied to real catalog categories.** The tokens map social to pink→lavender, video to coral→amber, tools to teal→blue, and so on. The catalog's category names are "Social & Communication", "Tools & Utilities", "Productivity" and so on. `categoryPastels` matches only "social", "tools", "music", … exactly, so every category except Productivity and Video falls back to blue. Example: the Instagram detail tag "Social & Communication" is blue (#DBE9FF) where pink (#FDE0EF / #8A1650) is expected. Shot: 30-detail. Code: `Components.kt:204-216`. Fix: match on the first word, or add the catalog's full names ("social & communication", "tools & utilities", "photography & design", …). Keep a loud default only for truly unknown names.
4. **must-fix · Switch rows have no vertical padding, so multi-line rows touch each other.** Mockup two-line list item: 72 dp, with 8 dp or more above and below the text. Implemented SwitchRow: `heightIn(min = 56.dp)` and horizontal padding only. In the Marketplace patch list, "Customize navigation bar"'s supporting text ends at y = 308.5 dp and "Disable Reels scrolling" starts at 309.9 dp, a 1.4 dp gap. "Allow nearest version" text is 1.6 dp from the top of its card. Shots: 32-detail-patches, 46-as-version. Code: `Components.kt:423-440`, padding at line 429. Fix: `.padding(horizontal = 16.dp, vertical = 12.dp)` and `heightIn(min = if (help == null) 56.dp else 72.dp)`.
5. **must-fix · Radio rows are 53 dp tall where the mockup has 72 dp.** Mockup bundle option rows (Choose one bundle, Patch bundle) step 72 dp (258→330→402). Implemented: 53.0 dp per row (272.7→325.7→378.7). Shots: 30-detail, 40-appsettings. Code: `Components.kt:443-456`. Fix: `heightIn(min = 72.dp)` when the row has two lines (or always for bundle rows). Keep the radio and the trailing label centred.
6. **must-fix · Buttons have no small or large size.** Mockup card Install/Update buttons are `btn small`: 32 dp high, 14 dp padding. Full-width bottom actions ("Next: download source", "Enable and build") are `btn large`: 48 dp high, 16 dp corner, 14 sp. Implemented: every ActionButton is the M3 default, 40 dp high and fully rounded. Card Install is 40 dp; Next / Enable is 40 dp and fully rounded. Shots: 11-apps-clean, 31-detail-chosen, 34-download-step. Code: `Components.kt:245-275`. Fix: add a size parameter (Small = 32 dp, contentPadding 14 dp; Large = 48 dp, `RoundedCornerShape(16.dp)`, labelLarge 14 sp). Use Small on the card PhoneRow buttons and Large on the bottom actions.
7. **must-fix · ChipRow cannot wrap, so inside a card it is clipped.** In the Schedules interval card the chip row scrolls sideways and runs under the card edge: "Custom" shows as "Cus", cut at x = 409.5 dp. The mockup wraps the chips into two rows inside the card (1 h, 3 h, 6 h, 12 h / 24 h, Custom). Shot: 50-schedules. Code: `Components.kt:371-396` (used at `SchedulesScreen.kt:182`). Fix: a wrapping (FlowRow) variant for in-card use. App settings already has `FlowChips`, so reuse it in IntervalCard.
8. **must-fix · Search uses an outlined text field, not the MD3 search bar.** Mockup `.searchbar`: 56 dp, fully rounded, surfaceContainerHigh #E8E8E8 fill, no border, 16 dp leading inset, trailing sort icon button, placeholder "Search 1,207 apps". Implemented: outlined field with a 4 dp corner, a #767676 border and a floating label "Search apps". Its touch box is 64.8 dp. Shot: 21-market-featured. Code: `MarketplaceScreen.kt:56-59` (calls `Field`, `Components.kt:472`). Fix: a SearchBar-style composable (filled, `RoundedCornerShape(50)`, 56 dp) with the sort action trailing.
9. **must-fix · Plain lists are drawn inside white inset groups, so their text is indented 16 dp too far.** In the mockup the patch list (Marketplace detail and App settings), the "Depends on Followed projects" list, the run pipeline and "Allow nearest version" sit directly on the grey surface: text x = 16 dp, dividers between rows. Implemented: all are wrapped in a white `InsetGroup`, so the text is at x = 31.7 dp and the dependency text at x = 87.8 dp (mockup 72). Inset groups belong only to the bundle choice (tokens §5). Shots: 32-detail-patches, 40-appsettings, 46-as-version, 50-schedules. Code: `Components.kt:228-232` (InsetGroup), used at `MarketAppScreen.kt:109`, `AppSettingsScreen.kt:226`, `236`, `351`, `SchedulesScreen.kt:128`. Fix: render these as plain lists with `Hairline` dividers and 16 dp insets.
10. **minor · The app tile corner and colour do not follow the tokens.** Mockup tile corner = 0.3 × size (12 dp at 40 dp, 19.2 dp at 64 dp), and the gradient comes from the app's category. Implemented: corner = size / 4 (10 dp, 16 dp), and the colour pair is picked by `label.hashCode()`. Code: `Components.kt:541-547`. Fix: `RoundedCornerShape(size * 0.3f)`, and take the pastel pair from `categoryPastels(category)`.
11. **minor · The avatar colour is primaryContainer, not lavender.** Mockup avatar: #E8E0FF background, #4A2A8F text. Implemented: #E6E1FF / #34248C. Code: `Components.kt:359`, `:364`. Fix: `relume.lavender.container` / `.onContainer`.

### ui/AppsScreen.kt (Apps tab)

12. **must-fix · The card status pill is top-aligned, not centred on the tile.** Mockup `.top` uses `align-items: center`: the pill centre (180) equals the tile centre (180). Implemented: the pill spans y = 187–207 dp (centre 197), but the tile spans 186.7–226.9 dp (centre 206.8). The pill sits 10 dp high. Shot: 11-apps-clean. Code: `AppsScreen.kt:105` `verticalAlignment = Alignment.Top`. Fix: `Alignment.CenterVertically`.
13. **must-fix · Card padding and the gap from tile to text are off.** Mockup card padding top is 16 dp, and the tile→text gap is 16 dp (title x = card + 72). Implemented: top 12.2 dp, gap 12 dp (title x = card + 68.2). Shot: 11-apps-clean. Code: `AppsScreen.kt:104` (`top = 12.dp`), `:105` (`spacedBy(12.dp)`). Fix: `top = 16.dp`, `spacedBy(16.dp)`.
14. **must-fix · The install row has no divider and is cramped.** Mockup `.inst`: full-bleed top border in outlineVariant #C8C8C8, min-height 56 dp, padding 8 16 dp. Its content is vertically centred, with 8 dp between the pill and the button. Implemented: no divider, and only an 8 dp bottom padding. Row 2 (the check and rebuild buttons) also starts right at the tile bottom: the check/rebuild glyph top is 12 dp below the tile, where the mockup has 32 dp (header bottom 200 → glyph 232, with a 4 dp margin plus a 48 dp row). Shot: 11-apps-clean. Code: `AppsScreen.kt:156-175` (PhoneRow), `:118` (the acts Row). Fix: put a `Hairline` above PhoneRow that cancels the card's side padding. Give PhoneRow `heightIn(min = 56.dp)` and `padding(vertical = 8.dp)`. Add `padding(top = 4.dp)` to the acts row and give it `heightIn(min = 48.dp)`.
15. **must-fix · An empty Apps list says "No apps enabled" after a failed load.** I launched the app while the server was down (the first load failed), then opened the Apps tab once the server was up. The tab showed "All 0 · No apps enabled. Enable one from the Marketplace." with no error banner, although 2 apps are enabled on the server. A pull-to-refresh then showed both. Shot: 11-apps-clean (first capture, now overwritten; the same state is in FAIL.png/xml from 15:02). Code: `AppsScreen.kt:73-75` shows the empty text whenever `all.isEmpty()`. Fix: show the empty text only after a successful load (a `loaded` flag). Otherwise show the load error. See also item 16.
16. **minor · The "New version" pill has a sync glyph that the mockup does not have.** Mockup `.st.upd` "New version" has no icon. Code: `Components.kt:180` (`R.drawable.ms_sync`). Fix: drop the icon.

### ui/AppModel.kt

17. **must-fix · Load errors are wiped by the next request.** `call()` sets `error = null` at the start of every request (`AppModel.kt:126`). So a failed `load()` banner disappears as soon as any other screen makes a call, which causes the silent empty state in item 15. Fix: clear `error` only on success, or keep the load error separately until a load succeeds.

### ui/MarketplaceScreen.kt (Marketplace list)

18. **must-fix · The featured carousel is scrolled out of view on first show.** The featured item is added to the LazyColumn after page 1 has loaded. LazyColumn keeps the first visible key (#Notepad), so the carousel ends up above the viewport. You only see it by pulling the list down. On a fresh start the screen opens at "#Notepad". Shots: 20-market (no carousel), 21-market-featured (after one swipe down). Code: `MarketplaceScreen.kt:68-70`, `MarketModel.kt:107-113`. Fix: always reserve the item slot with a fixed key (`item(key = "featured")`), even while it is empty. Or call `listState.scrollToItem(0)` when `featured` first becomes non-empty.
19. **must-fix · The featured cards do not match the carousel.** Mockup: a multi-browse carousel. The large item is 224 × 176 dp with a 28 dp corner and a category gradient (c1→c2). It has an uppercase "FEATURED" label, a white 48 dp app tile at top right, and a 16 sp title. Medium (56 dp wide) and small (32 dp wide) items follow. Implemented: equal 200 × 112 dp cards, flat colours cycled lavender/blue/teal/amber, no app tile, and "Featured" in sentence case. Shot: 21-market-featured. Code: `MarketplaceScreen.kt:108-130`. Fix: hero height 176 dp and width 224 dp. Use a gradient from `categoryPastels(app.category)` and add a 48 dp AppTile on white. Make the label uppercase, and render the following items narrow (56 / 32 dp).
20. **must-fix · Category chips have no category colour.** Mockup category chips: a 10 dp gradient dot and a border in on-container at 70 % over white. The label uses the on-container colour; the selected chip uses the category container. Implemented: neutral #767676 border and grey label for all of them. Shot: 21-market-featured. Code: `MarketplaceScreen.kt:60` with `Components.kt:371`. Fix: a category-chip variant per tokens §2 "Filter chips".
21. **must-fix · The "All apps" header and the sort button are missing.** Mockup: a section header "All apps" at x = 16, and a text button "Most bundles" with a sort icon at the right edge (y = 408). Implemented: neither, so there is no way to sort. Shot: 21-market-featured. Code: `MarketplaceScreen.kt:63-71`. Fix: add the header row with the sort menu (Most bundles, Most stars, Recently updated, Name A–Z per spec §7 / §2.5).
22. **must-fix · List rows have no category tag and use the wrong trailing inset.** Mockup supporting line: a category tag (pastel, 20 dp) plus "11 bundles". The trailing item (On pill or chevron) has its right edge 24 dp from the screen edge. Implemented: plain text "1 bundles · Productivity". The chevron's right edge is about 16 dp from the screen edge. Shot: 21-market-featured. Code: `MarketplaceScreen.kt:75`, `Components.kt:408` (`padding(horizontal = 16.dp)`). Fix: a supporting row with `CategoryTag` + count. Give ListRow `padding(start = 16.dp, end = 24.dp)`.
23. **minor · "1 bundles": the plural is wrong.** Code: `MarketplaceScreen.kt:75`, `:128`. Fix: singular for 1.
24. **minor · The top app bar has an extra "Refresh catalog" sync icon.** Mockup Marketplace bar: add-link + avatar only. Code: `MarketplaceScreen.kt:51`. Fix: drop it, or keep it if the lead accepts the web parity.
25. **minor · The header line "1207 apps · 296 community bundles · built-in · 0 custom" is not in the mockup** (the mockup puts the count in the search placeholder). Code: `MarketplaceScreen.kt:63`. Fix: move the count into the search placeholder, or accept it.

### ui/MarketAppScreen.kt (app detail and download-source step)

26. **must-fix · The bundle list is not collapsed and is titled differently.** Mockup: header "Choose one bundle" plus a "2 more" text button, with 3 bundles shown. Implemented: "Patch bundle" and all 11 Instagram bundles, so the patches start below the fold. Shot: 30-detail. Code: `MarketAppScreen.kt:75-89`. Fix: show 3, add an "N more" text button (right edge pulled out 12 dp), and rename the header.
27. **must-fix · No dividers between bundle rows.** Mockup inset group: dividers from the text start. Implemented: no `Hairline` in this list (App settings has them). Shot: 30-detail. Code: `MarketAppScreen.kt:77`. Fix: `if (i > 0) Hairline()` as in `AppSettingsScreen.kt:171`.
28. **must-fix · The patches header has no count and no Reset.** Mockup: "Patches · 12 on" plus a "Reset" text button. Implemented: "Patches" only. Shot: 32-detail-patches. Code: `MarketAppScreen.kt:108`. Fix: reuse the header row from `AppSettingsScreen.kt:338-345`, with Reset clearing `patchChanges`.
29. **must-fix · "Changed by you" is amber and sits outside the row.** Mockup `.st.mine` is pink (#FDE0EF / #8A1650) and sits under the patch title inside the row. Implemented: amber, placed after the whole SwitchRow. Code: `MarketAppScreen.kt:118`. Fix: pink, inside the text column (as `AppSettingsScreen.kt:365` already does).
30. **must-fix · The download-source step keeps the big hero and does not show the choice.** Mockup: app bar title "Download source". Below it, a 40 dp tile row with the name and "Patcheddit · 12 patches · Auto version". Implemented: app bar "Instagram", the 64 dp hero again, then a section header "Download source"; the chosen bundle is not shown. Shot: 34-download-step. Code: `MarketAppScreen.kt:50`, `:63-72`, `:124-130`. Fix: when `downloadStep` is true, set the AppBar title to "Download source" and replace the hero with a ListRow (tile 40, name, bundle · patch count).
31. **must-fix · Bottom action buttons are 40 dp and fully rounded.** Mockup `btn large` is 48 dp with a 16 dp corner. "Enable and build" also has a check glyph. Shots: 31-detail-chosen, 34-download-step. Code: `MarketAppScreen.kt:97`, `:100` (see item 6). Fix: the Large size, and icon `ms_check` on Enable.
32. **minor · The detail app bar repeats the name.** The mockup detail bar has only Back; the name is in the hero. Code: `MarketAppScreen.kt:50`. Fix: empty title on the bundle step.
33. **minor · Missing download-step parts.**
    - The link icon is grey #474747; the mockup uses amber #6B4500.
    - There is no paste icon button in the APKMirror field.
    - There is no helper text "The page that lists every version of the app.".
    - The "After you enable" explanation row is missing.
    - The `not_found` state is plain text, where the mockup has an amber warning card.

    Shot: 34-download-step. Code: `MarketAppScreen.kt:124-130`. Fix: add these per mockup screen 3.
34. **minor · The bundle supporting text order differs.** Mockup: "814 ★ · 14 patches". Implemented: "68 patches · 5785 stars". Code: `MarketAppScreen.kt:82`.
35. **minor · The version-range tag is missing in the hero.** Mockup: a neutral tag "1.12.10 – 1.12.12" next to the category. Code: `MarketAppScreen.kt:65-69`. Fix: add it if the detail has the supported range.

### ui/AppSettingsScreen.kt (App settings)

36. **must-fix · Patch rows have no gap between the text and the switch.** Text runs into the switch: "…to keep it in" ends where the switch starts, and "Remember live stream playback position" touches it. The mockup has 16 dp. Shot: 43-as-mid. Code: `AppSettingsScreen.kt:356` (Row without `horizontalArrangement`). Fix: `horizontalArrangement = Arrangement.spacedBy(16.dp)`.
37. **must-fix · The Reset text is 12 dp inside the content edge.** Mockup: the text button is pulled out 12 dp, so the "Reset" text ends at the 16 dp content edge, in line with the switches. Implemented: the text ends at x = 397.3 dp; the content edge is 409.3 dp. Shot: 40-appsettings. Code: `AppSettingsScreen.kt:340-344`. Fix: `Modifier.offset(x = 12.dp)` or a negative end margin on the text button, as the mockup `.sec .btn.text { margin-right: -12px }` does.
38. **must-fix · The APKMirror field is shortened by a search icon placed outside it.** Mockup: the search icon button is inside the field as its trailing icon, and the field is full width (16→324). Implemented: the field ends at x = 361.5 dp and the icon sits outside, so this field is 48 dp narrower than the APKPure field below it (ends at 409.5). Shot: 45-as-version. Code: `AppSettingsScreen.kt:253-256`. Fix: pass the icon as the field's trailing icon (`Field(trailingIcon = …)`) and drop the Row.
39. **minor · The app bar shows the package name as a subtitle.** The mockup bar has only the title. Code: `AppSettingsScreen.kt:100`.
40. **minor · Display name is a separate section.** In the mockup the app name is an inline option ("App name") under Custom branding. Code: `AppSettingsScreen.kt:273-274`. Fix: none needed if the lead keeps the web field set (spec §7.4). Listed for completeness.

### ui/SchedulesScreen.kt (Schedules)

41. **must-fix · "Run now" is full width.** Mockup: a compact filled button, 119.6 × 40 dp, left-aligned in the card. Implemented: 361.9 dp wide. Shot: 50-schedules. Code: `SchedulesScreen.kt:186` (`fillMaxWidth()`). Fix: drop `fillMaxWidth()`.
42. **must-fix · The run pipeline is a white list with 40 dp circles, not a timeline.** Mockup: 20 dp coloured glyphs at x = 26 dp joined by a vertical connector line, text at x = 72 dp, on the grey surface. Implemented: 40 dp pastel circles inside a white inset group with dividers, text at x = 87.8 dp. Shot: 50-schedules. Code: `SchedulesScreen.kt:128-137`. Fix: a 24 dp lead column with the glyph and a 1 dp outlineVariant connector, and no InsetGroup.
43. **must-fix · Interval chips are clipped (see item 7).** Code: `SchedulesScreen.kt:182`. Fix: FlowChips.
44. **minor · The interval card corner is 16 dp; the mockup `gcard` has 28 dp.** Code: `SchedulesScreen.kt:171` (InsetGroup). Fix: a 28 dp shape for this card.
45. **minor · OTA sheet layout.**
    - The content inset is 24 dp; the mockup has 16 dp.
    - "At" and "Retries" are stacked full width; the mockup puts them side by side (150 / 93 dp).
    - Cancel / Create are equal halves; the mockup right-aligns a text Cancel and a compact filled Create.

    Shot: 51-ota-sheet. Code: `SchedulesScreen.kt:252-262` (SheetFrame padding), `:233`, `:305`, `:310-312`.
46. **minor · The pipeline subtitles are fixed descriptions, not last-run results.** The mockup shows "297 bundles, unchanged", "Piko 3.3.0 is new", "1 built · 1 failed". Code: `SchedulesScreen.kt:131` (`st.detail`). Uncertain whether the API returns per-step results.

### ui/RelumeRoot.kt (navigation, avatar)

47. **must-fix · The avatar initials change between launches.** They come from the phone's device name ("V2" for V2507A) and fall back to "R" until the device loads. I saw "R" after a launch, then "V2". The mockup shows the account initials ("AD"). Shots: 11-apps-clean ("R"), 10-apps ("V2"). Code: `RelumeRoot.kt:106`. Fix: use the signed-in account's initials (admin username), and keep them stable.
48. **minor · The Apps tab has no count badge.** Mockup: a red #BA1A1A badge "6" on the Apps icon (enabled-app count). Code: `RelumeRoot.kt:151-160`. Fix: `BadgedBox` with the enabled count.
49. **minor · The selected tab label is not bold.** Mockup selected label: weight 700. Code: `RelumeRoot.kt:155`. Fix: bold the label when selected.

### ui/SettingsScreen.kt (behind the avatar; no mockup)

50. **minor · "Notifications · On" is a read-only row.** Every other settings screen uses switches for on/off. There is no mockup to compare against, so this is advisory. The section headers here also have the 4 dp offset from item 1. Shot: 60-settings.

### Checked and matching (no action)
- The top app bar is 64 dp, its title at x = 15.9 dp. The avatar is 32 dp with its right edge 11.8 dp from the screen edge, and trailing glyphs end on the 16 dp edge. The back icon is at x = 16.2 dp.
- Chip height is 31.7 dp, the first chip is at x = 16 dp and the gap is 8 dp. The selected chip is #DBE9FF with a check glyph.
- Marketplace list rows: 72 dp tall, tile x = 16 dp, text x = 72 dp, tile centred on the row (281.7 vs 281.65).
- Detail hero: 64 dp tile at x = 16; name, package and tag fit the tile height.
- The App settings bottom bar is grey; its buttons are 39 dp tall with an 8.9 dp gap; the Save border is sky and Save & rebuild is violet with a build glyph.
- The enable switch's right edge is 16 dp from the screen edge.
- The OTA FAB: right and bottom insets are 16 / 15.9 dp, colours #FFE1D5 / #7D2A0E.
- Nav indicators: amber, blue and teal per tab.
- The palette is fixed, with no dynamic colour; surfaces are grey #F8F8F8; Install/Update are coral #B85A35.

## Notes and uncertainties
- System back from the download-source step left the app after two presses. Not verified as a bug: it may be one back to the list plus one exit.
- The keyboard on this phone is Gboard Zhuyin. To type the login I switched it to QWERTY with the on-screen globe key, then switched it back to Zhuyin. No system setting was changed.
- Font scale 1.12 inflates text heights compared with the mockup. All the gap and inset findings above do not depend on font size.

## Round 2 (PARTIAL: stopped on the lead's request; the user took over device testing)

- Build: debug APK at HEAD 3bfcf65 (RC=0), installed on 10AFCF269C007FB (Success).
- Covered: the Apps tab, the Marketplace list header and its first rows, and the Schedules interval card and pipeline top.
- Not covered: app detail, the download-source step, App settings, the OTA sheet, Settings, the not-found warning card, and a full regression pass.
- Shots: `.claude/tmp/fe-review-android/shots/r2-*.png` and `FAIL.png`.

### Fixed (verified on the phone)
- Item 12 (Apps card pill): it is now 23.6 dp high and centred on the tile (pill centre 210.3, tile centre 210.5 dp).
- Items 13 and 14 (Apps card spacing and install row):
  - Card top padding is 16 dp. The title is at card + 71.9 dp. Row 2 glyphs sit 16 dp below the tile.
  - A full-bleed divider sits above the install row. The row is 57 dp. The Install button is 31 dp tall and centred with the pill. The pill and the button both end 16 dp from the card edge.
- Item 16: the "New version" pill has no glyph.
- Item 47: the avatar is the `person` glyph.
- Item 48: the Apps tab shows badge "2".
- Item 8: search is a filled, fully rounded 56 dp bar with the placeholder "Search 1,207 apps" and a trailing sort button.
- Item 21: the "All apps" header and the "Most bundles" sort text button are present.
- Item 22: list rows show a category tag plus "11 bundles".
- Item 1: section headers are at x = 15.9 dp ("All apps", "Last run …").
- Items 7, 41, 43: interval chips wrap inside the card, and "Run now" is compact (about 132 × 40 dp).
- Item 42: the pipeline uses 40 dp gradient tiles with a connector; text is at x = 71.9 dp.

### New or still open (partial; must-fix)
- R2-1 · Schedules interval chips: the wrapped rows are 24.6 dp apart. The mockup has 8 dp (rows at 212 and 252, chips 32 dp). Measured: row 1 at y = 353.5–385.2, row 2 at 409.8–441.5 dp. Cause: the chips keep 48 dp touch boxes, and 8 dp spacing is added between them. Fix: set the FlowRow vertical spacing so the visible gap is 8 dp, for example spacing 8.dp minus (48 − 32) dp of touch padding, or chips with a minimum interactive size of 32 dp. Owner: fix-android-screens (SchedulesScreen.kt IntervalCard) or fix-android-comp (FlowChips).
- R2-2 · The Marketplace list stays empty when its first load fails, with no error and no loading text. Seen once: the screen showed only the search bar and the "All" chip, with no banner. The scratch server had stopped between commands. The list filled after the next visit. This is the same silent-empty pattern as round-1 items 15 and 17, now on Marketplace. Owner: fix-android (MarketModel/MarketplaceScreen).
- Not verified: whether the featured carousel now shows at the top (item 18). In the one loaded capture, "All apps" sits right under the chips with no carousel above it. The capture was interrupted before the pull-down check. Owner: fix-android.
- Not verified: item 46 ("Last update run" block). The header still reads "Last run Oct 26 · 10:57" with static step subtitles. The part below the fold was not reached.
