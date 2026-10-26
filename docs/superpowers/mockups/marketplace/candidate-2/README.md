# Candidate 2 — "Storefront" (light only)
- Direction: an app store. Marketplace opens with colourful featured banners and a card gallery; each app is a big gradient icon tile tinted by its store category.
- Expand-in-place: clicking a card opens a full-width panel under its row with three numbered steps: bundle → patches → download source → "Enable and build".
- Colour does the work. Category hues (Fluent shared palette) on tiles, card washes and filter dots; Fluent brand blue stays for actions only. Android uses dynamic colour, with a fallback built from the same blue.
- Apps tab is a library list: status badges, icon-only check/rebuild/download buttons with tooltips; settings open in a right-side drawer (web) or a full screen (Android).
- Schedules: one hero card for the global interval plus the five-step run pipeline; OTA schedules listed below it.
- Trade-offs: pages are long and less dense than a table. Category colour depends on the catalog's 14 store categories (new ones fall back to grey). Monogram tiles stand in for real icons; the real app shows `iconUrl` and keeps the monogram as fallback.
- Open: Enable is blocked until a URL exists (other option: enable now, wait for the URL). On Android, phone settings move to the avatar menu.
- Files: `web/*.html` + `web/fluent.css`, `android/*.html` + `android/m3.css` (light-scheme switch: Dynamic/Fallback), `icons.js`, `tokens.md`.
