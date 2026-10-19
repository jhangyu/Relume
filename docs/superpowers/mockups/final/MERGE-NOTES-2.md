# Merge notes, part 2: build-log, schedules, devices, settings

Sources: A = designer-fd ("Patch Bay"), B = designer-uxpm ("Workbench Ledger", `_src/pages/`), C = designer-styling (the base).
This list covers features that A or B has and C did not have. Every feature is built in C's visual language, with no shared token or component changes. New page-only CSS sits in a second `<style>` block in each file's `<head>`.
Line numbers point to the files in `final/`.

## build-log.html (added 18, skipped 3)

| Feature (source) | Decision | Where / reason |
|---|---|---|
| Queue summary: running · waiting · workers (A, B) | add | build-log.html:187 |
| Queued builds listed, with a Remove action (A) | add | build-log.html:189-191 |
| Build facts: patches ver, app ver, arch, CLI, APK source (A) | add | build-log.html:196 |
| Step tracker with per-step time / detail and live patch counter (A, B) | add | build-log.html:197, CSS :164-168 |
| Trigger name and live elapsed timer in header (B) | add | build-log.html:194 |
| Status flips to Success and Cancel disables when the stream ends (B) | add | build-log.html:234 |
| Wrap toggle (A, B) | add | build-log.html:199 |
| Find in log with match highlight and count (A) | add | build-log.html:199 |
| Line count (A, B) | add | build-log.html:200 |
| Download .log (A) | add | build-log.html:200 |
| Line numbers in log (A) | add | build-log.html:171 (CSS counter) |
| Auto-turn-off Follow when the user scrolls up (B) | add | build-log.html:237 |
| History trigger column: schedule / manual / patch update (A) | add | build-log.html:204 |
| Artifact size column (B) | add | build-log.html:204 |
| Filter "This app" (B) | add | build-log.html:203 |
| Failure row with suggested fix: "Pin version…" / Retry (B) | add | build-log.html:207, :209 |
| Select history row → its log loads into the viewer, row highlighted (A) | add | build-log.html:242 (`show()`) |
| Load older (B) | add | build-log.html:213 |
| History grouped by Running / Queued / Today / date headers (A) | skip | Same info is now in the Queue card and the Started column. Section headers in a table add no new information. |
| Blinking cursor, scanline terminal texture (A) | skip | Pure A theme decoration. |
| Patch-applied list in log ("✓ Hide ads") (B) | skip | This is log content, not UI. C's log already streams per-patch lines. |

## schedules.html (added 17, skipped 2)

| Feature (source) | Decision | Where / reason |
|---|---|---|
| Host timezone and current server time shown (A, B) | add | schedules.html:184 |
| 24 h timeline of upcoming runs with "now" marker, built from enabled schedules (A) | add | schedules.html:185, :251 `tl()` |
| Next run as absolute time and relative "in Xh Ym" (A, B) | add | schedules.html:189 (`.rel`), :229 `rel()` |
| Last run result per schedule (B) | add | schedules.html:193 (and each card) |
| Action-type label on each schedule (B) | add | schedules.html:188 |
| Condition summary per schedule (notify, auto-pull, skip offline/retry) (A, B) | add | schedules.html:192 |
| Night-order explanation Check → Rebuild → OTA (B) | add | schedules.html:203 |
| Check & update vs check only (auto-pull new patches) (A) | add | schedules.html:208 |
| OTA conditions: skip offline + retry count, skip if nothing new (A, B) | add | schedules.html:210 |
| Targets change with action: sources / groups / devices (A, B) | add | schedules.html:218-220 |
| "All items" target and "Pick items…" (A, B) | add | schedules.html:200, :219 |
| Notify on result (B) | add | schedules.html:221 |
| Run now inside the editor; also reachable on mobile (A, B) | add | schedules.html:223 (list Run now no longer hidden on mobile) |
| Next-run list with relative time (A, B) | add | schedules.html:229 |
| Click a schedule → loads into editor, selected card highlighted (B) | add | schedules.html:187, :260 `load()` |
| "New schedule" resets the editor (A, B) | add | schedules.html:265 |
| Paused schedule dimmed with "paused" next run (A) | add | `tl()` toggles `.paused` |
| Coloured patch-cable strip per schedule (A) | skip | A theme decoration. C's per-action icon colour carries the same meaning. |
| Inline "sentence" form with underlined selects (B) | skip | B's visual treatment only. C's segmented controls plus the live sentence summary cover the same function. |

## devices.html (added 21, skipped 2)

| Feature (source) | Decision | Where / reason |
|---|---|---|
| Host mode line: Linux host network · mDNS on (A) | add | devices.html:176 |
| Host preview toggle Linux auto-discover / macOS manual; on mac, discovery is hidden and a note is shown (B) | add | devices.html:177, :202 |
| "Paired devices (n)" heading (B) | add | devices.html:178 |
| Online latency (A) | add | devices.html:181 |
| Rename device (A, B) | add | devices.html:181 (`data-rename`) |
| Assigned apps with installed → available version (B) / "pending" (A) | add | devices.html:184 |
| Dependency "required by" (A, B) | add | devices.html:185 |
| Dependency not-installed state, installs first (A) | add | devices.html:193 |
| Last OTA result on the card (A) | add | devices.html:186, :195 |
| OTA now shows pending count (A, B) | add | devices.html:187 |
| Disabled OTA explains why (B) | add | devices.html:196 |
| Offline guidance: re-enable wireless debugging, re-pair; auto-retry on next schedule (A, B) | add | devices.html:194 |
| Discovery "last seen Ns ago" (A) | add | devices.html:197 |
| Discovered states: Pairing mode / Paired (B, A "new/bound") | add | devices.html:198, :200 |
| Android version in discovery (A) | add | devices.html:198 |
| Pair dialog titled with the chosen phone (A, B) | add | devices.html:225 `openPair()` |
| OTA history filter by device (A) | add | devices.html:210 |
| OTA history trigger column (A) | add | devices.html:210 |
| Multi-app OTA rows with n / n counts (A) | add | devices.html:211 onward |
| "Skipped · offline" row (A) | add | devices.html:212 |
| Uninstall & retry on signature mismatch (B) | add | devices.html:213 |
| Phone illustration with app icons (A) | skip | Decoration. The assigned-apps list already shows the icons. |
| Radar animation (B) | skip | Decoration. The "Scanning" badge plus last-seen carry the state. |

OTA all (A) already existed in C (devices.html:174).

## settings.html (added 13, skipped 2)

| Feature (source) | Decision | Where / reason |
|---|---|---|
| Channel cards list the fields they need (B) | add | settings.html:178 |
| Telegram token help "From @BotFather · stored encrypted" (B) | add | settings.html:179 |
| ntfy optional access token for self-hosted (A) | add | settings.html:181 |
| Last test result (B) | add | settings.html:183 |
| Live retention estimate as keep count changes (A, B) | add | settings.html:185, :205 |
| Disk usage breakdown: APKs / cache / logs / free (A) | add | settings.html:186 |
| Token source path (B) | add | settings.html:189 |
| Rate-limit gauge with reset time (A, B) | add | settings.html:190 |
| "Without a token: 60 req/h" hint (A) | add | settings.html:190 |
| Re-check reports scopes (B) | add | settings.html:192 |
| Password strength meter (A) | add | settings.html:196, :203 |
| Confirm-mismatch error (B) | add | settings.html:197 |
| "Other sessions signed out" count in confirmation (B) | add | settings.html form onsubmit |
| Sign out button in page header (A) | skip | Already in C's sidebar footer on every page. |
| −/+ stepper for keep count (A, B) | skip | C's native number input has the same function (arrow keys, spinner). |
