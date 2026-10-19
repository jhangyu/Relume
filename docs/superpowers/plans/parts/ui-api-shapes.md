# Response shapes the frontend (P6, P9, P10) codes against

Owner of this file: planner-outline. Route owners (planner-build: P2, P4; planner-ops: P5) confirm or correct each row by message. After the merge, these rows become part of the Plan-fixed table. Fields named in the spec keep the spec names. "item" = the catalog item object of spec L574–575. All list responses are wrapped in an object with one key (never a bare array).

| route | owner | success response |
|---|---|---|
| `GET /api/catalog` | P2 | `{groups:[{id, name, color, position, items:[item]}], ungrouped:[item]}` (spec L574) |
| `GET /api/items/{id}` | P2 | item + `version:{mode, pin, allow_nearest}`, `variants`, `engine_options`, `dependency_app_ids`, `apkmirror:{versions, checked_at}`, `upstream:{…, repo, channel}` (spec L576) |
| `POST /api/items` | P2 | 201 `{id}` |
| `PUT /api/items/{id}`, `PUT /api/items/{id}/version`, `PUT /api/items/{id}/patches` | P2 | 200 = the same body as the matching GET |
| `DELETE /api/items/{id}`, `DELETE /api/groups/{id}`, `DELETE /api/sources/{id}` | P2 | 200 `{ok:true}`; 409 `{error}` |
| `GET /api/items/{id}/patches` | P2 | spec L506–510 object |
| `PUT /api/items/{id}/patches` body | P2 | `{overrides:{"<patch>":{enabled?, options?:{key:value}}}}`; entries for patches of the current bundle are replaced by the body; stale entries (names not in the bundle) are kept unchanged; `{overrides:{}}` = reset to bundle defaults; 409 for Xposed items |
| `PUT /api/items/{id}` body | P2 | partial: any of `display_name` (null = default), `group_id` (null = Ungrouped), `enabled`, `variants`, `engine_options`, `dependency_app_ids`; absent fields unchanged |
| `PUT /api/items/{id}/version` body | P2 | `{version_mode, version_pin, allow_nearest}` (spec L520) |
| `GET /api/sources` | P2 | `{sources:[{id, name, engine, upstream_id, cli_upstream_id, keystore, repo, channel, tag, asset_regex}]}` |
| `POST /api/sources` | P2 | 201 `{id}` (the slug) |
| `GET /api/upstreams` | P2 | `{upstreams:[{id, source_ids, kind, repo, channel, tag, asset_regex, latest_tag, latest_published_at, checked_at, check_error, downloaded_tag}]}` |
| `POST /api/upstreams/check` | P2 | `{upstreams:[…same rows…]}` |
| `POST /api/upstreams/{id}/pull` | P2 | `{job_id}` |
| `GET /api/dependency-apps` | P2 | `{dependency_apps:[{id, upstream_id, package, display_name, tag}]}` |
| `GET/POST /api/groups`, `PUT /api/groups/{id}` | P2 | GET `{groups:[{id, name, color, position}]}`; POST 201 `{id}`; PUT 200 `{id, name, color, position}` |
| `PUT /api/groups/order`, `PUT /api/groups/{id}/items/order` | P2 | 200 `{ok:true}` |
| `PUT /api/apps/{pkg}`, `POST /api/apps/{pkg}/icon/refresh` | P2 | 200 `{package, display_name, apkmirror_list_url, apkmirror_example_url, apkmirror_example_url2, apkpure_url}` / 200 `{ok:true}` |
| `POST /api/items/{id}/build` | P4 | 200 `{job_id}` |
| `POST /api/builds` | P4 | `{job_ids:[…]}` |
| `GET /api/items/{id}/artifacts` | P4 | `{artifacts:[{id, job_id, variant, abi, package, version_name, version_code, patches_version, size, sha256, created_at, file_name, download_url}]}` newest first; `file_name` = basename used by the download |
| job `detail`, kind build | P4 | `{item_id, fail_code, fail_detail, app_version, patches_version, cli_version, step, step_index, step_count, artifacts:[{id, variant, abi, size, file_name}]}` (`step*` null unless running) |
| `GET /api/devices` | P5 | `{devices:[{id, name, serial, host, connect_port, model, abi_list, sdk, android_release, ota_after_build, last_seen_at, online, assignments:[…spec L926…], dependencies:[…spec L926…]}]}` |
| `GET /api/devices/discovered` | P5 | `{pairing:[{name, host, port, last_seen}], connect:[{name, host, port, device_id, last_seen}]}` (`device_id` null when unknown) |
| `POST /api/devices/pair`, `POST /api/devices/{id}/connect`, `PATCH /api/devices/{id}`, `PUT /api/devices/{id}/assignments` | P5 | `{device:{…device row + online…}}` without assignments/dependencies (the page reloads `GET /api/devices`); connect failure 409 `{"error":"DEVICE_OFFLINE"}` or `{"error":"UNAUTHORIZED"}`; pair failure 400 `{"error":"PAIR_FAILED: <adb line>"}` (planner-ops X18) |
| `GET /api/devices/{id}/plan` | P5 | `{online, steps:[{kind, ref_id, package, action: install\|up_to_date\|error, reason, artifact\|null}]}`; dependency steps also carry `dependency_app_id, path, tag` |
| `POST /api/devices/{id}/ota`, `POST /api/ota/{job_id}/force-reinstall`, `POST /api/ota/{job_id}/downgrade` | P5 | `{job_id}` |
| job `detail`, kind ota | P5 | `{device_id, item_ids, mode, outcome, installs:[{id, kind, ref_id, package, version_name, version_code, result, error_code, error_text}]}` |
| `GET /api/notify` | P5 | `{type, enabled, config:{…secrets masked…}, events:[…], last_test:{at, ok, detail}|null}`; `type` null when never set |
| `PUT /api/notify` | P5 | 200 = GET body |
| `POST /api/notify/test` | P5 | `{ok, status, detail}` |
| core routes | P1 | `GET /api/jobs` → `{jobs:[job]}`; `GET /api/jobs/{id}` → job; `POST /api/jobs/{id}/cancel` → `{status}`; `GET /api/schedules` → `{schedules:[…]}`; `POST /api/schedules` → 201 `{id}`; `POST /api/schedules/preview` → `{runs}`; `POST /api/schedules/{id}/run` → `{job_ids}`; `GET/PUT /api/settings` → settings object; `GET /api/health`; auth routes per P1.4 |

Errors everywhere: `{error}` with the HTTP status (core §3).
