# Relume — "Workbench" (designer-styling)

**Direction:** a calm admin console in the shadcn/ui + Radix style: neutral zinc surfaces, one emerald accent, and status colour only where it carries meaning. The CSS is hand-written to Tailwind token discipline (4px spacing scale, HSL CSS variables, radius tokens). Mockups work offline. Light and dark themes come from one token set; the default follows the system and the sun button toggles it (saved in localStorage).

**Palette (HSL tokens, light / dark):**
- Surfaces: background `0 0% 100%` / `240 10% 3.9%`; muted `240 4.8% 95.9%` / `240 3.7% 15.9%`; border `240 5.9% 90%` / `240 3.7% 15.9%`
- Primary (ink): zinc-900 / zinc-50. Accent and focus ring: emerald `160 84% 39%` / `160 84% 45%`
- Status: ok green `142 71%`, update amber `32–38 92%`, building blue `217 91%` (pulsing dot), failed red `0 72%`
- The build log is always a dark terminal pane (`240 10% 6%`) with coloured step, ok, warn and error lines

**Typography:** system UI stack (`ui-sans-serif, system-ui, Noto Sans TC`) with no web fonts. 14px base, 24px/600 page titles, small-caps labels at 11px. `ui-monospace` is used for versions, package names, IPs, cron and logs.

**Components:** Card, Button (default / outline / ghost / accent / icon), Badge, Switch, Tabs (arrow-key roving focus), segmented radio, chip toggles, radio cards, Dialog (native `<dialog>`), dropdown menu, Toast (`aria-live`), and OTP input.

**Key interactions:**
- App list: collapsible groups. Native drag-and-drop reorders groups and items, and items can move across groups. On the keyboard, Alt+↑/↓ on a handle does the same. The "local → upstream" patch version is highlighted amber when stale. Update and Rebuild are disabled when they make no sense (already current, already building). Group header: select-all, Update all, Rebuild all, and an overflow menu (OTA group, zip download).
- App detail: tabs for Patches (switches; locked required patches), Options (fieldset per patch plus a raw JSON preview), Version (Auto vs Pin radio cards; pinning enables the version select), and History.
- Build log: a live log appends lines with a Follow toggle, progress bar and Cancel. The history table shows the failure reason inline in red.
- Schedules: list with enable switches and Run now. The plain-language editor covers Hourly / Daily / Weekly / Cron, and the summary sentence, cron string and next 5 runs update live.
- Devices: device cards (online dot, assigned apps editor, microG dependency, OTA now), a discovered-on-network list with a pair dialog, and manual pairing with 6-box code entry (auto-advance and paste fill).
- Settings: one-of-three channel radio cards, each showing its own fields, plus Send test, retention count, GitHub token status (masked) and password change.

**Responsive:** below 768px the sidebar becomes an off-canvas drawer behind a hamburger button. Item rows collapse to icon, name and actions, and tables drop secondary columns.
**Accessibility:** skip link, visible focus ring, labelled controls, `role=log`/`progressbar`/`tablist`, and `prefers-reduced-motion` support.

**Status:** all 7 screens are done and saved.
Done: login, apps (app list), app-detail, build-log, schedules, devices, settings, plus index.html.
Not done: none. Sample data is invented apart from source/app names.
Checks: no http(s) refs; no JS errors in headless Chrome; mobile has no overflow.
