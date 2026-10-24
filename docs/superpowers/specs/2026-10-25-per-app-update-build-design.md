# Per-app update/build switches and app-based schedules

## 1. Goal

The user picks, per app, whether it is built and whether it follows new versions. Schedules target apps (not patch sources or groups) and one schedule can update and rebuild in the same run.

Root cause of the "Rebuild button cannot be clicked" report: `static/schedules.js` `edit()` disables the other kind radios when an existing schedule is open (kind is immutable on PUT), and the page opens the first schedule on load. The kind split is removed, so the cause goes away.

## 2. Per-app switches (`item` rows)

| Switch | Column | Default for new items | Meaning |
|---|---|---|---|
| Build | `item.enabled` (existing) | off | Off = never built: manual, schedule and auto-rebuild all skip it. |
| Update | `item.auto_update` (new, INTEGER 0/1) | off | On = the item follows new versions (§3). Off = update checks skip it and nothing rebuilds it automatically. |

- Items created through the UI/API are inserted with `enabled = 0, auto_update = 0`. Existing rows keep their `enabled` value; migration sets `auto_update = 0`. Seed import keeps the values in `catalog-seed.json` (seed has no `auto_update` → 0).
- App list (`static/index.js`) shows two switches per item row: Build and Update. Each switch PUTs the item (`enabled` / `auto_update`, bool, validated like `enabled` in `catalog.py`).
- Item page (`static/item.js`) shows the Update switch next to the existing enabled switch.

## 3. "New version" = union of two triggers

An item with Update **and** Build on is rebuilt when either holds:

1. **Patch side**: its source's patches or CLI upstream, or one of its dependency projects, downloads a new tag.
2. **App side**: the item is in version mode `latest` and APKMirror's newest version differs from the `app_version` of its last successful build.

Mode `auto` picks the newest version the patches support, so it changes only with trigger 1. Mode `pinned` never changes. Trigger 2 therefore checks only `latest`-mode items.

### 3.1 Patch side

`upstream.pull(..., rebuild=True)` enqueues builds for `affected_items(upstream_id)` filtered to `enabled = 1 AND auto_update = 1` (today: same source only, `enabled = 1`). The rebuild set is not limited to a schedule's target: an item sharing the source with Update on follows the new patches by its own switch.

### 3.2 App side: new job kind `appcheck`

- Target `{"package": "<pkg>"}`; options `{"rebuild": bool, "notify": bool}`.
- Handler runs a new script `src/build/check-versions.sh <list_url>` in the isolated runner. It reuses `_cf_get` and `emit_apkm_versions` from `src/build/utils.sh` and prints one `::apkm_versions::<csv>` line. One APKMirror list-page request per package.
- Handler stores the list with `catalog.set_apkmirror_versions`. With `rebuild`, it enqueues a build (trigger `upstream`) for each item of that package with mode `latest`, Build on, Update on, and `build.needs_build` true.
- No list URL or fetch failure → job fails with a reason naming the package and the cause (no silent skip).
- `build.needs_build` also returns true for a `latest`-mode item whose `app.apkmirror_versions[0]` differs from `last_success.app_version`.

## 4. Schedules

Schedule kinds become `app` and `ota`. `ota` is unchanged.

### 4.1 `app` schedule

- **Target**: `{"all": true}` or `{"item_ids": [...], "project_ids": [...]}` (at least one non-empty list). Followed projects are listed in the same picker because the old "Check upstream: all sources and projects" schedule was their only scheduled check.
- **Options**: `{"update": bool, "build": bool, "force": bool, "notify": bool}`; at least one of `update`/`build` true; `force` only valid with `build` and without `update`.
- **Fire** (`scheduler.fire`): update work uses targeted items with Update on; build work uses targeted items with Build on:

| update | build | Enqueued |
|---|---|---|
| ✓ | – | `upstream` pull (rebuild false) for every upstream of targeted Update-on items (patches, CLI, dependency projects) and of targeted projects; `appcheck` (rebuild false) per package of targeted Update-on `latest` items. |
| ✓ | ✓ | Same jobs with `rebuild: true`. Builds come from those jobs after their download finishes (§3), so ordering is guaranteed. |
| – | ✓ | `build` per targeted Build-on item; without `force`, only items where `needs_build` is true. |

  Projects ignore `build`; their OTA follows the existing `auto_push` switch. Duplicate upstreams/packages are enqueued once per fire.
- **UI** (`static/schedules.html`, `static/schedules.js`): action segment = App / OTA. App shows two checkboxes Update and Build, plus "Rebuild even if unchanged" (enabled only when Build is checked and Update is not). Target picker: "All apps" chip plus a "Pick apps…" dialog listing items and followed projects. Kind may change on edit; source-chip and group-chip targets are removed.
- Upstream job option `pull: false` (check only) and its `check_only` path are deleted with the old `upstream` schedule kind; manual project/source pulls keep working.

### 4.2 Migration `schema/006_app_schedule.sql`

- `jobs.kind` CHECK adds `appcheck` (table rebuild, same pattern as `005_follow.sql`).
- `schedules.kind` CHECK becomes `('app','ota')`; rows converted in place:
  - `upstream` + `{"all":true}` → `app` + `{"all":true}`.
  - `upstream` + `source_ids` → `app` + `item_ids` of the items of those sources at migration time.
  - Options: `update: true`, `build: <old rebuild, default true>`, `notify` kept, `pull` dropped.
  - `build` + `all` → `app` + `all`; `group_ids` → `item_ids` of items in those groups (0 = ungrouped); `item_ids` kept. Options: `update: false`, `build: true`, `force: not only_changed`, `notify` kept.
  - A converted target that expands to no ids becomes `{"all": true}` and the schedule is set `enabled = 0`, so it never widens silently.
- `item.auto_update INTEGER NOT NULL DEFAULT 0` added.

## 5. Out of scope

- Detecting new app versions for `auto` or `pinned` mode beyond §3.
- Automatic background checks outside schedules and manual buttons.
- Changing OTA schedules, the projects page, or groups.

## 6. Verification

- Suite (core logic only, each test ≤1 s): auto-rebuild filter (§3.1), `needs_build` latest-mode branch, `appcheck` rebuild selection, `app` schedule fire table (§4.1), option validation, migration conversion of each old row shape.
- One-off run (scratch, not committed): start the web UI, toggle both switches on an item, create an app schedule with Update + Build, Run now, see pull/appcheck jobs then builds in Builds.
