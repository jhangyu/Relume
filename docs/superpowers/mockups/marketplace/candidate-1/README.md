# Candidate 1 — "Console" (light only)

Direction: a dense power-user console. Web = Fluent 2 DataGrids (36 px rows, tabular numbers, sortable headers, category facet rail with counts, `/` focuses search); Android = MD3 dynamic-colour lists with the deciding numbers on line two.
Marketplace: one table of all apps; expanding a row shows the bundle picker (stars, patches for this app, supported version, origin label) and the full patch list in two columns, then "Enable and build". A "needs URL" row gets an inline paste form.
Apps: status KPI strip doubles as filter; each row has check-update / rebuild / download / settings icon buttons with tooltips; settings open in a panel that docks beside the table on wide screens (Bundle, Patches+options, Version, Dependencies, Download).
Schedules: one "Update run" card shows the five run steps with last-run numbers; OTA schedules edit inline. `restyle-sample.html` (Builds) shows the shared look for all kept pages; logs are light, no dark surfaces.
Android: nav bar Marketplace / Apps / Schedules; palette switcher shows two dynamic-colour samples and the API 29–30 fallback (seed = today's #2B6AE8).
Trade-offs: density over friendliness (small type, many columns that drop at 1560/1280/760 px); bundle labels "Official catalog" repeat in the picker; the docked panel hides version/bundle columns while open.
Mock data: real names, bundles, stars and patch lists from the 2026-10-26 bundles.json; YouTube patch names, versions and run numbers are illustrative.
Open: where Android device settings live (mockup: account avatar in the top bar); whether built-in bundles get a version label in the catalog list.
Files: web/{marketplace,apps,schedules,restyle-sample}.html + fluent.css + mock.js; android/{marketplace,apps,schedules}.html + md3.css + mock.js; icons.js; tokens.md.
