# Polish plan: requirement 1, backend API and web Apps page

Base: `main` @ 3abe409. Paths are relative to `local/webui/` unless they start with `android/` or `tests/`.
Contract acceptance 1 (backend + web half). The Android half is planned by `arch-android-apps` from the "API contract" section.

## Decisions

1. **API shape: a separate `followed` array in the `GET /api/apps` response, each entry with `"source": "followed"`.**
   Why: `apps[]` is an item-id keyed list. Seven consumers depend on that (`app.js:262` badge, `devices.js:188` assignment
   checkboxes, `settings.js:160`, `projects.js:121` usedBy, `builds.js:54` name lookup, `apps.js:107` appOf, Android
   `AppObject` with required `bundle`/`status`/`download`). Project ids live in a different table and collide with item ids.
   Mixing them in one list forces id namespacing plus a guard in every consumer. A second array leaves all seven untouched;
   only the two Apps list screens read `followed`. The `source` field is the marker the spec asks for; the web page merges
   both arrays into one rendered list and branches on it. Not a parallel path: followed entries come from one builder
   (`projects.followed_apps`), and the APK URL formula moves to one helper shared with the OTA planner.
2. **Ids:** a followed entry's `id` is the project id (`project.id`). Clients never send it to `/api/apps/{id}` routes.
3. **Download:** `GET /api/projects/{id}/apks/{file}` (existing, `projects.py:483`). The entry carries the ready URL per file
   in `files[].url`; clients do not build URLs.
4. **Install:**
   - Web (server-side ADB phones): `POST /api/projects/{id}/install` with body `{}` (existing, `ota.py:724`). With no
     `device_ids`, the server installs on every ADB phone assigned to the project. Semantics stay "phones assigned to the
     project", not "any phone": `ota.handle` scopes a job to assigned projects, and assignment is how OTA keeps the app
     updated later. The button is disabled with a hint when `device_ids` is empty ("Assign phones on Followed projects").
   - Android (the phone itself, an app device): same flow as built apps today (`AppModel.beginInstall`): if the project is
     not assigned to this phone, `PUT /api/devices/{me}/assignments` with the current `item_ids` and `project_ids` = current
     + this id, then `GET /api/me/plan?projects={id}` and run the install runner on the returned step. No new endpoint.
     `POST /api/projects/{id}/install` refuses app devices (409 `APP_ONLY`), so Android must not call it.
5. **Badge:** does not count followed apps. It is labelled "enabled apps" and counts `apps[]` only (web `app.js:262-264`,
   Android `RelumeRoot.kt:142`). No change to either badge.
6. **Filters (web):** followed entries show only under the "All" filter and in search. The "All" count becomes
   built + followed and its label becomes "All apps". The other filters stay built-only.

## API contract

`GET /api/apps` (session or device token, unchanged auth) returns:

```json
{
  "apps": [ /* unchanged: built app objects, keyed by item id */ ],
  "followed": [
    {
      "id": 3,
      "source": "followed",
      "name": "MicroG RE",
      "package": "app.revanced.android.gms",
      "icon_url": "/api/apps/app.revanced.android.gms/icon",
      "repo": "MorpheApp/MicroG-RE",
      "version": "v0.3.1.4",
      "blocked": false,
      "files": [
        {"name": "microg-arm64.apk", "abi": "arm64-v8a", "size": 18234567, "version_name": "0.3.1.4",
         "version_code": 240913008, "url": "/api/projects/3/apks/microg-arm64.apk"}
      ],
      "device_ids": [1]
    }
  ]
}
```

| field | type | meaning |
|---|---|---|
| `id` | int | project id. Use with `/api/projects/{id}/...` and `GET /api/me/plan?projects={id}` only. |
| `source` | string, always `"followed"` | marker; `apps[]` entries have no `source` field. |
| `name` | string | project label (display name, else repo name). |
| `package` | string or null | null until the first verified download. |
| `icon_url` | string or null | `/api/apps/{package}/icon`; null when `package` is null. The route 404s for packages in no catalog: show the letter tile. |
| `repo` | string | GitHub `owner/name`. |
| `version` | string or null | downloaded release tag; null when nothing readable is downloaded. |
| `blocked` | bool | signer changed and not confirmed. When true, `files` is `[]` and install is refused (409). |
| `files` | array | APKs of the downloaded tag, ordered by file name. Empty when not downloaded or blocked. |
| `files[].name` | string | file name. |
| `files[].abi` | string or null | `arm64-v8a`, `armeabi-v7a`, `x86`, `x86_64`, `universal`. |
| `files[].size` | int | bytes. |
| `files[].version_name` | string or null | |
| `files[].version_code` | int or null | |
| `files[].url` | string | server-relative download URL (`GET`, same auth as the API). |
| `device_ids` | int[] | ADB phones assigned to the project (app devices are not listed). Web uses it to enable Install. |

Actions per client:
- Download: `GET files[i].url`. Web shows a menu of `files`. Android picks the file whose `abi` matches the phone (the
  `pick_apk` rule: phone ABIs in order, then `universal`) or lets the user choose; that is the Android architect's call.
- Install, web: `POST /api/projects/{id}/install` body `{}` → `200 {"job_ids":[...]}`; `400` when no phone is assigned;
  `409` when blocked.
- Install, Android: assignments + `GET /api/me/plan?projects={id}` (see decision 4). The plan step has
  `kind: "project"`, `ref_id: id`, and `download` with the same URL as `files[].url`.

## Backend edits

### `projects.py` (owner: backend implementer)
1. Add a helper next to `pick_apk` (`:80`), and `import urllib.parse` at the top:
   ```python
   def apk_url(project_id, file):
       return f"/api/projects/{project_id}/apks/{urllib.parse.quote(file)}"
   ```
2. Add after `project_devices` (`:258-261`):
   ```python
   def followed_apps():
       """Apps-list entries of Followed projects (spec polish 1): only what Download and Install need."""
       out = []
       for (pid,) in db.conn().execute("SELECT id FROM project ORDER BY id").fetchall():
           r = _row(pid)
           tag = r["downloaded_tag"]
           facts = _apks(r["upstream_id"], tag) if tag else None
           blocked = bool(_alert(r, facts))
           out.append({"id": pid, "source": "followed", "name": label(r), "package": r["package"],
                       "icon_url": f"/api/apps/{r['package']}/icon" if r["package"] else None,
                       "repo": r["repo"], "version": tag if facts else None, "blocked": blocked,
                       "files": [] if blocked else [
                           {"name": f["file"], "abi": f["abi"], "size": f["size"], "version_name": f["version_name"],
                            "version_code": f["version_code"], "url": apk_url(pid, f["file"])}
                           for f in sorted(facts or (), key=lambda f: f["file"])],
                       "device_ids": project_devices(pid)})
       return out
   ```
   `_row`, `_apks`, `_alert`, `label`, `project_devices` already exist; `_row` is defined after this point in the file but
   is resolved at call time.

### `ota.py`
3. `_project_download` (`:265-266`): replace the inline f-string with `projects.apk_url(p['project_id'], p['file'])`,
   then delete `import urllib.parse` (`:7`): `:266` is its only use.

### `market.py`
4. Import `projects` (`from . import apklocate, apps, build, catalog, db, projects`; no cycle: `projects` does not import
   `market`). `list_apps` (`:109`):
   ```python
   return 200, {"apps": [_with_download(a) for a in apps.list_enabled()], "followed": projects.followed_apps()}
   ```

## Web edits

### `static/apps.js` (owner: web Apps implementer; whole file is this item's region)
5. State: `const S = { apps: [], followed: [], filter: 'all', q: '', menuOpen: false };`
6. `FILTERS[0]`: label `'All apps'`.
7. Add `followedRow(p)` next to `row()` (`:70`). Same 5 grid cells as `.arow`, `data-id="f${p.id}"`:
   - cell 1: `iconTile(p, 's48')`, name as `<a href="/projects.html">`, `p.package || p.repo` in `mono subtle-text`.
   - cell 2: `p.version || '—'`, caption `Followed`.
   - cell 3: `<span class="origin">${icon('follow')}Followed project</span>` and `github.com/${p.repo}` in mono.
   - cell 4: `p.blocked` → `badge err` "Signer changed" + caption link "Confirm on Followed projects" (`/projects.html`);
     else `!p.version` → caption "Not downloaded yet"; else empty.
   - cell 5 `.acts`: Download `<span class="dd"><button class="icon-btn" data-act="dl" …>${icon('download')}</button></span>`
     disabled when `!p.files.length`; Install `<button class="icon-btn" data-act="install" …>${icon('devices')}</button>`
     disabled when `p.blocked || !p.version || !p.device_ids.length`, `data-tip`/`title`: enabled
     `Install on ${n} assigned phone(s)`, no phones `Assign phones on Followed projects first`.
   - No check, rebuild, settings, or status pill.
8. `renderList()` (`:86`): `counts.all = S.apps.length + S.followed.length`; filtered rows =
   built rows as today, then `S.filter === 'all' ? S.followed.filter(match) : []`, where `match` uses
   `${p.name} ${p.package || ''} ${p.repo}`; render `[...rows.map(row), ...fol.map(followedRow)]`. Empty-state check uses
   both arrays.
9. `loadList()` (`:97`): `const r = await api('GET', '/api/apps'); S.apps = r.apps; S.followed = r.followed;`
10. `appOf` (`:107`): `const k = el.closest('[data-id]').dataset.id; return k[0] === 'f' ? S.followed.find(p => p.id === Number(k.slice(1))) : S.apps.find(a => a.id === Number(k));`
11. `openMenu` (`:109`): for `a.source === 'followed'` fill the menu from `a.files` directly:
    `<a role="menuitem" href="${f.url}" download>${f.abi || '?'} · ${f.name} · ${fmt.bytes(f.size)}</a>`; else
    `fillDownloads` as today.
12. List click handler (`:126-146`): add
    `if (t.dataset.act === 'install') await act('POST', \`/api/projects/${a.id}/install\`, {}, \`Installing ${a.name} ${a.version} on ${a.device_ids.length} phone(s)\`);`
    before the `check`/`build` lines. Built-app actions never see a followed entry (its row has no such buttons).

### `static/apps.html` (shared file: two owners)
- **This item's region:** lines 87-90 only (`#summary`, the list `<section>`, `.lhead`). Edits: section
  `aria-label="Apps"`; `.lhead` third label `Patch bundle` → `Source`.
- **Not this item's region:** the `<header>` (lines 75-83: search box, Check all, Add from Marketplace) and every CSS rule.
  That belongs to the toolbar/search restyle implementer. Hand-off note for them: placeholder and sr-only text
  "Search enabled apps" → "Search apps" (followed apps are searchable now).
- No CSS change needed: `.arow` grid and `.origin` exist; `.origin` without an origin modifier renders neutral.

### Not changed (checked)
`app.js` badge and `fillDownloads`, `devices.js`, `settings.js`, `projects.js`, `builds.js`: they read `apps[]` only.
Followed projects page unchanged (spec).

## Files touched
`local/webui/projects.py`, `local/webui/ota.py`, `local/webui/market.py`, `local/webui/static/apps.js`,
`local/webui/static/apps.html` (lines 87-90 only), `local/webui/tests/test_market_api.py`.

## Backend tests that must change
- `tests/test_market_api.py:162`: expected body `b'{"apps": []}'` → `b'{"apps": [], "followed": []}'` (fresh test DB has no
  project rows). No other test asserts the `/api/apps` body; `test_apps.py:255` checks `_app_obj` only.
- No UI-type test to delete for this item: `test_ui_static.py` id-lookup test (`:82`) must stay green — no new `$('#…')`
  ids are introduced.

## Acceptance checks (mechanical)
1. Linux gate green (judge): `docker build -f local/Dockerfile --target app -t relume-gate . && docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell` → RC 0.
2. Scratch check (not committed), `.claude/tmp/polish-be-apps/check.py`, run from repo root with
   `python3 -m` on the `local.webui` package path the tests use: temp data dir, `db.init` + `db.migrate`, insert one
   upstream+project with `downloaded_tag`, write `apks.json` (two facts with `sha256`) plus the two files in
   `upstream.tag_dir(uid, tag)`, then assert:
   `followed_apps()[0]` keys == the contract table keys; `files[*].url == apk_url(...)` and sorted by name;
   setting `project.signer_alert` → `blocked is True and files == []`; `package = NULL` → `icon_url is None`.
   Then `market.list_apps(None)[1].keys() == {'apps','followed'}`. Pattern: `tests/test_projects.py:37-41, 49-54`.
   Log RC to `docs/logs/2026-10-26/polish-be-apps-check.log`.
3. Single URL formula: `grep -n "apks/" local/webui/*.py` → only `projects.py` (`apk_url` and the route string); `grep -n urllib local/webui/ota.py` → no match.
4. Live read-only (after redeploy, logged-in session, optional): `curl -s -b <session> localhost:65427/api/apps | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['apps']), [f['source'] for f in d['followed']])"`.
5. Web, by the user (spec: no UI review by agents): followed rows appear on Apps under "All apps", show only Download and
   Install, Download menu downloads the APK, Install queues an OTA job (visible on Builds/Devices).

## Out of this item
Android display and install wiring (arch-android-apps). Search box/toolbar restyle in `apps.html` header and `app.css`.
