# Web UI for the local build — Design

Date: 2026-10-19. Requirements: `docs/logs/2026-10-19-webui-contract.md`. Facts: `docs/logs/2026-10-19-preflight-{build,patches,runtime}.md`. File references in the "today"/upstream fact tables describe upstream commit 9fd15dc. Most of those files are deleted, so names (functions, workflow jobs, steps), not lines, are cited.
This file merges seven parts: Core (topology, web stack, auth, persistence, API, scheduler), Catalog (sources, apps, items, patches, upstream), Build (build orchestration), Device (wireless ADB, OTA, notifications), UI (Part 5: mockup ↔ spec mapping, frontend files), Xposed (Part 7: LSPatch/NPatch module items); Part 6 (CI, GitHub Actions builds) was dropped, see §0.6 Q1. A reference "core §N", "catalog §N", "build §N", "device §N", "ui §N", "xposed §N" means section N of that part below. User decisions: §0.6.

## 0.1 Summary

One Docker Compose file, five containers. The existing builder container becomes `app`: it serves a stdlib-only Python web UI on port 47000, keeps all state in SQLite plus files under `local/data/`, and runs builds, upstream pulls and `adb` installs as child processes. `app` and the three anti-Cloudflare sidecars share the network namespace of an idle anchor container `netns`; the sidecars listen on loopback only. Linux uses host networking (phone discovery by mDNS); macOS uses bridge networking (manual phone IP and port). The only platform difference is `RELUME_NETWORK_MODE` in `local/.env` (core §1). The org scripts, `src/options`, `src/patches`, `apps.json` and `local/build.sh` are replaced by catalog rows and one generic build runner (catalog §8, build §7). GitHub Actions builds are dropped (§0.6 Q1). Xposed module apps (LSPatch/NPatch) are a second item type on the same runner (xposed §2). The frontend is the user-chosen "Workbench" mockup ported to static files (ui §1).

## 0.2 Requirement coverage

| requirement | where |
|---|---|
| 1 Scheduled upstream updates | schedules of kind `upstream` (core §6) run pull jobs (catalog §5.1–5.4) |
| 2 App list = (source × app), groups, ordering, icons | catalog §3, §3.1–3.3 |
| 2 Buttons "Update upstream", "Rebuild", "Download" | catalog §5.2 (`POST /api/upstreams/{id}/pull`), build §3.4 (`POST /api/items/{id}/build`, `GET /api/artifacts/{id}/download`) |
| 3 Wireless ADB, one-click and scheduled OTA | device §4.1–4.3, schedules of kind `ota` (core §6) |
| 4a Live log, history with failure reason, retention | core §5 and §8 (jobs, SSE log), build §5 (fail codes), build §6 (retention) |
| 4b Notifications (one of Telegram, Discord, ntfy) | device D9, §4.4, §6 |
| 4c Web editing of patch selection, options, version pin | catalog §4, §4.1, §4.2 |
| 4d Multiple phones, per-phone apps, followed projects | device §3.1 (`device_assignments`), §4.3; followed projects (`docs/superpowers/specs/2026-10-23-follow-project-design.md` §3: `project`, `item_dependency`, `device_projects`) |
| Linux host / NAS (host network, mDNS) | core §1, §12; device D3, D4 |
| macOS Docker Desktop (manual IP:port + code) | core §1, §12; device D4, §4.1 |
| Single admin (username + password, created on first start), LAN | core §7 (plain HTTP, decision Q6) |
| Web UI screens (final mockup, 7 screens) | ui §2 (every element mapped, added or dropped), ui §1 (frontend files) |
| GitHub Actions builds dropped (decision Q1) | none: the web app image is built locally, the Android companion app by its own workflow |
| Xposed / LSPatch / NPatch items kept (decision Q2) | xposed §2–§9 |
| Decisions Q3–Q5 (seed BiliRoamingM/Dropped-Patches, auto rebuild, seed ABIs) | catalog §8 (Q3, Q5), catalog §5.2 + core §6 (Q4) |
| Restored mockup features (§0.6 R1–R6): group zip download, "keep me signed in", option colour picker, newer APKMirror versions, login server summary, group colour | build §3.4 (`GET /api/groups/{id}/download`); core §7, §8 (`remember`); ui §2 App detail (colour picker, client); build §3.2, §4 step 5d + catalog §3, §6 (`apkmirror_versions`); core §8 `GET /api/health` + ui §2 Login; catalog §3, §3.1, §6 (`grp.color`) + ui §2 App list |
| Deferred mockup features (§0.6 F1–F3): cron editor, locked required patches, device latency | not in v1; next phase |

## 0.3 Shared contracts (binding for all parts)

Tables and owners (one schema file per owner, core §4):

| owner | tables |
|---|---|
| core `001_core.sql` | `settings`, `sessions`, `jobs`, `schedules` |
| build `002_build.sql` | `build_jobs` (detail of a `jobs` row, CASCADE), `artifacts` (RESTRICT) |
| catalog `003_catalog.sql` | `upstream`, `source`, `app`, `grp`, `item`, `bundle_info`; `005_follow.sql` rebuilds `upstream` (kind `project`), `item_dependency`, `ota_installs` and adds `project`, `device_projects` (`docs/superpowers/specs/2026-10-23-follow-project-design.md` §3.1) |
| device `004_device.sql` | `devices`, `device_assignments`, `ota_jobs` (detail of a `jobs` row, CASCADE), `ota_installs` |

Job kinds, atomic targets, triggers: `build` `{"item_id":N}`, `upstream` `{"upstream_id":N}`, `ota` `{"device_id":N}` (all assignments) or `{"device_id":N,"item_ids":[…]}` (only these items). Triggers: `manual`, `schedule`, `upstream` (build enqueued by a pull with `rebuild`), `after_build` (OTA enqueued by `build.succeeded`). Core status values: `queued`, `running`, `success`, `failed`, `cancelled`. One serial worker per kind (core §5).

Per-job flags live in `jobs.options` (JSON, core §4). `jobs.enqueue` stores them normalized: only the keys below for that kind, missing keys filled with the default (core §5). Keys and defaults: upstream `pull` (true), `rebuild` (false); build none besides `notify`; ota `retries` (0), `mode` (`normal` | `force_reinstall` | `downgrade`, default `normal`), `package` (null; required for the two recovery modes, device §3.2); every kind `notify` (true). The schedule option `only_changed` (build) is used at schedule expansion and is not stored on the job (core §6).

Delete rules (`PRAGMA foreign_keys=ON`): deleting an item first deletes its artifact files (`build.forget_item(item_id)`, build §3.4), then the `item` row; `build_jobs`, `artifacts`, `device_assignments` and `item_dependency` rows of the item go by `ON DELETE CASCADE`; `jobs` rows stay (target JSON only). The route answers 409 while a build job of the item is queued or running. Deleting a device cascades `device_assignments`; `ota_jobs` and `ota_installs` keep `device_id` as a plain column without FK, so OTA history stays. Groups: items move to Ungrouped (catalog §3.1). Sources: 409 while items use them (catalog §6).

Events (`events.emit` / `events.subscribe`, core §5):

| event | emitted by | payload | consumers |
|---|---|---|---|
| `upstream.new_version` | catalog (check or pull, once per new tag) | `{upstream_id, tag, item_ids, job_id}` (`job_id` = the upstream job that ran the check; null for `POST /api/upstreams/check`) | notifier |
| `build.succeeded` | build | `{item_id, job_id, app_version, patches_version, artifacts}` | notifier, device (OTA after build) |
| `build.failed` | build | `{item_id, job_id, fail_code, fail_detail}` | notifier |
| `ota.done` | device | `{job_id, device_id, outcome}` | notifier |
| `ota.failed` | device | `{job_id, device_id, reason}` | notifier |
| `device.offline` | device | `{job_id, device_id}` | notifier |

In-process functions between parts: `catalog.resolve_items(target)`, `catalog.resolve_upstreams(target)`, `devices.resolve_devices(target)` (schedule expansion, core §4); `catalog.job_input(item_id)` (build input, catalog §5.3, fields = build §3.1); `build.latest_artifact(item_id, abi)` (device §4.3); `build.item_status(item_ids)` and `build.needs_build(item_id)` (build §3.4; App list status, `only_changed` schedules); `build.forget_item(item_id)` (item delete, build §3.4); `catalog.set_apkmirror_versions(package, versions)` (called by the build handler, catalog §3); `projects.item_projects`, `projects.assigned_projects`, `projects.project_info`, `projects.check_assignment` (`docs/superpowers/specs/2026-10-23-follow-project-design.md` §4.2).

Settings keys: core `admin_username`, `admin_password_hash`, `job_history_count`, `base_url`; build `artifact_retention`; device `notify`.

Files under `/data` (host `local/data/`): `webui.db`, `logs/<job id>.log` (core), `work/<job id>/`, `artifacts/`, `apk-cache/` (build), `upstream/`, `icons/` (catalog), `home/.android/` (device, adb keys).

## 0.4 Route table

All routes need the admin session except the five marked "no auth" (`GET /api/health`, `GET /api/auth/state`, `POST /api/auth/setup`, `POST /api/auth/login`, `POST /api/auth/reset`). Mutating requests must be `Content-Type: application/json` (core §7).

| route | owner | purpose |
|---|---|---|
| `GET /api/health` | core | liveness and login-page server summary, no auth (core §8) |
| `GET /api/auth/state`, `POST /api/auth/setup`, `POST /api/auth/login`, `POST /api/auth/reset`, `POST /api/auth/logout`, `POST /api/auth/password` | core | setup state, first-run account creation, login with optional `remember`, forgot-password reset (these four without auth), logout, change password |
| `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`, `GET /api/jobs/{id}/log` (SSE) | core | history (rows carry `detail`) and live log of build, upstream and OTA jobs; cancel also removes a queued job |
| `GET/POST /api/schedules`, `PUT/DELETE /api/schedules/{id}`, `POST /api/schedules/preview`, `POST /api/schedules/{id}/run` | core | schedules |
| `GET/PUT /api/settings` | core | job history size, base URL, artifact retention count, GitHub token details, server info (arch, TZ, network mode, disk use) |
| `GET /api/catalog` | catalog | app list: groups with ordered items, names, icons, versions, upstream state |
| `GET/POST /api/sources`, `PUT/DELETE /api/sources/{id}` | catalog | patch sources |
| `GET/POST /api/items`, `GET/PUT/DELETE /api/items/{id}` | catalog | items: display name, group, enabled, variants, dependency projects (`project_ids`), engine options |
| `GET/POST /api/groups`, `PUT/DELETE /api/groups/{id}`, `PUT /api/groups/order`, `PUT /api/groups/{id}/items/order` (id `0` = Ungrouped) | catalog | groups (name, colour) and ordering |
| `GET/PUT /api/items/{id}/patches`, `PUT /api/items/{id}/version` | catalog | patch selection and options, version pin |
| `GET /api/apps/{pkg}/icon`, `POST /api/apps/{pkg}/icon/refresh`, `PUT /api/apps/{pkg}` | catalog | icon, display name, APKMirror/APKPure URLs |
| `GET /api/upstreams`, `POST /api/upstreams/check`, `POST /api/upstreams/{id}/pull` | catalog | upstream state, check now, pull (job) |
| `GET/POST /api/projects`, `PUT/DELETE /api/projects/{id}`, `POST /api/projects/assets`, `POST /api/projects/{id}/signer/accept` | projects | followed projects (`docs/superpowers/specs/2026-10-23-follow-project-design.md` §6) |
| `POST /api/items/{id}/build`, `POST /api/builds` | build | rebuild one item / many |
| `GET /api/items/{id}/artifacts`, `GET /api/artifacts/{id}/download` | build | artifacts, download |
| `GET /api/groups/{id}/download` (id `0` = Ungrouped) | build | ZIP of the newest successful artifacts of the group's items (build §3.4) |
| `GET /api/devices`, `GET /api/devices/discovered`, `POST /api/devices/pair`, `POST /api/devices/{id}/connect`, `PATCH/DELETE /api/devices/{id}`, `PUT /api/devices/{id}/assignments`, `POST /api/devices/{id}/ota` | device | phones, pairing, assignment, OTA |
| `POST /api/ota/{job_id}/force-reinstall`, `POST /api/ota/{job_id}/downgrade` | device | confirmed recovery installs |
| `GET/PUT /api/notify`, `POST /api/notify/test` | device | notification channel |
| `GET /`, `GET /<page>.html`, `GET /static/*` | core | frontend files (ui §1; `login.html` and its assets no auth) |

## 0.5 Integration decisions

Contradictions between the parts and how they were resolved (arch-core is the tie-breaker):

| topic | parts disagreed | decision | reason |
|---|---|---|---|
| Job table | build `build_jobs` and device `ota_jobs` each held status/trigger/times; core had `jobs` | one core `jobs` table for lifecycle; `build_jobs` and `ota_jobs` hold kind-specific detail keyed by `job_id` | one history list, one log/SSE path, one queue, one restart rule |
| Auto version with no compatible version | catalog: fail; build: fall back to newest with a warning | fail with `version_unresolved`; the user can choose `version_mode='latest'` explicitly | the silent fallback is a known hazard (`docs/logs/2026-10-19-preflight-build.md:73`); `latest` keeps the behaviour available on purpose |
| Dependency link table | `item_dependency(item_id, project_id)` | followed projects (`docs/superpowers/specs/2026-10-23-follow-project-design.md` §3.1) | `project` carries package, display name and signer pin; `projects.project_info` gives the planner the APK path |
| OTA concurrency | device: parallel per device; core: one OTA worker | one OTA worker | one phone is the common case; per-device workers are a later change inside `jobs.py` |
| Scheduled OTA | device `devices.ota_cron`; core `schedules` | core `schedules` kind `ota` | one scheduler, one editor, no cron parser |
| Notification channels | device: many channels; contract: "one of" | one channel in settings key `notify` | contract wording; fewer routes |
| mDNS on a stdlib-only stack | device wanted an mDNS library | stdlib DNS-SD legacy-unicast query (device D3) | no dependency; Ubuntu's adb has no working mDNS (verified, device D3) |
| adb key path | device `/root/.android` | `/data/home/.android` via `HOME=/data/home` | container runs as the host uid on Linux (core §1) |
| Log path, history retention | build `DATA/jobs/<id>/build.log`, per-item history setting | core `/data/logs/<id>.log`, core `job_history_count`; artifact rows deleted with their files, `artifacts` FK RESTRICT keeps jobs that still have files | one cleanup rule for all job kinds |
| Upstream pull concurrency | catalog: own lock per upstream | pull = core job of kind `upstream` (serial, dedup) | reuses queue, log and history |
| Job input field names | catalog and build listed different names | build §3.1 names are canonical; catalog §5.3 maps to them | one schema |
| Sidecar addressing | build left it to core | `FLARESOLVERR_URL` etc. = `http://127.0.0.1:<port>`, all sidecars bind loopback | shared namespace; nothing exposed on the LAN in host mode |
| Seed file location | catalog asked core | `local/webui/catalog-seed.json` | next to the code that imports it |
| Part numbering | ci, xposed and ui drafts each called themselves "Part 5" | UI = Part 5, Xposed = Part 7 (Part 6 dropped) | one reference scheme |
| Carrier of a pull's `rebuild` flag | catalog: button flag; core: target has only `upstream_id`; the dropped CI relied on a "manual = false" default | new column `jobs.options`; `rebuild` absent = false; schedules default true; UI buttons send true (Q4) | one place for per-job flags; dedup compares normalized options (§0.3), so a scheduled and a manual job of the same target are one job |
| CLI upstream | catalog: one global row; xposed: one per engine | `source.cli_upstream_id`; Morphe sources share one row | LSPatch/NPatch need their own engine jars |
| Upstream state field of an item | xposed proposed a `module` object | one `upstream` object for every item (catalog §6) | same data, one name |
| Same package twice on one phone | xposed proposal | `PUT /api/devices/{id}/assignments` answers 400 | the second install always hits SIGNATURE_MISMATCH |
| Patch options scope | mockup: "shared by every item from the source"; catalog: per item | per item (catalog §3) | catalog decision; mockup text changed (ui §2) |
| Cron schedules | mockup Cron tab; core: no cron | three spec forms only; cron editor deferred to next phase (§0.6 F1) | core §6, no parser |
| Cross-kind ordering | mockup: "rebuild waits for check, OTA waits for builds"; core: kinds run in parallel | parallel kinds; order by schedule times and chaining | core §5; mockup text changed (ui §2) |
| Password minimum | mockup 12, core 10 | 10 | core §8 |
| Icon fetch timing | catalog: when an app row is created; CLI start-up must make no Play requests | background task from `catalog.start()` | seed import and CLI commands stay offline from Play |
| Telegram download | build: delete `telegram_dl` (Xposed only) | deleted; secret `TDL_BACKUP` retired | no caller (xposed §1) |
| Network-namespace anchor | core draft: `app` is the anchor | idle `netns` service is the anchor; `app` and the sidecars join it (core §1) | after a crash restart of an anchor `app`, the sidecars keep the old namespace |
| Module start-up | core: one `start()` does registration and threads; CLI subcommands run "the normal start-up" | `register()` (no threads, no IO) and `start()` (threads, adb server, Play fetch); CLI calls only `register()` (core §3) | CI makes no Play requests and has no adb |
| OTA recovery modes | device: mode and install id on the request; core `enqueue` had no slot | `options.mode` + `options.package` (§0.3) | one job shape for core and device |
| Notifier IO | device: sends in the subscriber | subscriber only queues; one notifier thread sends (device §4.4) | subscribers run in the emitting worker thread |

## 0.6 User decisions (2026-10-19, `docs/logs/2026-10-19-webui-contract.md` "Decisions")

| # | question | decision | applied in |
|---|---|---|---|
| Q1 | GitHub Actions build path (old workflows call the deleted org scripts) | dropped: no GitHub patched-APK builds (original ruling: keep and port them; reversed 2026-10-25 when the system's build scope was fixed to the web app image and the Android companion app); org scripts deleted | build §7 |
| Q2 | Xposed / LSPatch apps (`src/build/Xposed.sh`) | keep as a second item type in v1 | xposed (Part 7) |
| Q3 | BiliRoamingM and Dropped-Patches sources | seed as normal items; the first build shows whether they work | catalog §8 |
| Q4 | New upstream version | rebuild affected items automatically; phones with "install after build" get them | catalog §5.2, §5.4; core §6 |
| Q5 | ABI variants | seed only universal + arm64-v8a; other ABIs can be added per item | catalog §8; catalog §6 (`POST /api/items` default) |
| Q6 | Transport | plain HTTP on the LAN | core §7 |
| — | UI | mockup series `docs/superpowers/mockups/final/` ("Workbench" + merged features) | ui (Part 5) |
| — | Auto version with no compatible version | fail `version_unresolved`; `version_mode='latest'` is the explicit alternative | §0.5 |
| — | Spec review should-fix items S1–S10, S12 | fix in the spec now | §0.3, §0.5, core §1, §3, §5, §12, build §4, device §3.2, §4.4 |
| R1 | Mockup "Download all (zip)" on a group | restore | build §3.4; ui §2 App list |
| R2 | Mockup "Keep me signed in" on login | restore | core §7, §8; ui §2 Login |
| R3 | Mockup colour picker for colour-valued patch options | restore | ui §2 App detail (client only) |
| R4 | Mockup "newer versions on APKMirror" in the pin list | restore, cached, no extra scrape per page load | catalog §3, §6; build §3.2, §4 step 5d; ui §2 App detail |
| R5 | Mockup server summary on the login page | restore with non-sensitive data only | core §8 `GET /api/health`; ui §2 Login |
| R6 | Colour per group (user, 2026-10-19) | include in v1: one `grp.color` column, set by the group create/update route, shown on the App list group header | catalog §3, §3.1, §6; ui §2 App list |
| F1 | Mockup cron editor | deferred to next phase | core §6 (three spec forms only); ui §2 Schedules |
| F2 | Mockup locked "required" patches | deferred to next phase | ui §2 App detail |
| F3 | Mockup device latency | deferred to next phase | ui §2 Devices |

No question is open.

## 0.7 Uncertainties (to be settled by tests during implementation)

- mDNS legacy-unicast replies from Android's responder: not tested against a real phone (device T4). If it fails, Linux falls back to the manual form, which always works.
- Real phone flows (pairing, reconnect after reboot, signature mismatch text): device T4/T5, user-run.
- Morphe CLI `options-create` / `list-patches` output layout and APKEditor `info` flags: captured during implementation (catalog §12, build §10).
- Build duration (timeout 60 min is an estimate, build §10).
- `list-patches --with-options` preset-value layout for option `values` (catalog §4.1): parsed from captured output like the other fields.
- GitHub token `expires_at`/`scopes` depend on response headers that fine-grained tokens may not send; null then (core §8).
- LSPatch v1.2 flags against current app versions and the NexAlloy v1.1 pin: first build shows the result (xposed §12).
- APKMirror list-page titles: the version is parsed from the row title text; the exact title layout per app is checked against captured pages during implementation (build §4 step 5d).


# Part 1 — Core: topology, web stack, auth, persistence, API, scheduler


Other parts design against §1–§7. arch-core is the tie-breaker for shared interfaces.

### 1. Containers (one compose file for macOS and Linux)

`local/docker-compose.yml` has five services. The `builder` service is renamed `app` and also runs the web server; a new idle `netns` service holds the network namespace.

| service | image | network | role |
|---|---|---|---|
| `netns` | `busybox:1.38.0-musl`, `command: ["tail","-f","/dev/null"]` | `network_mode: ${RELUME_NETWORK_MODE:?set RELUME_NETWORK_MODE in local/.env}`; `ports: ["${RELUME_WEB_PORT:-47000}:47000"]` | network-namespace anchor only; runs no code that can crash |
| `app` | `local/Dockerfile` target `app` (`debian:trixie-slim`, JRE 21) + `adb` (device D2) + pup and APKEditor baked in (build §2.1) + `tzdata` + the code baked in (`jhangyu/relume-app`); `CMD ["python3","-m","webui"]` | `network_mode: service:netns` | web UI, API, scheduler, job workers; runs builds and `adb` as child processes |
| `flaresolverr` | as today | `network_mode: service:netns`; env `HOST=127.0.0.1`, `PORT=47191` | page fetch |
| `cfbypass` | as today | `network_mode: service:netns`; `command: [..., "--host","127.0.0.1","--port","47800"]` | page fetch fallback |
| `apkmsolver` | as today | `network_mode: service:netns`; solver binds `127.0.0.1:47900` | Turnstile solver |

All services: `restart: unless-stopped`. `app` has `healthcheck: curl -sf http://127.0.0.1:47000/api/health`.

Why this shape:
- Builds run inside `app` as child processes. No Docker socket, no host-path translation. This removes preflight-build §1 blockers 1 and 3 (`docs/logs/2026-10-19-preflight-build.md:22`).
- `netns` is the network-namespace anchor (today the anchor is `flaresolverr`, `local/docker-compose.yml` at 9fd15dc, `network_mode: service:flaresolverr`). It owns `ports:` and `network_mode`, because a container that joins another container's namespace cannot publish ports. The sidecars and `app` keep reaching each other on `localhost` exactly as today (`local/docker-compose.yml` at 9fd15dc, `builder` env `FLARESOLVERR_PORT`, `CFBYPASS_PORT`, `APKM_SOLVER_PORT`).
- Why not `app` as the anchor: when the restart policy restarts a crashed anchor, containers that joined its namespace keep the old one (Docker 29 / Compose v5), so every build would fail `download_failed` until a manual `up -d`. Verified 2026-10-19 (Docker 29.8.2, Compose v5, scratch project, bridge mode): with an idle busybox anchor, a crash of `app` (`kill 1` inside it; RestartCount 0 → 1) leaves `app` → sidecar and sidecar → `app` loopback connections working, with no restart of the sidecar. Compose docs: `network_mode: service:{name}` "gives the container access to the specified container", and Compose orders start/stop by `network_mode: "service:..."` dependencies (docs.docker.com/compose/how-tos/networking, startup-order, fetched 2026-10-19).
- The only platform difference is one value in `local/.env`:
  - Linux: `RELUME_NETWORK_MODE=host` → the anchor's namespace is the host's; mDNS reaches the LAN; the web port is the host's port 47000.
  - macOS: `RELUME_NETWORK_MODE=bridge` → port 47000 is published; outbound TCP from the container to the phone works; mDNS finds nothing; the manual pairing form is used.
  - Same code on both. Discovery runs on both and simply returns no devices on macOS.
- Proof (run 2026-10-19, Docker 29.8.2, Compose v5.5.0, scratch project): a service with `network_mode: host` plus `ports:` starts and Compose prints `Published ports are discarded when using host network mode`; with `bridge` the port is published (`0.0.0.0:47999->8080/tcp`); a second service with `network_mode: service:<first service>` starts in both modes. `${VAR:?msg}` makes Compose stop with an error if the variable is not set, so a wrong platform default cannot happen silently.
- Sidecars bind `127.0.0.1` because in Linux host mode they would otherwise be open on the LAN (FlareSolverr is an open browser proxy). Verified bind knobs: FlareSolverr reads `HOST` (`/app/flaresolverr.py` in `ghcr.io/flaresolverr/flaresolverr:latest`); cfbypass `server.py` has `--host` (default `0.0.0.0`) and its `/app/docker-entrypoint.sh` forwards args (`exec python3 server.py "$@"`); `local/solver/solver.py` (the `ThreadingHTTPServer` bind) binds `0.0.0.0` today and changes to `127.0.0.1` (owner: build).
- Host ports used in Linux host mode: 47000, 47191, 47800, 47900, and adb's 5037 (loopback). They must be free on the host.

Mounts on `app`:
- the three signing keystores of `src/keystore/` as read-only file mounts at `/repo/src/keystore/<name>`; the code (`local/webui/`, `src/build/*`) is baked into the image, never the keystores or the token file.
- `${RELUME_DATA_DIR:-./data}:/data` (default `local/data/`, gitignored) — every write: DB, job logs, work dirs, artifacts, caches, adb keys. A host directory (not a named volume) so the user can back it up and reach artifacts on a NAS share. Long-syntax bind with `bind: {create_host_path: false}` (as for the token file): Docker would otherwise create a missing host dir owned by root, and a non-root `app` (`RELUME_UID`) could not write it. The user creates the directory (§12 step 1); the server creates `/data/home`, `/data/logs` and the other subdirectories at start.
- GitHub token file bind, unchanged (`local/docker-compose.yml` at 9fd15dc, `builder` volume `github_token`).

Process user: `user: "${RELUME_UID:-0}:${RELUME_GID:-0}"`, env `HOME=/data/home`. Linux sets its own uid/gid in `local/.env` so `local/data/` is owned by the host user (preflight-build §7 ownership blocker, `docs/logs/2026-10-19-preflight-build.md:85`). macOS leaves the default; Docker Desktop maps ownership.

Other env on `app`: `TZ=${TZ:-UTC}` (schedules use local wall time), `RELUME_NETWORK_MODE` (shown on the Devices page, ui §2), `PYTHONDONTWRITEBYTECODE=1` (repo is read-only), `PYTHONPATH=/repo/local`, the three sidecar ports as today.

The `app` image builds on arm64 and amd64 (build §2.1).

`local/.env.example` (tracked) lists `RELUME_NETWORK_MODE`, `RELUME_WEB_PORT`, `RELUME_DATA_DIR`, `RELUME_UID`, `RELUME_GID`, `TZ`, `RELUME_GITHUB_TOKEN_FILE`. `local/.env` and `local/data/` are added to `.gitignore`.

### 2. Web stack

- Backend: Python 3.12 standard library only. Verified in `relume-builder`: Python `3.12.3`, `hashlib.scrypt` present. Modules: `http.server.ThreadingHTTPServer`, `sqlite3`, `hashlib`, `secrets`, `hmac`, `threading`, `subprocess`, `urllib.request`, `json`, `zoneinfo`. No pip packages for the server.
- Frontend: static HTML/CSS/vanilla JS (ES modules) in `local/webui/static/`, ported from the user-chosen mockup `docs/superpowers/mockups/final/` (files and mapping: ui §1, §2). No npm, no bundler. Drag-reorder uses the native HTML Drag and Drop API.
- Live log: Server-Sent Events (`EventSource`).
- Code changes need `docker compose -f local/docker-compose.yml build app` and `up -d` (code is baked into the image).

### 3. Code layout

Package `local/webui/`:

| owner | files |
|---|---|
| core | `__main__.py` (start + CLI `reset-password`; it handles `export-seed`), `server.py` (HTTP server, router, static files, auth guard, SSE, file responses), `db.py`, `auth.py`, `jobs.py`, `scheduler.py`, `settings.py`, `events.py`, `schema/001_core.sql`, `tests/test_core_*.py` |
| build | `build.py`, `schema/002_build.sql`, `tests/test_build_*.py`; `src/build/build-item.sh`, `src/build/inspect.sh`, `src/build/utils.sh`, `src/build/test-build-item.sh` (incl. the Xposed engine branch, xposed §5.3); `local/Dockerfile` (toolchain, adb, pup, APKEditor, multi-arch); `local/docker-compose.yml`, `local/.env.example`, `.gitignore` (§1 is the input spec); `local/solver/solver.py` bind change |
| catalog | `catalog.py`, `upstream.py`, `schema/003_catalog.sql`, `tests/test_catalog_*.py`; the one-off seed script and `catalog-seed.json` (incl. Xposed validation and seed rows, xposed §4, §5.1) |
| device | `devices.py`, `ota.py`, `notify.py`, `schema/004_device.sql`, `tests/test_device_*.py` |
| frontend | `static/app.css`, `static/app.js` (one owner); `static/<page>.html` + `static/<page>.js` (one owner per page, ui §1) |
| cleanup (round 2, after `catalog-seed.json` is committed) | every deletion: org scripts, `src/build/Xposed.sh`, `src/options/`, `src/patches/`, `src/build/helper/apps.json`, `src/etc/connection.sh`, `src/etc/ci.sh`, `local/build.sh`, old workflows and actions (build §7, catalog §8); README download links |

Xposed (Part 7) owns no file: its catalog rules land in catalog's files, its engine branch in build's files.

Feasible 2-round split: round 1 = core; catalog + upstream + seed script; build runner (bash, `utils.sh`, Dockerfile, compose); device + notify; frontend shell (`app.css`/`app.js`, login, App list). Round 2 = build job service (`build.py`); the other five pages; Xposed engine branch and validation; cleanup. Core lands the `db`/`jobs`/`events`/router interfaces first inside round 1 (an interface commit) so the other round-1 members code against them.

Module contract:
- Each feature module exports `ROUTES = [(method, pattern, handler)]`; `server.py` imports the four owner modules by name. `pattern` uses `{name}` segments (`/api/jobs/{id}`); integer-looking values arrive as `int`.
- `handler(req)` gets `req.method`, `req.params`, `req.query`, `req.json` (parsed body, max 1 MiB), `req.headers`. It returns one of: `(status, obj)` → JSON; `FileResponse(path, download_name)` → streamed file with `Content-Length` (APKs are 100–230 MB, `docs/logs/2026-10-19-preflight-runtime.md:20`); `StreamResponse(write_fn, download_name)` → `write_fn(fileobj)` writes the body straight to the socket, no `Content-Length`, connection closed at the end (group ZIP, build §3.4); `SSEResponse(iterator)`.
- Errors: raise `HttpError(status, message)` → `{"error": message}`. Any other exception → 500 `{"error":"internal error"}`, traceback to stdout (visible in `docker compose logs app`).
- Each feature module may export `register()` and `start()`. `register()` registers job handlers and event subscribers; it starts no thread and does no IO (no network, no child process). `start()` starts background work: job workers and scheduler (core), the icon fetcher (catalog §3.3), `adb start-server` (device D1), the notifier thread (device §4.4).
- Start-up order. Server (`python3 -m webui`): DB schema → seed import (catalog §8) → every module's `register()` → restart recovery (§5) → every module's `start()` → HTTP server. CLI subcommands (`reset-password`, `export-seed`): DB schema → seed import → every module's `register()` only. So they make no Play requests, need no adb, and start no worker, scheduler or mDNS browser.

Input validation (one rule set in `server.py`/owner handlers, checked before any value reaches a path, argv or bash; failure → 400):
- Slugs (`source.id`, `item.key`, variant `name`, `keystore` file name, `exclude_splits` entries): `^[A-Za-z0-9_][A-Za-z0-9._-]*$` (no leading `.` or `-`, so no `..` and no option-like value). `keystore` must also exist in `src/keystore/`.
- `package`: `^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$`. `repo`: `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`. `asset_regex`: must compile with Python `re`.
- Variant fields: `abi` and `strip_libs` ∈ the ABI list (build §3.1); `download.kind`/`type` ∈ their enums; `arch`, `dpi` slugs; `minver` `^[A-Za-z0-9 .+-]*$`; `download.url` `https://` URL. `version_pin` slug.
- Devices: `host` = IPv4 address (`ipaddress.IPv4Address`) or an RFC 1123 hostname (labels `[A-Za-z0-9-]`, not starting with `-`); ports int 1–65535; pairing `code` exactly 6 digits.
- Remote values (GitHub `tag`, APKMirror-scraped app version) are mapped with `[^A-Za-z0-9._-]` → `_` and a leading `.` or `-` → `_` before they are used in a path or file name.
- Every subprocess call uses an argv list (no shell string). The runner (`build-item.sh`, `utils.sh` `patch_variant`) uses no `eval` and quotes every expansion; today's `eval` in `patch` (`utils.sh`) is removed with it.

### 4. Persistence

SQLite at `/data/webui.db`. `db.py` rules: one connection per thread (`threading.local`), `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`; `with db.tx() as c:` for writes. Times are stored as UTC ISO-8601 text (`2026-10-19T04:00:00Z`).

Schema files `local/webui/schema/NNN_<owner>.sql` run in number order at start. `PRAGMA user_version` = highest applied number. The project is unreleased: owners edit their own file freely until release; a schema change during development means deleting `local/data/webui.db` (the plan's test fixtures always start from an empty DB).

Core tables (`001_core.sql`):
```sql
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- value = JSON
CREATE TABLE sessions (token TEXT PRIMARY KEY, expires_at TEXT NOT NULL);
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('build','upstream','ota')),
  status TEXT NOT NULL CHECK (status IN ('queued','running','success','failed','cancelled')),
  trigger TEXT NOT NULL CHECK (trigger IN ('manual','schedule','upstream','after_build')),
  schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL,
  target TEXT NOT NULL,          -- JSON, atomic target (see below)
  options TEXT NOT NULL DEFAULT '{}',  -- JSON, per-job flags (§0.3), normalized by jobs.enqueue (§5)
  reason TEXT,                   -- one-line failure/cancel reason
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
CREATE INDEX jobs_kind_id ON jobs(kind, id);
CREATE TABLE schedules (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('upstream','build','ota')),
  target TEXT NOT NULL,          -- JSON, may be a group/all target
  spec TEXT NOT NULL,            -- JSON, see §6
  options TEXT NOT NULL DEFAULT '{}',  -- JSON, kind-specific, e.g. upstream {"rebuild":true}
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT, next_run_at TEXT);
```
Job log file: `/data/logs/<job id>.log`.

Settings keys and owners: core `admin_username`, `admin_password_hash`, `job_history_count` (default 500 per kind), `base_url` (absolute URL of the UI, used for links in notifications; empty = no links); build `artifact_retention`; device `notify`. Owners document their keys in their parts. Secrets in settings are never returned by the API; the API returns `"set": true|false`.

Owner tables reference `jobs(id)` and catalog `item(id)` by foreign key. Detail rows of a job use `ON DELETE CASCADE`; rows that must outlive history cleanup (artifacts) use `ON DELETE RESTRICT`.

Target JSON convention (shared by jobs, schedules, API):
- atomic job targets: build `{"item_id":N}`; upstream `{"upstream_id":N}` (one upstream row can serve several sources, catalog §3); ota `{"device_id":N}` or `{"device_id":N,"item_ids":[…]}`; the OTA mode and the recovery package travel in `jobs.options` (`mode`, `package`, §0.3), not in the target.
- schedule / bulk targets: `{"all":true}` or one list: `{"source_ids":[…]}` (upstream), `{"group_ids":[…]}` (id `0` = Ungrouped) or `{"item_ids":[…]}` (build), `{"device_ids":[…]}` (ota). Expansion: catalog exports `resolve_items(target) -> [item_id]` and `resolve_upstreams(target) -> [upstream_id]` (the sources' upstreams plus their CLI upstreams, distinct); device exports `resolve_devices(target) -> [device_id]`. Each atomic target becomes one job, so every item has its own log and history row.

Backup: copy `local/data/` while `app` is stopped, or `sqlite3 .backup` (not shipped). Catalog configuration lives only here after the old `src/options`/`src/patches` files are removed (see catalog part).

### 5. Jobs and events (in-process, `jobs.py`, `events.py`)

API for owners:
- `jobs.register(kind, handler, detail=None)`. `detail(job_id) -> dict` is optional; `GET /api/jobs/{id}` adds its result as `detail` (build: versions, fail code, artifacts; ota: per-package installs).
- `jobs.enqueue(kind, target, trigger, schedule_id=None, options=None) -> job_id`. `options` is first normalized: keys not listed for the kind in §0.3 are dropped, missing keys get their defaults. If a job with the same kind, target and normalized options is already `queued`, its id is returned and nothing is added (so a scheduled and a manual build of one item are one job; a `force_reinstall` OTA is never merged into a `normal` one). A `running` job with the same target does not block a new queued one. `POST /api/jobs/{id}/cancel` on a `queued` job sets `cancelled` at once; it never runs.
- `handler(job, log)`: `job.id`, `job.target`, `job.cancel_event` (`threading.Event`). `log.line(text)` appends `HH:MM:SS text\n` with ANSI escapes stripped (the scripts print colour codes, `docs/logs/2026-10-19-preflight-build.md:66`). `log.run(argv, cwd, env)` starts a child in its own process group, streams stdout+stderr into the log, kills the group on cancel, returns the exit code; optional `timeout` seconds kills the group and raises `JobFailed('timeout')`; optional `on_line(text)` is called for each stripped line (build uses it for `::step::`/`::version::` markers, build §3.2). Return normally = `success`; raise `JobFailed(reason)` = `failed`; cancel = `cancelled`.
- `events.emit(type, payload)`, `events.subscribe(type, fn)`. Subscribers run synchronously in the emitting thread (a job worker or a request thread) and must not raise (exceptions are logged and swallowed). A subscriber does no network IO itself: it enqueues a job or puts the event on its own queue (notifier, device §4.4). Event names and payloads are fixed in integration §0.3 (top of the merged spec): `upstream.new_version`, `build.succeeded`, `build.failed`, `ota.done`, `ota.failed`, `device.offline`. Chaining is done by owners: a pull with `rebuild` enqueues builds with `trigger='upstream'`; device subscribes to `build.succeeded` and enqueues OTA with `trigger='after_build'`.

Workers: one thread per kind, each runs its queue oldest-first, one job at a time. Builds are serial because all builds share one exit IP, one solver, and APKMirror rate-limits bursts (`docs/logs/2026-10-19-preflight-build.md:97`). OTA and upstream checks run in parallel with builds.

Restart recovery: at start, every `running` job becomes `failed` with reason `server restarted`; `queued` jobs stay and run.

History cleanup (after each job finishes): per kind, delete jobs beyond the newest `job_history_count`, one by one; a delete that fails on a RESTRICT foreign key is skipped. Their log files are deleted with them.

### 6. Scheduler (`scheduler.py`)

Spec JSON (only these three forms; the UI editor maps to them):
- `{"every":"day","at":"04:00"}`
- `{"every":"week","days":[0,3],"at":"04:00"}` (0 = Monday)
- `{"every":"hours","hours":6}` (1–168; counted from `last_run_at`, or from enable time)

No cron strings: three forms cover "every day at 04:00" style schedules without a parser dependency.

Behaviour:
- One thread wakes every 30 s and also when a schedule is saved. For each enabled schedule with `next_run_at <= now`: expand its target (§4), `jobs.enqueue` one job per atomic target with `trigger='schedule'`, set `last_run_at`, compute `next_run_at`.
- Times are local wall time in `TZ` via `zoneinfo`; stored as UTC. A wall time that does not exist on a DST day runs at the next existing minute.
- Missed runs (container was down): at start, a schedule whose `next_run_at` is in the past runs once, then continues normally. No backfill of multiple missed runs.
- A target that expands to nothing logs a warning in the server log; no job is created.
- Kinds: `upstream` schedule = pull (check + download + inspect) of the expanded upstreams; `build` schedule = rebuild the expanded items; `ota` schedule = OTA to the expanded devices.
- `schedules.options` is passed to `jobs.enqueue` for each job (normalized there, §5). Keys: upstream `pull` (default true; false = check only, catalog §5.1) and `rebuild` (schedule editor default true, decision Q4: the pull enqueues builds of affected enabled items); build `only_changed` (default false; true = enqueue only items where `build.needs_build(item_id)`, build §3.4; used here at expansion, not stored on the job); ota `retries` (0–5, default 0, device §4.2); every kind `notify` (default true; false = the notifier skips this job's events, device §4.4).

### 7. Auth (`auth.py`)

- One admin account: username + password, stored as settings `admin_username` and `admin_password_hash`. The account exists only when both are set; a hash without a username counts as no account.
- First start: the server generates and prints no password. While no account exists, `GET /api/auth/state` → `{"setup_required":true}` and the first visitor creates the account with `POST /api/auth/setup {username, password, remember?}`: username 1–64 chars of `[A-Za-z0-9._-]`, password ≥ 10 chars (else 400). Check and create run in one `BEGIN IMMEDIATE` transaction, so of two concurrent setups exactly one gets 200 (and a session cookie, like login); any later setup → 409 `{"error":"already set up"}`. Login and reset while no account exists → 409.
- Forgot password: `POST /api/auth/reset` (no auth) → 202 `{"ok":true}`. The server sets a new random 20-character password, deletes all sessions, and prints `admin password reset for <username>: <new password>` to stdout (`docker logs relume-app`, Portainer container logs). A second reset within `RESET_COOLDOWN` (5 minutes, in-memory) → 429 `{"error":"cooldown","retry_after":S}`.
- CLI: `docker compose -f local/docker-compose.yml exec app python3 -m webui reset-password` calls the same function (same output, deletes all sessions; exits 1 when no account exists).
- Hash: `hashlib.scrypt(n=2**14, r=8, p=1)`, 16-byte random salt, compare with `hmac.compare_digest`.
- Session: `secrets.token_urlsafe(32)` in cookie `relume_session` (`HttpOnly`, `SameSite=Strict`, `Path=/`). Login body `remember` ("Keep me signed in", default true in the form): true → cookie `Max-Age=2592000` and server-side `expires_at` = now + 30 days; false → cookie without `Max-Age`/`Expires` (ends when the browser closes) and server-side `expires_at` = now + 12 h. Password change deletes all sessions except the current one.
- Every `/api/*` route except `GET /api/auth/state`, `POST /api/auth/setup`, `POST /api/auth/login`, `POST /api/auth/reset` and `GET /api/health` needs a valid session → else 401. Pages under `/` redirect to `/login.html` without a session.
- CSRF: `SameSite=Strict`, mutating methods require `Content-Type: application/json`, and if an `Origin` header is present its host must equal the `Host` header.
- Brute force: after 5 failed logins within 5 minutes, login answers 429 for 5 minutes (in-memory counter). Wrong username or wrong password → the same 401 `{"error":"wrong credentials","attempts_left":N}` (no user enumeration); locked → 429 `{"error":"locked","retry_after":S}` (login page text, ui §2).
- Transport is plain HTTP on the LAN (decision Q6, §0.6).

### 8. API surface

All JSON. Owners define the routes under their prefixes in their parts.

| owner | prefix | routes |
|---|---|---|
| core | `/api/health` | `GET` → `{"ok":true, "app":"Relume", "version":"1.0", "started_at":"<ISO time>"}` (no auth); `version` is the constant `VERSION` in `server.py`, `started_at` the process start time. The login page shows it as the server summary (ui §2 Login). It carries only these four fields: never item, app or device names, counts, IPs, host or network details, arch, tokens or paths. |
| core | `/api/auth` | `GET /state` → `{setup_required}`, `POST /setup {username, password, remember?}`, `POST /login {username, password, remember?}`, `POST /reset` (core §7), `POST /logout`, `POST /password {old,new}` (new ≥ 10 chars) → `{"signed_out":N}` (other sessions deleted) |
| core | `/api/jobs` | `GET ?kind=&status=&item_id=&device_id=&before_id=&limit=` (newest first, max 100); `GET /{id}`; `POST /{id}/cancel`; `GET /{id}/log` (SSE) |
| core | `/api/schedules` | `GET` (each row adds `next_runs` = next 5 run times, and `last_counts` = `{status: n}` of the jobs of that schedule created at or after `last_run_at`), `POST`, `PUT /{id}`, `DELETE /{id}`, `POST /preview {spec}` → next 5 run times; `POST /{id}/run` → run now |
| core | `/api/settings` | `GET` → non-secret core settings; `github {state (absent/unauthenticated/authenticated/rejected), limit, remaining, reset_at, token_hint ("****" + last 4), path, expires_at, scopes}` from `GET https://api.github.com/rate_limit`, cached 5 min, `?recheck=1` bypasses the cache; `expires_at`/`scopes` from the `github-authentication-token-expiration`/`X-OAuth-Scopes` response headers when present, else null; `server {arch (platform.machine()), tz, now, network_mode, disk {total, free (shutil.disk_usage('/data')), artifacts, apk_cache, upstream, logs (directory size sums), artifacts_count}}`. `PUT {job_history_count, base_url, artifact_retention}` (`artifact_retention` is build's key, edited on the Settings page; a change runs build retention for every item at once, build §6) |
| catalog | `/api/catalog`, `/api/upstream` | see catalog part |
| build | `/api/builds`, `/api/artifacts` | see build part |
| device | `/api/devices`, `/api/ota`, `/api/notify` | see device part |

`GET /api/jobs` joins nothing from owner tables; `item_id` / `device_id` filters match the job `target`. Each job JSON: `{id, kind, status, trigger, schedule_id, target, options, reason, created_at, started_at, finished_at, detail}`; `detail` comes from the kind's `detail` function, in the list too (one call per row, page ≤ 100 rows; the history tables of ui §2 need versions, sizes and installs).

Log SSE: `GET /api/jobs/{id}/log`. Each event carries a chunk of log lines; `id:` = byte offset after the chunk. On reconnect the browser sends `Last-Event-ID`, the server resumes from that offset. Finished job: send the whole file, then `event: end` with the final status, then close. Running job: after the file end, wait on a per-job `threading.Condition` notified by `log.line`, send new bytes; send `event: end` when the job finishes. A comment line every 15 s keeps idle connections open.

### 9. Data flow (core view)

1. Browser → `server.py` → auth guard → owner handler.
2. Button "Rebuild"/"Update upstream"/"OTA now" → owner route → `jobs.enqueue` → worker thread → owner handler → child processes → `log`.
3. Scheduler thread → expand target → `jobs.enqueue`.
4. Handler finishes → job status → owner emits event → `notify.py` sends a message; chaining subscribers enqueue follow-up jobs.
5. Browser log view → SSE from the log file.

### 10. Error handling

| failure | behaviour |
|---|---|
| `RELUME_NETWORK_MODE` unset | Compose refuses to start with the message from `${RELUME_NETWORK_MODE:?...}` |
| port 47000 or a sidecar port busy (Linux host mode) | the process fails to bind and exits; `restart: unless-stopped` retries; healthcheck stays unhealthy; `docker compose logs app` shows the bind error |
| DB locked | `busy_timeout` 5 s; then 503 `{"error":"database busy"}` |
| handler exception | 500, traceback in server log, server keeps running |
| job handler exception other than `JobFailed` | job `failed`, reason = exception class + message (first line), traceback into the job log |
| server restart during a job | job `failed` with reason `server restarted` (§5) |
| sidecar down | build handler's concern (build part); core healthcheck covers `app` only |
| forgotten password | login-page "Forgot password?" → `POST /api/auth/reset`, or `reset-password` CLI (§7) |

### 11. Testing (core)

- `python3 -m unittest discover -s /repo/local/webui/tests` inside `app` (stdlib `unittest`, temporary DB under `/tmp`, no network).
- Core tests: router pattern matching and int params; auth hash verify, session expiry, 401 without cookie, 415 on non-JSON mutating request, Origin mismatch → 403, 429 after 5 failures; `remember` true → `Max-Age` cookie and 30-day expiry, false → no `Max-Age` and 12 h expiry; `GET /api/health` without a session returns exactly the four documented keys; `register()` of every module starts no thread (thread count unchanged); migration order and `user_version`; job enqueue dedup (scheduled and manual build of one item → one job; `force_reinstall` OTA not merged into `normal`; unknown option keys dropped), serial order per kind, parallel across kinds, cancel kills the child process group (child `sleep` process is gone), restart recovery; history cleanup skips RESTRICT rows; scheduler next-run for day/week/hours including a DST change in `Europe/Berlin`; missed-run single catch-up; SSE resume from `Last-Event-ID` and `end` event.
- Compose checks: `RELUME_NETWORK_MODE=host docker compose -f local/docker-compose.yml config` and the same with `bridge` both exit 0; with the variable unset it exits non-zero.
- Smoke on each platform (user-run): `up -d --build`, open the UI, create the admin account (`POST /api/auth/setup`), `curl` login, `GET /api/jobs` → 200; `docker compose exec app curl -sf localhost:47191` → FlareSolverr answers; from another LAN machine on Linux host mode `curl <host>:47191` fails (sidecar not exposed).

### 12. Deployment

Linux arm64 host / NAS:
1. `cp local/.env.example local/.env`; set `RELUME_NETWORK_MODE=host`, `RELUME_UID=$(id -u)`, `RELUME_GID=$(id -g)`, `TZ`. As the same user, `mkdir -p local/data` (or the `RELUME_DATA_DIR` path); Compose does not create it (§1 mounts), so it is owned by that user.
2. Put the GitHub token in the token file (as today, `local/build.sh` token-file block behaviour moves to the server start check; build part owns it).
3. `docker compose -f local/docker-compose.yml up -d --build`.
4. Open `http://<host>:47000` and create the admin account (username + password) on the first-start page.
5. Host firewall must allow inbound TCP 47000, and outbound UDP multicast to 224.0.0.251:5353 with its unicast replies (device discovery, device part D3).

macOS (Docker Desktop, Apple silicon):
1. Same (including `mkdir -p local/data`), with `RELUME_NETWORK_MODE=bridge`; leave `RELUME_UID`/`RELUME_GID` unset.
2. The repo must be under a Docker Desktop shared path (default includes `/Users`).
3. Open `http://localhost:47000` (or the Mac's LAN IP). Pair phones with the manual form.

Both: memory headroom for two browser containers (`shm_size: 2g` each, `local/docker-compose.yml` at 9fd15dc, `shm_size` of `cfbypass` and `apkmsolver`).

### 13. User decisions (core)

Answered in §0.6: Q1 GitHub Actions builds dropped; Q6 plain HTTP (§7).


# Part 2 — Catalog: sources, apps, items, patch selection, upstream releases


Owner: arch-catalog. Status: draft for integration into `2026-10-19-webui-design.md`.
Covers requirements 1 (scheduled upstream pulls), 2 (list unit, groups, ordering, icons, "Update upstream" button), 4c (web editing of patch selection, options, version pin), and the data part of 4d (dependency apps as tracked upstreams).

### 1. Why the model changes

Today the catalog is spread over five places (all citations verified in code):

| Today | Where | Problem for the UI |
|---|---|---|
| Source list (org, script, runs) | `local/build.sh` `ORGS` table and again `.github/workflows/manual-patch.yml` `workflow_dispatch` input `org` options | Unit is an org "run", not (source × app) |
| Upstream repo + channel | 3rd arg of `dl_gh` in each script, e.g. `src/build/morphe.sh`, `morphe-beta.sh` | Hard-coded; re-downloaded every run, tag never stored (`src/build/utils.sh` `dl_gh`) |
| Include/exclude | `src/patches/<key>/{include,exclude}-patches`, read by `get_patches_key` (`utils.sh`), rewritten by `sed -i` (`utils.sh` `get_patches_key`) | Second toggle mechanism; options after `|` are dropped (`utils.sh` `get_patches_key`) |
| Patch options | `src/options/<org>.json`, passed as `--options-file` (`utils.sh` `patch`) | Shared by all apps of an org and by stable/beta orgs |
| Version pin | Shell `version="…"` before `get_apk` (`hoo-dles.sh`, `rushiranpise.sh`, `Piko-Newx.sh`); `near_version=1` (`hoo-dles.sh`) | Pinning means editing a script |
| APKMirror/APKPure lookup | `src/build/helper/apps.json` (`utils.sh` `get_apk`, `get_apkpure`) | Fine, but a second registry keyed by package |
| Display name, icon | Nowhere (README headers only, e.g. the README MicroG RE note) | Must be new fields |

The new model replaces all of these with database rows plus a file cache. Old files are deleted after the one-time seed (§8).

### 2. Components and responsibilities

| Component | Runs in | Responsibility |
|---|---|---|
| Catalog store | web server (tables in the arch-core database) | Sources, apps, items, groups, ordering, overrides, pins |
| Release tracker | web server | Ask GitHub for the newest release of each upstream; download assets into the file cache; emit `upstream.new_version` |
| Bundle inspector | web server container (it carries the Java toolchain, build §2.1) | Run Morphe CLI `options-create` and `list-patches` on a cached patches file; return patch names, defaults, options, compatible versions |
| Icon fetcher | web server | Fetch app icon and default display name from Google Play once per package; cache file |
| Job input writer | web server, called by the build orchestrator (arch-build) | Turn one item into the build job input (§5.3) |

No new container and no new dependency: GitHub and Play calls are plain HTTPS from the web server; the inspector is a short child process (`java -jar <cli> …`) in the same container that runs builds (build §2.1).

### 3. Data model (schema file `local/webui/schema/003_catalog.sql`, owned here)

```
upstream    id PK, kind ('patches'|'cli'|'project'|'module'), repo ('owner/name'),
            channel ('stable'|'prerelease'|'tag'), tag (text, only for channel='tag'),
            asset_regex, latest_tag, latest_published_at, checked_at, check_error,
            downloaded_tag, notified_tag
            UNIQUE(repo, channel, tag)
source      id PK (slug, e.g. 'morphe', 'morphe-beta'), name, upstream_id → upstream,
            cli_upstream_id → upstream (kind='cli'),
            engine ('morphe'|'lspatch'|'npatch', default 'morphe', xposed §4),
            keystore (file name in src/keystore, default 'morphe.keystore', utils.sh)
app         package PK, display_name, icon_file, icon_fetched_at,
            apkmirror_list_url, apkmirror_example_url, apkmirror_example_url2, apkpure_url,
            apkmirror_versions (JSON array, newest first, null = never seen), apkmirror_checked_at
grp         id PK, name, color (text '#rrggbb', null = no colour), position
item        id PK, source_id → source, package → app, key, display_name (null = default),
            group_id → grp (null = "Ungrouped", shown last), position, enabled,
            version_mode ('auto'|'pinned'|'latest'), version_pin, allow_nearest,
            variants (JSON array, schema = build §3.1 `variants[]`),
            patch_overrides (JSON), engine_options (JSON, null unless engine is lspatch/npatch, xposed §4)
            UNIQUE(source_id, package)
bundle_info upstream_id, tag, package, patches (JSON, §5.2)   PK(upstream_id, tag, package)
project         id PK, upstream_id → upstream (kind='project') UNIQUE, package, display_name, auto_push, signer_sha256, signer_since, signer_alert (`docs/superpowers/specs/2026-10-23-follow-project-design.md` §3.1)
item_dependency item_id → item ON DELETE CASCADE, project_id → project ON DELETE CASCADE   PK(both)
```

Rules:
- **Item = (source × package).** This is the list unit of requirement 2. TikTok in `DeRevanced.sh` becomes two items (two packages). YouTube Music × 4 archs (`Anddea-Revanced-Extended.sh`) stays one item; the arch outputs are entries of `item.variants` (format owned by arch-build, build §3.1).
- **Stable and beta are separate sources** with separate upstream rows (same repo, channel differs), e.g. `morphe` (stable) and `morphe-beta` (prerelease). Each item owns its overrides; the sharing of `src/options/morphe.json` and `src/patches/youtube-morphe` between stable and beta (`morphe.sh`, `morphe-beta.sh`) ends. The seed copies the shared values into both items.
- **Several sources may share one upstream row** (dedup by `UNIQUE(repo, channel, tag)`), so one GitHub request serves them.
- **CLI upstream per source** (`source.cli_upstream_id`): all Morphe sources share one row (`kind='cli'`, `MorpheApp/morphe-desktop`, stable; every script downloads it the same way today, `morphe.sh` and 15 others); LSPatch and NPatch have their own rows (xposed §4.1).
- **Item type = `source.engine`**: `morphe` = patches item; `lspatch`/`npatch` = Xposed module item (xposed §4). `revanced-cli v4.6.0` downloaded by `BiliRoamingM.sh` and `Dropped-Patches-Revanced.sh` is never run by `patch()` (`utils.sh` `patch` always runs `morphe-desktop-*.jar`), so it is not modelled.
- **Followed projects** (MicroG RE, per the README MicroG RE note): replaced the dependency-app model; see `docs/superpowers/specs/2026-10-23-follow-project-design.md`. Seed: project 1 MicroG RE (`MorpheApp/MicroG-RE`, stable, asset_regex `^microg-[0-9.]+\.apk$`) linked through `item_dependency` to YouTube, YouTube Music, Google Photos and Solid Explorer hoo-dles items.
- `item.key` is the artifact name stem used today (first arg of `patch`, e.g. `youtube`, `youtube-music-arm64-v8a`; `morphe.sh`). arch-build uses it for artifact naming.
- `item.enabled = false` excludes the item from "build all" and schedules but keeps its settings.
- `app.apkmirror_versions`: non-beta versions listed on the first page of the app's APKMirror list page, written only by `catalog.set_apkmirror_versions(package, versions)` from the build handler (build §3.2, §4 step 5d), which also sets `apkmirror_checked_at`. The catalog never fetches APKMirror itself (§3.3). `job_input` sets `apkmirror.versions_due` = true when `apkmirror_checked_at` is null or older than 24 h (§5.3).

#### 3.1 Groups and ordering
- Groups are global (`grp.position`). Items have `group_id` and `position` inside the group.
- Group colour (§0.6 R6): `POST /api/groups {name, color?}` and `PUT /api/groups/{id} {name?, color?}` set `grp.color`; `color` must match `^#[0-9A-Fa-f]{6}$` or be null (null clears it), else 400. Ungrouped has no colour. Seed groups have `color` null.
- Reorder is "send the full new order": `PUT /api/groups/order {ids:[…]}` and `PUT /api/groups/{id}/items/order {item_ids:[…]}`. The second call also moves items into the group. The server rewrites `position` as 0..n-1 in one transaction. No fractional ranks needed for lists of this size (seed ≈ 55 items).
- Deleting a group moves its items to "Ungrouped". Ungrouped has no `grp` row; `PUT /api/groups/0/items/order` orders it and moves items into it (`group_id` null). Seed: one group per source, in `local/build.sh` `ORGS` table order, then group "Xposed" (xposed §4.1).

#### 3.2 Display names
- `app.display_name` default = Google Play `og:title` with the suffix ` - Apps on Google Play` removed (verified 2026-10-19: `curl -A Mozilla 'https://play.google.com/store/apps/details?id=com.google.android.youtube&hl=en'` returns `og:title` "YouTube - Apps on Google Play"). If the fetch fails, default = package name.
- Item label in UI = `item.display_name` if set, else `"<app.display_name> · <source.name>"`. Both are editable.

#### 3.3 Icons
- Source: Google Play `og:image` of `details?id=<package>&hl=en` (verified 2026-10-19 for `com.google.android.youtube` and HTTP 200 for `com.bilibili.app.in`). No APKMirror fetch: that path is Cloudflare-protected and rate-limited (`docs/logs/2026-10-12-apkmirror-403-investigation.md`) and must stay reserved for builds.
- Fetched once per app row without an icon by a background task started from `catalog.start()` (not inside the seed import, so seed import and CLI start-up make no Play requests), stored as `<data>/icons/<package>` (content type from the response), served by `GET /api/apps/{pkg}/icon` with a long cache header. "Refresh icon" button: `POST /api/apps/{pkg}/icon/refresh`.
- Failure (not on Play, network error): UI shows a letter tile from the display name. No upload feature in v1.
- Known limit: the shown icon is the original app icon. Patches such as "Custom branding icon" (`src/patches/youtube-music-rve-anddea/include-patches`) change the installed icon; the UI does not reflect that.

### 4. Patch selection and options (requirement 4c)

One mechanism replaces include/exclude files and per-org options files: **per-item overrides in Morphe CLI options-file format.** Not applicable to Xposed items: the patches routes answer 409 and `version_mode='auto'` answers 400 (xposed §5.1).

- Stored: `item.patch_overrides = {"<patch name>": {"enabled": true|false, "options": {"<key>": <value>}}}`; a patch absent from the map uses the bundle default. Only user changes are stored, so new upstream defaults apply automatically.
- Written for a build as `[ {"patches": <overrides>} ]`, one array entry per patches file of the source (piko-newx downloads several `.mpp` and patches with all of them, `Piko-Newx.sh`, `utils.sh` `patch_multi`).
- Evidence that partial files work with the current CLI: `src/options/anddea.json` contains one patch only, 13 option files are `[]`, and both are used by builds today (`utils.sh` `patch`). Morphe CLI docs (`MorpheApp/morphe-cli` `docs/documentation.md`, fetched 2026-10-19): the options file is "a list of patch bundles", `"enabled": false` disables a patch, `-e/-d/-O` override the file; `options-create -p … -f <package> -o <file>` writes defaults and merges into an existing file.
- `-e`/`-d` flags, `get_patches_key`, `src/patches/**` and `src/options/**` are deleted.

#### 4.1 What the edit page shows
`GET /api/items/{id}/patches` returns, for the source's **downloaded** tag:
```
{ tag, files: [ "<patches file name>" … ],
  patches: [ {name, description, default_enabled, enabled, options:[{key, title, type, default, values:[…], value}], compatible_versions:[…]} ],
  stale_overrides: [ "<patch name>" … ],          // in overrides, not in this bundle
  compatible_versions: [ … ] }                     // union for the package, newest first
```
`values` = preset values printed by `list-patches --with-options` (empty when none; the UI shows a select when non-empty). Data comes from `bundle_info` (§5.2) merged with `item.patch_overrides`. `PUT /api/items/{id}/patches` stores the overrides after validating: patch names exist in the bundle, option keys exist, value type matches. Stale overrides are kept and listed (the user may be between upstream versions); they are dropped from the build input (§5.3) and logged as a warning.

#### 4.2 Version pin
- `version_mode='auto'` (default): highest version listed as compatible for the package across the whole bundle (existing rule, `utils.sh` `detect_version`). If that list is empty the build fails with `version_unresolved` (build §5) instead of today's silent newest (`utils.sh` `get_apk`); the edit page shows the empty list in advance.
- `version_mode='latest'`: explicit newest non-beta on APKMirror (`utils.sh` `get_apk`), for items whose bundle lists no versions (universal patches).
- `version_mode='pinned'`, `version_pin = "<x>"` → passed to the build as the requested version (replaces `version="x"` in scripts). Pins outside `compatible_versions` are allowed with a warning, because existing pins are already outside it (e.g. `Piko-Newx.sh` `12.24.0-prod.02`) and `--continue-on-error` (`utils.sh` `patch`) lets them build.
- `item.allow_nearest = true` → replaces `near_version=1` (`hoo-dles.sh`, `utils.sh` `get_apk`).
- `prefer_version` and the CI experimental-version scrape (`morphe-beta.sh`, `.github/actions/experimental-check/action.yml` step `Fetch experimental support versions`) are not carried over; a pin covers the same need.
- `PUT /api/items/{id}/version {version_mode, version_pin, allow_nearest}`. Seed: items with `version=` lines become `pinned`; others `auto`, except items whose seed-time inspect shows an empty compatible list become `latest` (keeps today's behaviour explicit).

### 5. Upstream release tracking (requirements 1 and 2 button)

#### 5.1 Check
For each upstream row (deduplicated):
- `channel='stable'` → `GET /repos/{repo}/releases/latest` (GitHub docs: "the most recent non-prerelease, non-draft release", fetched 2026-10-19).
- `channel='prerelease'` → `GET /repos/{repo}/releases?per_page=10`, newest by `published_at` among non-drafts, prerelease or not. This keeps today's meaning: `dl_gh … prerelease` takes the newest release of any kind (`utils.sh` `dl_gh`). Sorting is done by us because the docs do not state list order.
- `channel='tag'` → `GET /repos/{repo}/releases/tags/{tag}` (only for pinned sources such as `Dropped-Patches-Revanced.sh` `v1.4.2`).
- One upstream is checked by the plain function `upstream.check(upstream_id)`, used by the route and the pull job.
- Module rows (`kind='module'`) must match exactly one asset, else `check_error="ambiguous module asset (<n> matches)"` (xposed §9).
- Asset filter: `asset_regex` on `assets[].name`, defaults: patches `\.mpp$`, cli `^morphe-desktop-.*-all\.jar$` (seen: `local/work/morphe-1/morphe-desktop-1.18.1-all.jar`), always skip `\.asc$`; for `stable` also skip names containing `dev` (`utils.sh` `dl_gh`).
- Auth: the existing token file mounted read-only (`local/docker-compose.yml` at 9fd15dc, `builder` volume `github_token`); empty file = unauthenticated (60 req/h, `local/build.sh` token-file block). One check of all upstreams ≈ 18 requests (one per distinct repo+channel), within the unauthenticated budget once per hour.
- Result: `latest_tag`, `latest_published_at`, `checked_at`, `check_error` (null on success). If `latest_tag != downloaded_tag` the source's items show "Update available".
- Event: when `latest_tag != notified_tag`, emit `upstream.new_version {upstream_id, tag, item_ids, job_id}` once and set `notified_tag` (prevents a notification on every scheduled check). Notification delivery is arch-device's section.

#### 5.2 Pull ("Update upstream" button and scheduled pull)
1. Check (5.1). If `latest_tag == downloaded_tag`: done ("up to date") when the row is not a `patches` row or a `bundle_info` row exists for `(upstream_id, downloaded_tag, package)` for every package used by items of the sources on this upstream (the same package set as step 3); otherwise skip to step 3 (re-inspect).
2. Download every matching asset to `<data>/upstream/<upstream_id>/<tag>.part/`; verify size equals `assets[].size` and, when GitHub gives `assets[].digest` (`sha256:…`), the hash. Rename the directory to `<tag>/` (atomic on the same filesystem). Then set `downloaded_tag`.
3. Inspect (patches upstreams only). First, if the CLI upstream (`source.cli_upstream_id`) of the sources on this upstream has no `downloaded_tag`, pull it in the same job (steps 1–2 for that row). Then run in the web server container, per package used by items of the sources on this upstream:
   `java -jar <cli> options-create -p <files> -f <package> -o <tmp.json>` (names, default enabled, option defaults as JSON) and
   `java -jar <cli> list-patches --patches <files> --with-packages --with-versions --with-options -f <package>` (descriptions, option types, compatible versions; text parsed like `utils.sh` `detect_version`).
   Store the merged result in `bundle_info`. `cli`, `module` and `project` rows are not inspected (`project` rows are verified instead, `docs/superpowers/specs/2026-10-23-follow-project-design.md` §4.3).
4. Keep the two newest tags per upstream on disk, delete older ones (a job started before the pull still has its files).
5. Optional rebuild: if the job's `options.rebuild` is true (absent = false; schedules default true and the UI buttons send true, decision Q4) and a new tag was downloaded, enqueue builds of the enabled items of the sources on this upstream.

The per-item button "Update upstream" calls `POST /api/upstreams/{item.source.upstream_id}/pull {"rebuild":true}` (body stored as the job's `options`) → enqueues the pull job, returns its job id. The UI must say that this updates the source for all its items (upstream is per source, not per app). CLI upstreams are pulled the same way ("Update CLI" in Settings pulls every `cli` row; schedule targets include the sources' CLI upstreams, core §4). A new CLI tag does not trigger a rebuild.

#### 5.3 Build job input (canonical field list: build §3.1 `job.json`)
Function `catalog.job_input(item_id) -> dict`, called by the build handler when the job starts. It fills these `job.json` fields:
- `item_id`, `source_id`, `package`, `key`, `engine` (= `source.engine`), `engine_options` (= `item.engine_options`).
- `patches_files` and `cli_jar` = absolute paths in `/data/upstream/<upstream_id>/<downloaded_tag>/` (opaque to the build); `upstream_tag` = source upstream `downloaded_tag`; `cli_tag` = `downloaded_tag` of the source's CLI upstream; for Xposed items `patches_files` = module APK(s), `cli_jar` = engine jar, `options_file_json` = null (xposed §5.2); `keystore` = `source.keystore`.
- `apkmirror {list_url, example_url, example_url2, versions_due}` and `apkpure_url` from the `app` row (apps.json is folded into `app`); `versions_due` per §3.
- `version` = `{mode: version_mode, value: version_pin, near: allow_nearest}`.
- Morphe items: `options_file_json` = `[ {"patches": overrides minus stale} ]`, one entry per patches file. This is the only patch-selection input; `enabled` true/false replaces include/exclude.
- `recipe` = `{"variants": item.variants}` verbatim.
The build never calls GitHub: `dl_gh`, `morphe_dl`-style functions and `src/etc/connection.sh` (`local/build.sh` per-org loop) leave the build path. If `downloaded_tag` is null for the source or CLI upstream, `job_input` raises and the build fails with `upstream_missing` (build §5); there is no silent download inside the build.

#### 5.4 Schedules
The scheduler engine is core's (core §6). A pull is a core job: `kind='upstream'`, target `{"upstream_id":N}`, registered by `upstream.py` with `jobs.register('upstream', handler)`; its log shows each GitHub call, download and inspect step. Schedules of kind `upstream` target `{"source_ids":[…]}` or `{"all":true}` (expanded by `catalog.resolve_upstreams`), `options.pull` (default true) and `options.rebuild` (default true, Q4): enqueue `jobs.enqueue('build', {"item_id":N}, 'upstream')` for enabled items whose source got a new tag.
"Check only" (no download) is the synchronous route `POST /api/upstreams/check` (≈18 GitHub requests, no job); it updates state and emits `upstream.new_version` with `job_id` null.
Seed: one daily schedule `{"every":"day","at":"09:00"}` of kind `upstream`, target all, `rebuild=true`.

### 6. Interfaces summary

| Direction | Interface |
|---|---|
| exposes → UI | `GET /api/catalog`, `GET /api/items/{id}` (item object below); CRUD `/api/sources`, `/api/items`, `/api/groups`; the reorder, patches, version, upstream check/pull and icon routes above |
| exposes → arch-build | job input §5.3 via an in-process function; `variants` JSON stored as given |
| exposes → arch-device | `project`, `item_dependency`, `device_projects` rows and `projects.project_info` (`docs/superpowers/specs/2026-10-23-follow-project-design.md` §4.2); event `upstream.new_version` |
| exposes → core | `resolve_items(target)`, `resolve_upstreams(target)`, upstream job handler, event `upstream.new_version` |
| consumes | `build.item_status(item_ids)` for the item object (build §3.4); Java toolchain in the web container (build §2.1); `jobs.enqueue('build', ..., 'upstream')` (core §5); token file; scheduler (core §6); notification sender (device) |

Item object (one shape for the App list and App detail, ui §2):
- `GET /api/catalog` → `{groups:[{id, name, color, position, items:[item…]}], ungrouped:[item…]}`, groups and items in `position` order.
- item = `{id, label (§3.2), display_name, source_id, source_name, engine, package, key, enabled, group_id, position, icon_url, abis:[variant abi…], upstream:{id, repo, channel, downloaded_tag, latest_tag, checked_at, check_error}, last_build, last_success, active_job}`; the last three come from `build.item_status` (build §3.4).
- `GET /api/items/{id}` = item + `version {mode, pin, allow_nearest}`, `variants`, `engine_options`, `project_ids`, `apkmirror {versions, checked_at}` (from `app.apkmirror_versions`/`apkmirror_checked_at`, §3; the Version tab marks versions newer than `compatible_versions[0]` as "not in patch list", ui §2).
- `POST /api/items {source_id, package, group_id, display_name?, variants?, engine_options?}`: creates the `app` row if missing (icon fetched by the background task, §3.3); `variants` default = universal + arm64-v8a, APKMirror `apk` download (decision Q5). `PUT /api/items/{id}` takes the same fields plus `enabled`, `project_ids`. `DELETE /api/items/{id}` follows the delete rules of §0.3 (409 while a build of the item is queued or running). When the created item's source upstream is a `patches` row with a `downloaded_tag` and no `bundle_info` row exists for `(upstream_id, downloaded_tag, package)`, `POST /api/items` also enqueues the pull job of that upstream with `options` `{"rebuild":false}`; step 1 of §5.2 then finds the missing package and re-inspects. The step-1 rule alone would wait for the next scheduled or manual pull, so the edit page would show "patch list unavailable" until then; one enqueue at creation is the smallest fix.
- `POST /api/sources {id, name, engine, repo, channel, tag?, asset_regex?, keystore, cli_upstream_id}`: `repo/channel/tag` are deduped into an `upstream` row (kind `patches`, or `module` when `engine` ≠ `morphe`, xposed §5.1). `DELETE /api/sources/{id}` answers 409 while items use it.

### 7. Data flow

```
schedule / button ──► release tracker ──GitHub API──► upstream row (latest_tag)
                                   │ new tag
                                   ├─► download → <data>/upstream/<id>/<tag>/
                                   ├─► inspector (java child process) → bundle_info
                                   ├─► event upstream.new_version → notifications
                                   └─► (rebuild) enqueue item builds
edit page ──► item.patch_overrides / version_pin  ◄── bundle_info (validation, display)
build start ──► job input writer(item) → arch-build job
app created ──► icon fetcher (Play) → <data>/icons/<pkg>, app.display_name
```

### 8. Seed and deletion (no backward compatibility)

- During implementation, a one-off script (not kept) scans `src/build/*.sh` (`get_patches_key`/`get_apk`/`patch`/`dl_gh`/`version=` lines), `src/patches/**`, `src/options/*.json`, `src/build/helper/apps.json`, and writes one committed seed file `local/webui/catalog-seed.json` with sources, upstreams, apps, items, groups, overrides, pins, variants (in build §3.1 format), dependency links.
- Overrides conversion: exclude line `X` → `{"X":{"enabled":false}}`; include line `X` → `{"X":{"enabled":true}}`; each options-file `patches` entry copied (enabled + options). The `|`-suffix in include lines was never applied (`utils.sh` `get_patches_key`) and is dropped.
- The web server imports the seed only when the catalog tables are empty. After that the database is the only source of truth. `python3 -m webui export-seed` writes the current catalog back in seed format (`catalog.seed_text`).
- Decision Q3: BiliRoamingM and Dropped-Patches are seeded as normal items; the first build shows whether they still patch.
- Decision Q5: the seed keeps only variants with `abi` `universal` or `arm64-v8a` (e.g. morphe YouTube keeps 2 of 5, `src/build/morphe.sh`; anddea YouTube Music keeps arm64-v8a); an item with neither keeps all its variants and the seed script lists it. Other ABIs are added per item in the UI.
- Xposed: the seed adds 4 upstreams, 3 sources, 8 items and group "Xposed" (xposed §4.1).
- Not migrated: 17 unreferenced `src/patches` dirs (preflight-patches §4), `paresh.json`, `piko-x-shim.json` (no caller), the `scrazzz-patches` row (script missing, `local/build.sh` `ORGS` table), commented-out builds (Hola-VPN, TeraBox, medium, strava, GoogleNews). They can be re-added in the UI.
- Deleted after seed: `src/options/`, `src/patches/`, `src/build/helper/apps.json` (moved into `app`), the org table in `local/build.sh`, the per-org scripts and `src/build/Xposed.sh` (build §7). The old GitHub workflows are deleted and not replaced (decision Q1). The untracked `hushfacebook.json` and `piko-newx.json` are read by the seed script, then removed with the directory.

### 9. Error handling

| Failure | Behaviour |
|---|---|
| GitHub 401 (bad token) | `check_error="token rejected"`; stop further GitHub calls in that run (repeated 401 can lock out, followup spec "Known risks"); banner in settings |
| GitHub 403/429 rate limit | `check_error="rate limited until <X-RateLimit-Reset>"`; skip the rest of the run; next schedule retries |
| Repo/tag not found (404) | `check_error="not found"` on that upstream; others continue |
| No asset matches regex | `check_error="no matching asset"`; `downloaded_tag` unchanged |
| Download size/hash mismatch or interrupted | delete `<tag>.part/`; keep old tag; error on upstream row |
| Inspect fails (java error, CLI too old for bundle) | keep files and `downloaded_tag`; `bundle_info` missing → edit page shows "patch list unavailable" and blocks override edits, builds still allowed with stored overrides; the next pull of the same tag re-runs inspect (§5.2 step 1) |
| Override names a patch missing in new bundle | kept as `stale_overrides`; dropped from build input; warning in build log |
| Pin not in compatible list | warning on edit page; build proceeds |
| Play icon/name fetch fails | letter tile, package name; retry via refresh button |
| Concurrent pull of same upstream | pulls are core jobs on one serial worker; a second request while one is queued returns the queued job id (core dedup) |
| Edit while the item is building | the running job already has its input snapshot; edit applies to the next build |

### 10. Testing approach

- Release pick: unit tests with saved GitHub JSON fixtures for stable, prerelease (prerelease newer than stable, and stable newer than prerelease), tag, draft present, `dev` asset skipped, `.asc` skipped, no matching asset.
- `notified_tag`: two checks with the same tag emit one event.
- Pull atomicity: interrupted download leaves the previous tag usable (fault injection on a temp dir).
- Fresh install: CLI upstream not downloaded → patches pull → CLI row gets `downloaded_tag` and `bundle_info` exists; `bundle_info` missing with unchanged tag → pull re-inspects; new item for a package without `bundle_info` on an already-inspected tag → `POST /api/items` enqueues the pull → pull re-inspects (tag unchanged) and `bundle_info` exists for the new package.
- Inspector parser: fixture = captured `list-patches` and `options-create` output from one real bundle (captured once during implementation).
- Override merge: defaults + overrides + stale → expected `options_file_json`; empty overrides produce `[{"patches":{}}]`.
- Seed: import into an empty DB; assert source count = 20 (18 org rows minus scrazzz, plus 3 Xposed sources), the 8 Xposed items present with Facebook pinned `580.0.0.51.74`, no seeded variant with an ABI other than universal/arm64-v8a unless the item has neither, item count = number of distinct (source, package) pairs reported by the seed script, every non-empty `src/patches` and `src/options` value present as an override; second start does not re-import.
- Reorder: full-order PUT yields positions 0..n-1; moving an item between groups.
- Group colour: create and update with `#1a2B3c` stored and returned by `GET /api/catalog`; `red`, `#123` → 400; null clears it.
- End-to-end (user-run, long): pull morphe, edit one override, build YouTube, confirm the override appears in the CLI log line for that patch.
- Xposed tests: xposed §11.

### 11. User decisions (catalog)

Answered in §0.6: Xposed kept (Q2, Part 7); BiliRoamingM and Dropped-Patches seeded (Q3, §8); a new upstream version rebuilds (Q4, §5.2).

### 12. Uncertainties

- Whether the Morphe CLI accepts override names that do not exist in one of several bundles (piko-newx multi-`.mpp` case). Test during implementation with the real piko-newx release.
- `options-create` output fields beyond `enabled`/`options` (the untracked files carry `meta.source`/`sha256`, e.g. `src/options/piko-newx.json`, but the docs do not mention them); the inspector must not depend on `meta`.
- Exact `list-patches` text layout for option types; parser built against captured output.
- Google Play page markup can change; failure path is the letter tile.


# Part 3 — Build orchestration


Scope: per-(patch source × app) builds driven by the web server; job queue and concurrency; live log; history and failure reasons; artifact naming and retention; APKMirror rate limit and solver use; refactor of `src/build/*.sh`, `src/build/utils.sh`, `local/build.sh`.

Terms: **item** = one (patch source × app) row of the catalog (owned by catalog part). **job** = one row of the core `jobs` table with `kind='build'`, building one item. **variant** = one output APK of a job (e.g. universal, arm64-v8a). `DATA/` = `/data` in the `app` container (host `local/data/`, core §1).

### 1. Today (facts the design replaces)

- Smallest unit is an org "run" (a numbered shell function), not an app: `src/build/morphe.sh` dispatches by `$1`; one run can hold several apps (`src/build/hoo-dles.sh` = Prime Video + AdGuard).
- The org registry is a bash table (`local/build.sh` `ORGS` table); runs are serial (`local/build.sh` per-org loop) through `docker compose exec` into the builder (`local/build.sh` per-org loop).
- Work dir `local/work/<org>-<run>` is wiped per run (`local/build.sh` per-org loop); APKs are copied flat into `local/out/<org>/`, overwriting, never cleaned (`local/build.sh` per-org loop). File names carry no version (`src/build/utils.sh` `patch`).
- No log is persisted; output is stdout with ANSI colours (`src/build/utils.sh` `green_log`, `red_log`, `yellow_log`), no timestamps.
- PASS = exit 0 and ≥1 APK per run (`local/build.sh` per-org loop); `rc` is unreliable because scripts have no `set -e` and `patch` uses `--continue-on-error` (`src/build/utils.sh` `patch`).
- Sourcing `utils.sh` has side effects: `mkdir`, pup + APKEditor download, UA scrape (`src/build/utils.sh` top-level code).
- Patches and CLI are re-downloaded every run (`dl_gh`, `src/build/utils.sh`); `get_patches_key` rewrites `src/patches/*` in place with `sed -i` (`src/build/utils.sh` `get_patches_key`).
- Version auto-detect works only for `*.mpp` and silently falls back to the newest APK when empty (`src/build/utils.sh` `detect_version`, `get_apk`).
- Solver endpoints are hard-coded `http://localhost:$PORT` (`src/build/utils.sh` `_fs_get`, `_cfb_get`, `get_apk`), which forces the builder into flaresolverr's network namespace (`local/docker-compose.yml` at 9fd15dc, `network_mode: service:flaresolverr`).

### 2. Components and responsibilities

| Component | Where | Responsibility |
|---|---|---|
| Build runner | `src/build/build-item.sh` (new, bash) | Executes exactly one job from a `job.json`: resolve version, get input APK(s), patch every variant, write `result.json`. No knowledge of DB, queue or HTTP. |
| Build library | `src/build/utils.sh` (slimmed) | Side-effect-free functions: logging, APKMirror/APKPure fetch (`get_apk`, `get_apkpure`, `_cf_get` stack, `req`), `detect_version`, `split_editor`, one `patch_variant`. |
| Job service | `local/webui/build.py`, registered as `jobs.register('build', handler, detail)` (core §5) | Writes `job.json`, spawns the runner through core `log.run` (process group, cancel, timeout, log capture and SSE fan-out are core's), classifies the result, registers artifacts, runs retention, emits events. Queue and the single build worker are core's. |
| Input APK cache | `DATA/apk-cache/` | Keeps the last downloaded input per (package, download spec) so rebuilds after an option change do not hit APKMirror again. |

Nothing else is added: no message broker, no separate worker container, no docker socket.

#### 2.1 Execution placement (requirement on core part)

The job service spawns `bash src/build/build-item.sh <job.json>` as a **child process in the web server container**. Therefore that container image must contain today's builder toolchain (`local/Dockerfile` apt-get `RUN`: java 21, apksigner, jq, wget, curl, unzip, zip, python venv) plus pup v0.4.0 and APKEditor 1.4.9 baked in at image build (moved out of `src/build/utils.sh` top-level code). The image is multi-arch: the pup binary is chosen by the build architecture (today `uname -m`, `src/build/utils.sh` top-level code), so it also builds on amd64. The separate `builder` service and `docker compose exec` path are deleted.

Solver reachability: `utils.sh` reads full URLs `FLARESOLVERR_URL`, `CFBYPASS_URL`, `APKM_SOLVER_URL` from env instead of `localhost:$PORT`. Compose sets them to `http://127.0.0.1:47191`, `http://127.0.0.1:47800`, `http://127.0.0.1:47900` (the sidecars share the `netns` network namespace with `app` and bind loopback only, core §1). `local/solver/solver.py` (the `ThreadingHTTPServer` bind) changes its bind address from `0.0.0.0` to `127.0.0.1`.

### 3. Interfaces

#### 3.1 `job.json` (written by job service, read by runner)

Built when the job **starts** (not when queued), so a queued job uses the latest edits. The catalog part's job input writer (catalog §5.3) supplies every field except `job_id` and `work_dir`; the field names below are the canonical ones.

```json
{
  "job_id": 123, "work_dir": "DATA/work/123",
  "item_id": 7, "source_id": "morphe", "package": "com.google.android.youtube", "key": "youtube",
  "engine": "morphe", "engine_options": null,
  "upstream_tag": "v1.21.1", "cli_tag": "v1.18.1",
  "patches_files": ["DATA/upstream/<id>/<tag>/patches-1.21.1.mpp"],
  "cli_jar": "DATA/upstream/<cli id>/<tag>/morphe-desktop-1.18.1-all.jar",
  "options_file_json": [ { "patches": { "<patch name>": { "enabled": true, "options": {} } } } ],
  "version": {"mode": "auto", "value": null, "near": false},
  "apkmirror": { "list_url": "...", "example_url": "...", "example_url2": null, "versions_due": true },
  "apkpure_url": null,
  "keystore": "morphe.keystore",
  "recipe": { "variants": [
    { "name": "universal", "abi": "universal",
      "download": {"kind": "apkmirror", "type": "apk", "arch": null, "dpi": null, "minver": null, "url": null},
      "exclude_splits": null, "strip_libs": null },
    { "name": "arm64-v8a", "abi": "arm64-v8a", "download": {"...": "same as above"},
      "exclude_splits": null, "strip_libs": "arm64-v8a" }
  ] }
}
```

`recipe.variants` is stored opaquely by the catalog as `item.variants` and copied verbatim; its schema is owned here. Field semantics map 1:1 to today's script arguments, so every current script body is expressible as data:
- `download.kind`: `apkmirror` = `get_apk` (`src/build/utils.sh` `get_apk`), `apkpure` = `get_apkpure` (both in `src/build/utils.sh`), `url` = `req` direct download (`src/build/Dropped-Patches-Revanced.sh`).
- `download.type` = `get_apk` 3rd arg (`apk|bundle|bundle_extract`, `src/build/utils.sh` `get_apk`); `arch/dpi/minver` = args 4-6.
- `exclude_splits` = `split_editor <in> <out> exclude "<list>"` (`src/build/morphe.sh`); `[]` = merge all splits (`src/build/morphe.sh`); `null` = no split step.
- `strip_libs` = `split_arch` `--striplibs` (`src/build/utils.sh` `split_arch`).
- `abi` ∈ `arm64-v8a|armeabi-v7a|x86|x86_64|universal`; the device part matches it against the phone ABI.
- More than one `patches_files` entry = today's `patch_multi` (`src/build/utils.sh` `patch_multi`). `patches_files` = the engine's payload files: patches bundles for `engine=morphe`, module APKs for `lspatch`/`npatch` (xposed §5.2).
- Patch selection and options come only from `options_file_json` (written to `options.json`, passed as `--options-file`); no `-e/-d` flags.
- `apkmirror` / `apkpure_url` replace the `src/build/helper/apps.json` lookup in `get_apk`/`get_apkpure` (`src/build/utils.sh` `get_apk`, `get_apkpure`); those functions take the URLs as arguments.

Mapping examples: morphe YouTube = one apk download, 5 variants (`src/build/morphe.sh`); morphe Reddit = `bundle_extract`, variants `all-splits` (`exclude_splits: []`) + `arm64-v8a` with exclude list (`src/build/morphe.sh`); anddea YouTube Music = 4 variants each with its own `download.arch` (`src/build/Anddea-Revanced-Extended.sh`).

#### 3.1.1 Patch inspection (for the catalog part)

`bash src/build/inspect.sh <cli_jar> <package> <out_dir> <patches_files...>` runs the two catalog commands (`options-create`, `list-patches`; catalog §5.2 step 3) and writes their outputs to `out_dir`. It runs as a direct subprocess of the web server (same container, §2.1), **outside the build queue**: it never touches APKMirror, so it does not count against the single-build limit. Timeout 120 s.

#### 3.2 Runner → job service (process contract)

- stdout+stderr: free-form log lines (existing `[+]`/`[-]`/`[!]` messages kept).
- Failure marker: on any fatal step the runner prints exactly one line `::fail::<code>::<detail>` and exits non-zero. Codes in §5.
- APKMirror versions marker: `::apkm_versions::<v1>,<v2>,…` (newest first, betas/alphas dropped, §4 step 5d). The job service calls `catalog.set_apkmirror_versions(package, versions)` for it.
- Progress markers: `::step::<name>[::<detail>]` at the start of each step, names `version`, `download`, `patch` (detail `<i>/<n>` = variant i of n), `verify`; the job service logs `::step::store` itself. `::version::<app_version>` once the version is resolved. The job service reads them through `log.run(on_line=…)` (core §5): it keeps the current step of the running job in memory (for `build.item_status`) and writes `build_jobs.app_version` at once. The Builds page derives step times from the log's `HH:MM:SS` prefixes (ui §2).
- `DATA/work/<job_id>/result.json` on success: `{app_version, patches_version, cli_version, variants:[{name, abi, file, size, sha256, package, version_name, version_code, signer_sha256}]}`. `package`/`version_*` are read from the patched APK with `java -jar APKEditor.jar info -i <apk> -package -version-code -version-name` (flags per APKEditor README, verify against the pinned 1.4.9 at implementation); `signer_sha256` from `apksigner verify --print-certs` (apksigner is in the image, `local/Dockerfile` apt-get `RUN`).
- Success = exit 0 **and** `result.json` present **and** every declared variant file exists and is non-empty. Exit code alone is not trusted (see §1).

#### 3.3 DB tables (schema file held by core part)

```
build_jobs(job_id PK FK jobs(id) ON DELETE CASCADE, item_id FK item(id) ON DELETE CASCADE,
  fail_code TEXT NULL, fail_detail TEXT NULL,
  app_version TEXT NULL, patches_version TEXT NULL, cli_version TEXT NULL)
  -- status, trigger and times live in core `jobs`; jobs.reason = "<fail_code>: <fail_detail>"
artifacts(id PK, job_id FK jobs(id) ON DELETE RESTRICT, item_id FK item(id) ON DELETE CASCADE, variant TEXT,
  abi TEXT  -- arm64-v8a|armeabi-v7a|x86|x86_64|universal
  package TEXT, version_name TEXT, version_code INT, patches_version TEXT,
  signer_sha256 TEXT, path TEXT, size INT, sha256 TEXT, created_at)
```
Index: `build_jobs(item_id, job_id DESC)`, `artifacts(item_id, abi, created_at DESC)`. Schema file `local/webui/schema/002_build.sql`.
Settings key: `artifact_retention` (int, default 1, per item). Job history size is core's `job_history_count`.
Log file is core's `/data/logs/<job id>.log`.

#### 3.4 HTTP API (route list held by core part)

| Route | Purpose |
|---|---|
| `POST /api/items/{id}/build` | Enqueue (trigger=manual). If a job for this item is already `queued`, return that job (no duplicate). If one is `running`, a new one is queued behind it. |
| `POST /api/builds` body `{item_ids:[...]}` | Enqueue many (group "build all"). Same dedup per item. |
| core `/api/jobs` routes | History, cancel, live log (core §8). `GET /api/jobs/{id}` `detail` for builds = `build_jobs` row + artifacts. |
| `GET /api/items/{id}/artifacts` | Non-deleted artifacts, newest first. |
| `GET /api/artifacts/{id}/download` | Streams the file (`Content-Disposition: attachment`), 100–230 MB files, no buffering. |
| `GET /api/groups/{id}/download` | Group "Download all (zip)". Items = `catalog.resolve_items({"group_ids":[id]})` (enabled items; id `0` = Ungrouped). For each item: every artifact of its newest `success` build job; items without one are skipped; 404 when no file remains. Response = a ZIP streamed while it is written: stdlib `zipfile` in `ZIP_STORED` mode (APKs are already compressed) writing to the response socket, entry names = the artifact file names (§4 step 7), ZIP64 as needed, no `Content-Length`, `Connection: close`, `Content-Disposition: attachment; filename="<group name slug>-<YYYYMMDD>.zip"`. Core's `server.py` provides `StreamResponse(write_fn, download_name)` for this (core §3). Each file is opened before it is written, so retention cannot break it (§6). No temp file, no extra disk. |

In-process: builds are enqueued with core `jobs.enqueue('build', {"item_id":N}, trigger)` (dedup is core's).
- `item_status(item_ids) -> {item_id: {last_build, last_success, active_job}}` for the catalog item object (catalog §6): `last_build {job_id, status, reason, fail_code}` = newest finished build job; `last_success {job_id, app_version, patches_version, finished_at}` = newest `success`; `active_job {job_id, status, step, step_index, step_count}` = queued or running job (step fields null while queued). Null when absent.
- `needs_build(item_id) -> bool` (schedules with `only_changed`, core §6): true when the item was never built, its newest build failed, or the source's or CLI's `downloaded_tag` differs from the newest success's `patches_version`/`cli_version`. A new app version on APKMirror is not detected (no APKMirror call outside builds).
- `forget_item(item_id)`: deletes `DATA/artifacts/<item_id>/` (files only); called by the catalog item delete route before the `item` row is deleted, whose CASCADE removes `build_jobs` and `artifacts` rows (§0.3 delete rules).
- Used by device part: `latest_artifact(item_id, abi)` → from the newest `success` build job of the item: the artifact with `abi = <abi>`, else `abi = 'universal'`.

#### 3.5 Events (for notifications part)

`build.succeeded {item_id, job_id, app_version, patches_version, artifacts:[artifact ids]}` (device part subscribes for OTA-after-build) and `build.failed {item_id, job_id, fail_code, fail_detail}`. Cancelled jobs emit nothing.

### 4. Data flow

1. Trigger (button, schedule, upstream pull) → `jobs.enqueue('build', ...)` → core `jobs` row `queued`.
2. Core's single build worker picks the oldest `queued` build job (FIFO), sets `running`, calls the build handler.
3. Job service reads catalog → writes `DATA/work/<id>/job.json` + `options.json` (no `options.json` for Xposed items); inserts the `build_jobs` row with `patches_version = upstream_tag`, `cli_version = cli_tag` (shown on the Builds page while running). Precheck: every `patches_files` and `cli_jar` path exists, else fail `upstream_missing` without spawning.
4. Spawn `bash src/build/build-item.sh job.json` via core `log.run(..., timeout=3600)` (own process group), cwd = work dir, env = the three solver URLs only. No `GITHUB_TOKEN`: builds never call GitHub (§5.1), and the runner executes third-party patch code that must not see the token.
5. Runner:
   a. `init_ua` (UA scrape moved out of source-time, `src/build/utils.sh` top-level code).
   b. Symlink `cli_jar` and `patches_files` into the work dir (globs `morphe-desktop-*.jar`, `*.mpp` keep working).
   c. Version (rule owned by catalog §4.2): `version.mode='pinned'` → `version=<value>`; `'latest'` → empty version, newest non-beta on APKMirror (`src/build/utils.sh` `get_apk`); `'auto'` → `detect_version`, and if that is empty → `::fail::version_unresolved` (the silent fallback of `src/build/utils.sh` `get_apk` is removed; the user picks `latest` explicitly). `version.near` → `near_version=1`.
   d. APKMirror versions (restored mockup feature, §0.6 R4): whenever this step fetches the app's APKMirror list page anyway (version `latest`, or the version page lookup falls back to the list page), the runner prints `::apkm_versions::` from that page (0 extra requests). Otherwise, when `apkmirror.versions_due` is true and the item has an `apkmirror` download, it fetches the list page once through the same `_cf_get` chain, after the input APK is obtained, and prints the marker; a failure only logs `[!]` and never fails the job. So APKMirror sees at most one extra request per package per 24 h, only inside builds on the single build worker; no page load and no schedule of its own scrapes APKMirror. Versions are parsed from the `h5.appRowTitle a.fontBlack` titles, beta/alpha excluded (same selector as `get_apk` in `src/build/utils.sh`).
   For each distinct `download` spec: look in `DATA/apk-cache/<package>/<version>-<type>-<arch>-<dpi>.apk|apkm`; hit → copy; miss → `get_apk`/`get_apkpure`/`req`, then store in cache replacing older files of the same spec. Cache bypassed when the version is empty or `allow_nearest` is set (resolved version unknown before scrape).
   e. For each variant: optional `split_editor` (exclude_splits), then `patch_variant` = one CLI call: `java -jar <cli> patch -p <files...> --options-file options.json --keystore=src/keystore/<keystore> --force --continue-on-error [--striplibs <abi>] --out=release/<variant>.apk <input>` (merges `patch`, `patch_multi`, `split_arch` of `src/build/utils.sh`). Missing output → `::fail::patch_failed`. `engine=lspatch|npatch` uses the engine command, output-dir rule and keystore credential table of xposed §5.3 instead.
   f. Write `result.json` (sha256 via `sha256sum`, package/version via APKEditor `info`, signer via `apksigner verify --print-certs`). A variant whose package cannot be read → `::fail::patch_failed`.
6. Core `log.run`, line by line: strip ANSI, prefix `HH:MM:SS`, append to `/data/logs/<id>.log`, wake SSE readers of that job.
7. On exit: validate (§3.2); move each variant to `DATA/artifacts/<item_id>/<job_id>/<source_id>-<key>-<variant>-<app_version>-<upstream_tag>.apk` (source prefix keeps downloads from morphe and morphe-beta distinct on the user's disk); insert `artifacts` rows; fill `build_jobs` versions; handler returns (job `success`); else raise `JobFailed` with the last `::fail::` line (no marker → `internal`, detail = last `[-]` line).
8. Delete `DATA/work/<id>/`. Run retention for the item (§6). Emit event.

### 5. Error handling

| fail_code | Raised by | Meaning / user action |
|---|---|---|
| `upstream_missing` | job service precheck | No downloaded patches/CLI for the source → run "Update upstream". |
| `version_unresolved` | runner step c | `auto` mode and the bundle lists no compatible version → pin a version or choose `latest`. |
| `apk_not_listed` | runner step d | Item has no `apkmirror.list_url` (today `src/build/utils.sh` `get_apk`). |
| `download_failed` | runner step d | Page scrape or file download failed after FlareSolverr → CFBypass → Turnstile solver chain (`src/build/utils.sh` `_cf_get`, `get_apk`). Detail = last `[-]` line. |
| `merge_failed` | runner step e | APKEditor merge produced no APK. |
| `patch_failed` | runner step e | CLI exited non-zero or output missing. |
| `timeout` | core `log.run` | Wall clock > 60 min → kill. |
| `cancelled` (status) | job service | User cancel. |
| `interrupted` | core start | Row was `running` when the server stopped (core reason `server restarted`). |
| `internal` | job service | Runner exited without marker. |

Cancel/timeout: `SIGTERM` to the process group, `SIGKILL` after 10 s; work dir deleted; no artifacts registered.
Restart: core marks `running` → `failed` (`server restarted`); `queued` rows are resumed by the worker.
`--continue-on-error` stays (today's behaviour): a single patch failing does not fail the job; its CLI error lines remain visible in the log.

#### 5.1 APKMirror rate limit and solver

- Concurrency is fixed at **1 running build** globally (core's one worker per kind). Reasons: APKMirror 429 is IP-wide after a burst (`docs/logs/2026-10-19-preflight-runtime.md` §4), all jobs share one exit IP, and each Turnstile solve peaks ~1.09 GB with an unbounded `ThreadingHTTPServer` (`local/solver/solver.py`). `ponytail: global single worker; per-host-resource limits only if build throughput matters.`
- Within a job, downloads stay sequential as today; the input APK cache removes repeat hits for rebuilds.
- The solver is still only called after a failed plain download (`src/build/utils.sh` `get_apk`), now via `APKM_SOLVER_URL`.
- GitHub API is no longer called by builds (downloads move to the catalog's upstream fetcher), so build volume no longer spends GitHub quota; `src/etc/connection.sh` (a GitHub reachability probe, `src/etc/connection.sh` `check_connection`) is deleted.

### 6. Retention and cleanup

- After every successful job, and for every item when `PUT /api/settings` changes `artifact_retention` (core §8): for that item keep the newest `artifact_retention` succeeded jobs' files; older artifact files and their `artifacts` rows are deleted (history still shows the versions from `build_jobs`). The newest successful job's artifacts are never deleted.
- Race with an in-flight OTA install needs no lock: only artifacts older than the newest N jobs are deleted, and on POSIX an unlinked file stays readable through an already-open descriptor, so an install that has opened the file finishes. The device part opens the file before starting the push.
- Job rows and logs are cleaned by core (`job_history_count`); a job that still has artifacts is kept (`ON DELETE RESTRICT`).
- `DATA/work/<id>/` is deleted at job end in every outcome. On server start, any `DATA/work/*` dir is deleted (no job can be running then).
- `DATA/apk-cache/`: one file per (package, download spec); a new version replaces the old one. `ponytail: no size cap; add LRU-by-size if disk becomes a problem.`

### 7. Refactor and deletions

Deleted (project is unreleased; no compatibility): `local/build.sh`; every org script `src/build/{morphe,morphe-beta,Anddea-Revanced-Extended,Anddea-Revanced-Extended-Beta,Piko,Piko-Beta,Piko-Newx,DeRevanced,HushFacebook,hoo-dles,bufferk,arandomhooman,icysymmetra,docbt,rushiranpise,BiliRoamingM,Dropped-Patches-Revanced}.sh` (their bodies become catalog variant data); `src/build/Xposed.sh` (its apps become catalog data, xposed §4.1); `src/etc/connection.sh`; the `builder` service; `local/out/`, `local/work/`; the old GitHub workflows, actions and `src/etc/ci.sh`.

`src/build/utils.sh` changes:
- Remove top-level side effects `src/build/utils.sh` top-level code (tools into image; UA into `init_ua`).
- Remove `dl_gh`, `dl_gl` (`:37-133`, upstream download is catalog's), `get_patches_key` (`:138-172`, selection now lives in the options file), `get_apk_chplay` (`:645`, no active caller), `telegram_dl` (`:704`, no caller; secret `TDL_BACKUP` is retired with it), `apk_editor` (`:882-888`, no caller).
- Replace `patch`, `patch_multi`, `split_arch` with `patch_variant`. Replace `exit 1` inside library functions (`:772`) with `return 1` so the runner decides the fail code.
- Solver URLs from env (§2.1).
- Remove `lspatch`/`npatch` (`:797-843`); the `patch_variant` engine branch replaces them (xposed §5.3).

Owned by other parts: `src/options/*`, `src/patches/*`, `src/build/helper/apps.json` migration and the seed of `item.recipe` from script bodies (catalog §8).

### 8. Testing

- Runner self-check `src/build/test-build-item.sh`: runs the runner against a fixture `job.json` with `download.kind=url` pointing at a local file and a stub CLI jar replaced by a shell shim on PATH; asserts `result.json` variants, file names, and that a missing input yields `::fail::...` + non-zero exit. No network.
- Build handler unit tests: `job.json` written from a fake catalog input; retention keeps N and never the newest; artifact registration from `result.json`. (Queue, cancel, restart and SSE are covered by core tests.)
- Group ZIP: two items, one without a success → ZIP lists only the other item's artifact names, `zipfile.ZipFile` reads it back, sizes match; empty group → 404.
- APKMirror versions: a `::apkm_versions::` line from a fake runner → `app.apkmirror_versions` set; runner self-check with `versions_due=false` and a pinned version makes no list-page fetch (fetch shim records calls).
- Failure classification test: fake runner scripts that print each `::fail::` code / no marker / exit 0 without result.json → expected `fail_code`.
- Engine cases (LSPatch/NPatch argv, output-dir rule, unknown keystore): xposed §11.
- One live end-to-end (user-run, long): morphe YouTube item builds, artifact name carries app + patches version, `apksigner verify` passes.

### 9. User decisions (build)

Answered in §0.6: GitHub Actions builds dropped (Q1); Xposed apps kept (Q2, Part 7); seeded variants universal + arm64-v8a only (Q5, catalog §8).

### 10. Uncertainties

- Run duration is unmeasured; the 60 min timeout is a guess sized for 5 patch passes + solver retries.
- APKEditor `info` flag names are from the current README, not verified against the pinned 1.4.9.
- Whether BiliRoamingM / Dropped-Patches (revanced-cli sources) still patch with morphe-desktop (`src/build/utils.sh` `patch`) is unverified; they are seeded (decision Q3) and the first build shows the result.


# Part 4 — Devices, OTA, notifications


Covers requirements 3 (wireless ADB OTA, one-click + scheduled), 4b (notifications), 4d (multiple phones, per-phone assignment, dependency apps).
Facts cited from `docs/logs/2026-10-19-preflight-runtime.md` (= RT) and `docs/logs/2026-10-19-preflight-patches.md` (= PP). Web sources fetched 2026-10-19.

### 1. Decisions

| # | Decision | Reason / evidence |
|---|---|---|
| D1 | `adb` runs **inside the web server container** as a child process (one adb server, `adb start-server` at boot). No separate device service. | adb is client-initiated TCP to the phone (RT §6); the builder has no adb (RT §3). One process owner = no cross-container adb server sharing. |
| D2 | adb comes from the distro package (`apt install adb`, Ubuntu noble; verified 2026-10-19 in `ubuntu:24.04` arm64: `adb version` → `Version 34.0.4-debian`). | Google platform-tools ships no Linux arm64 build; distro adb is the only arm64 option without building AOSP. |
| D3 | **The app does mDNS discovery itself** with the Python standard library: a DNS-SD "legacy unicast" one-shot query (RFC 6762 §6.7: a query sent from a port other than 5353 gets unicast replies). UDP socket on an ephemeral port, PTR questions for `_adb-tls-pairing._tcp.local` and `_adb-tls-connect._tcp.local` sent to `224.0.0.251:5353`, replies read for 2 s, PTR/SRV/A records parsed (DNS name compression included). No multicast group join and no bind on 5353, so it does not conflict with a host avahi-daemon in Linux host mode. Then adb is called only with explicit `ip:port`. adb's own mDNS is not used. | Distro adb builds are often compiled without mDNS (ArchWiki "Android Debug Bridge"); verified 2026-10-19 for Ubuntu's 34.0.4: `adb mdns check` prints nothing (exit 0) with and without `ADB_MDNS_OPENSCREEN=1`, and `adb server-status` is an unknown command. adb 34.0.5 openscreen backend is off by default and has a truncated-query crash fixed only in 36.0.0 (platform-tools release notes). Pairing and connecting by explicit ip:port need no adb mDNS. |
| D4 | **One code path for Mac and Linux.** Discovery is a background browser whose results only *pre-fill* the same pair/connect forms the user fills by hand. On Mac Docker Desktop the browser simply finds nothing; UI shows "no devices discovered — enter IP and port". | Contract forbids per-platform forks. mDNS does not reach the LAN from Mac containers (RT §6, ~75% confidence) — so this is a runtime "empty result", not a code branch. Network-mode difference stays in compose (arch-core). |
| D5 | adb host key dir `/data/home/.android` (`adbkey`, `adbkey.pub`): the `app` container sets `HOME=/data/home` (core §1), so adb's default `$HOME/.android` is on the persistent data dir. | The key is the host identity the phone trusts after pairing (RT §5 [B] "known to the host from pairing"); losing it = re-pair every phone. |
| D6 | Signature mismatch is detected from the install result, not by a pre-check. On `INSTALL_FAILED_UPDATE_INCOMPATIBLE` the job stops and the UI offers "Uninstall and install (app data is lost)" requiring explicit confirmation. Never automatic, never in scheduled runs. | `pm install` already verifies the signer; a pre-check duplicates it. Uninstall destroys data (RT §5). |
| D7 | "Needs update" = the newest successful artifact's `build_job_id` differs from the `build_job_id` of the last successful install of that item on that device, **or** the package is missing on the phone. Version code is not the trigger. | A new patches release rebuilds the same app version (same versionCode); comparing versionCode would never ship patch updates. `adb install -r` reinstalls same version. |
| D8 | Dependency projects (MicroG RE) are installed **before** the item that needs them; the rule, ordering and signer blocking are in `docs/superpowers/specs/2026-10-23-follow-project-design.md` §5.4. | PP §10: YouTube, YouTube Music, Google Photos, Solid Explorer (hoo-dles) need MicroG RE. |
| D9 | Notifications: one channel, type `telegram` / `discord` / `ntfy`, with per-event toggles. Plain HTTPS POST from the web server, no SDKs. | Requirement 4b says "to one of" the three. Each is one HTTP request (§6). |

### 2. Components and responsibilities

All live in the web server process (arch-core's container).

| Component | Responsibility |
|---|---|
| Adb runner | Only module that executes `adb`. Serializes per-device commands, applies timeouts, parses output, returns typed results/errors. Starts adb server at boot (`HOME=/data/home`, D5). |
| mDNS browser | Runs the D3 query when `GET /api/devices/discovered` is called (the Devices page calls it every 10 s while open) and once before each reconnect; services `_adb-tls-pairing._tcp` and `_adb-tls-connect._tcp` (service names: RT §5 [B]). Keeps an in-memory map `instance name → ip:port, last_seen`. Instance name of code-paired phones is `adb-<guid>` (RT §5 [B]); the guid is the stable key used to re-find a phone's new connect port. |
| Device manager | Pair, connect, reconnect, forget; reads device facts (model, ABI list, Android SDK, serial); keeps online state. |
| OTA planner | For one device: resolve each assigned item to an artifact (from arch-build), add dependency apps (from arch-catalog), compare with phone state and install history, output an ordered install list. |
| OTA executor | Runs the install list on one device; records per-package result; emits events. |
| Notifier | Its event subscribers only put the event on an in-memory `queue.Queue`. One notifier thread (started by `notify.start()`, core §3) takes events in order, formats a short text, sends to the configured channel if the event's toggle is on. |

### 3. Interfaces

#### 3.1 Tables (schema file `local/webui/schema/004_device.sql`, owned here)

`devices`
| column | type | note |
|---|---|---|
| id | int pk | |
| name | text | user label, default = model |
| serial | text unique | `getprop ro.serialno`; identity across IP/port changes |
| mdns_name | text null | `adb-<guid>` if seen by discovery at pair/connect time |
| host | text | last IP |
| connect_port | int | last connect port |
| model, abi_list, sdk, android_release | text, text, int, text | from `getprop ro.product.model`, `ro.product.cpu.abilist`, `ro.build.version.sdk`, `ro.build.version.release` |
| ota_after_build | bool | install automatically when an assigned item's build succeeds |
| last_seen_at | timestamp | |

`device_assignments` — (device_id → devices ON DELETE CASCADE, item_id → item ON DELETE CASCADE) pk; item_id = arch-catalog's (source × app) item. Order of install follows the item order in the catalog. Two assigned items with the same package on one device are rejected (400): the second install would always hit `SIGNATURE_MISMATCH` (Morphe stable/beta, Morphe vs Xposed items, xposed §7).

Dependency links: catalog's `item_dependency` (catalog §3), read only here. Scheduled OTA: core `schedules` rows of kind `ota`, target `{"device_ids":[…]}` or `{"all":true}` (expanded by `devices.resolve_devices`, core §4).

`ota_jobs` — job_id pk → core `jobs(id)` ON DELETE CASCADE, device_id (plain column, no FK, §0.3 delete rules), item_ids (JSON, copied from the job target; null = all assignments), mode (copied from `jobs.options.mode`: `normal` / `force_reinstall` / `downgrade`), outcome (`done` / `partial` / `failed` / `skipped_offline`, null while running). Status, trigger and times live in core `jobs`: outcome `done`/`partial` → core status `success`, `failed`/`skipped_offline` → `failed`; the outcome is repeated in `jobs.reason`.

`ota_installs` — id, job_id → core `jobs(id)` ON DELETE CASCADE, device_id (plain column, no FK), kind (`item` / `project`), ref_id (item_id, or project id), build_job_id (null for project), upstream_tag (null for item), package, version_name, version_code, result (`installed` / `up_to_date` / `failed`), error_code (§5), error_text (last 2 KB of adb output). History and D7 lookup ("last successful install of item X on device Y") read this table. Schema after migration 005: `docs/superpowers/specs/2026-10-23-follow-project-design.md` §3.1.

Notification channel: core settings key `notify` = `{type, enabled, config, events}`; config: telegram `{bot_token, chat_id}`, discord `{webhook_url}`, ntfy `{server_url, topic, token?}`; events = JSON array of event names from §3.3.

Secrets (`bot_token`, `webhook_url`, ntfy `token`) are write-only through the API: GET returns them masked (`****` + last 4 chars).

#### 3.2 API routes (JSON, behind arch-core's admin session)

| route | purpose |
|---|---|
| `GET /api/devices` | devices + online state + `assignments:[{item_id, installed:{version_name, build_job_id, at}|null, available:{version_name, build_job_id}|null, pending}]` + `projects:[DP]` (`docs/superpowers/specs/2026-10-23-follow-project-design.md` §6); computed from `ota_installs`, `latest_artifact` and `projects.project_info` without calling adb (`pending` = D7/D8 minus the on-phone presence check) |
| `GET /api/devices/discovered` | current mDNS map: pairing services (not yet paired) and connect services (with matched device id if known) |
| `POST /api/devices/pair` `{host, pair_port, code, connect_port?}` | `adb pair host:pair_port code`; then connect (connect_port from body, or from mDNS by same host); then read facts; upsert device by serial |
| `POST /api/devices/{id}/connect` `{host?, connect_port?}` | reconnect; body optional (Mac: user types the new port shown on the phone) |
| `PATCH /api/devices/{id}` | name, ota_after_build |
| `PUT /api/devices/{id}/assignments` `{item_ids:[]}` | replace assignment set; 400 when two items share a package (§3.1) |
| `DELETE /api/devices/{id}` | `adb disconnect host:port`, delete row (assignments CASCADE; OTA history keeps `device_id`, §0.3). A queued OTA job for the device then fails with reason `device removed`. Cannot revoke trust on the phone; UI tells user to tap "Forget" in Wireless debugging. |
| `POST /api/devices/{id}/ota` `{item_ids?}` | one-click OTA (all assignments, or the listed ones — e.g. the per-item install button) → core job id (`kind='ota'`, target `{"device_id":N}` or `{"device_id":N,"item_ids":[…]}`, options `{}` = mode `normal`) |
| `POST /api/ota/{job_id}/force-reinstall` `{install_id}` | after D6 confirmation: reads the `ota_installs` row (must belong to `job_id`), enqueues an OTA job with target `{"device_id":<row device>}` and options `{"mode":"force_reinstall","package":<row package>}`; the handler runs `adb uninstall <pkg>` then installs only that package |
| `POST /api/ota/{job_id}/downgrade` `{install_id}` | same lookup; options `{"mode":"downgrade","package":<row package>}`; the handler installs only that package with `install -r -d` |
| (handler rule) | for mode ≠ `normal`, the handler maps `options.package` to the device's assigned item with that package (unique per device, §3.1), else to the dependency app with that package; neither → job `failed`, reason `package not assigned` |
| history / live progress | core `GET /api/jobs?kind=ota&device_id=`, `GET /api/jobs/{id}` (`detail` = `ota_jobs` + `ota_installs`), `GET /api/jobs/{id}/log` |
| `GET /api/notify`, `PUT /api/notify` | read (secrets masked) / write the channel |
| `POST /api/notify/test` | sends a test message, returns provider HTTP status/body on failure; stores `notify.last_test {at, ok, detail}` (shown on Settings) |

#### 3.3 Events

Consumed (core event bus): `build.succeeded {item_id, job_id, ...}`, `build.failed {item_id, job_id, fail_code, fail_detail}`, `upstream.new_version {upstream_id, tag, item_ids, job_id}`. Scheduled OTA arrives as a core job, not an event.
Emitted: `ota.done {job_id, device_id, outcome}` (outcome `done`/`partial`), `ota.failed {job_id, device_id, reason}`, `device.offline {job_id, device_id}` (only when a scheduled/after-build job is skipped).
Notifier event names = these names; default toggles for a new channel: all except `device.offline`.

#### 3.4 Consumed from other parts

- arch-build (agreed): `artifacts` rows `{item_id, job_id (= build_job_id here), path, package, version_name, version_code, abi (arm64-v8a|armeabi-v7a|x86|x86_64|universal), signer_sha256, size, created_at}`; package/version read from the patched APK. `latest_artifact(item_id, abi)` = newest succeeded job, exact ABI else `universal`. Retention never deletes the newest succeeded job's files and only deletes files older than the newest N jobs. No pin: the executor resolves the artifact immediately before each install (§4.3 step 4); a missing file at that moment re-resolves once.
- followed projects: `projects.project_info(project_id, abis)` gives downloaded tag, picked APK path and package (`docs/superpowers/specs/2026-10-23-follow-project-design.md` §4.2).
- core: scheduler (schedules kind `ota`), job queue (`jobs.register('ota', handler, detail)`), event bus, log streaming, DB, `HOME=/data/home`, network mode per platform, `base_url` setting for links.

### 4. Data flow

#### 4.1 Pairing
1. Phone: Developer options → Wireless debugging → "Pair device with pairing code" shows IP, pair port, 6-digit code (RT §5).
2. Linux: the pairing service appears in `GET /api/devices/discovered`; UI pre-fills IP and pair port; user types the code. Mac: user types IP, pair port and code.
3. `adb pair ip:pair_port code` (syntax: developer.android.com/tools/adb). Pair port ≠ connect port (RT §5 [B]).
4. Connect port: from mDNS `_adb-tls-connect._tcp` with the same IP (Linux), else the form asks for the "IP address & Port" shown on the main Wireless debugging screen (Mac). `adb connect ip:connect_port`.
5. `adb -s ip:port shell getprop ...` → facts → upsert `devices` by serial (re-pairing an existing phone updates the row, keeps assignments and history).

QR pairing is not offered: no official QR format (RT §5); code pairing covers both platforms.

#### 4.2 Reconnect (before every OTA job and on "Connect")
1. `adb devices` shows `host:port device` → online.
2. Else `adb connect host:connect_port` (last known).
3. Else, if `mdns_name` is in the mDNS map → `adb connect` to the new ip:port; save it.
4. Else offline: manual job → fail with `DEVICE_OFFLINE` and the UI asks for a new port; scheduled/after-build job → repeat steps 1–3 `options.retries` times (core §6, default 0) 60 s apart (`cancel_event.wait(60)`), then `skipped_offline`.

The connect port is random per enable of Wireless debugging (RT §5, inference). Linux recovers via step 3; Mac needs the user to type the new port. This is a stated limitation (§8).

#### 4.3 OTA job (manual, scheduled, or after build)
1. Queue: core's single OTA worker runs OTA jobs one at a time, oldest first; a trigger whose target and normalized options equal an already queued job returns that job (core dedup, core §5). The OTA worker is separate from the build worker, so installs never wait behind builds. `ponytail: one OTA at a time; per-device workers if several phones make OTA slow.`
2. Reconnect (§4.2).
3. Plan: for each assigned item in catalog order → artifact = newest succeeded build's APK whose `abi` is the phone's first ABI in `abi_list`, else `universal`, else error `NO_MATCHING_ARTIFACT`. Skip if D7 says up to date. For each item to install, prepend its dependencies that D8 says need install (presence: `adb shell pm list packages <pkg>`), each dependency once per job.
4. Execute in order; for an item, call `latest_artifact` again right before the install (the plan's choice may be retention-deleted or superseded during a long job), then `adb -s <serial> install -r <path>` (`-r` keep data: developer.android.com/tools/adb). Timeout 15 min per APK (100–230 MB over Wi-Fi, RT §1). Stream adb output as progress.
5. If a dependency fails, the items needing it are marked failed `DEPENDENCY_FAILED` and not attempted. Other items continue. Job status: all ok → `done`; some failed → `partial`; none ok → `failed`.
6. Emit `ota.done`/`ota.failed`.

`after_build`: on `build.succeeded(item_id)`, every device with `ota_after_build` and that item assigned gets a job limited to that item.

#### 4.4 Notification
Event → notifier subscriber → `queue.Queue.put` (returns at once, in the emitting worker or request thread) → notifier thread → if the channel is enabled, subscribed to the event, and the event's job (payload `job_id`, every event kind incl. `upstream.new_version`) does not carry `options.notify = false` (core §6; `job_id` null = sent): one HTTPS POST (§6), timeout 10 s, 3 attempts with backoff (honor Telegram `parameters.retry_after` and Discord 429). Failure is logged. Because sending happens only on the notifier thread, a slow or down provider never holds the build or OTA worker or the synchronous `POST /api/upstreams/check` request. Under the CLI start-up (no `start()`, core §3) events queue and are never sent; CI has no channel configured. The queue is in memory: events still queued at a server stop are lost.

### 5. Error handling

| error_code | detection | user-facing action |
|---|---|---|
| `DEVICE_OFFLINE` | reconnect §4.2 fails | "Turn on Wireless debugging; enter the new port" (Mac) / wait for discovery (Linux) |
| `UNAUTHORIZED` | `adb devices` state `unauthorized`, or connect refused after pairing was revoked on phone | re-pair |
| `PAIR_FAILED` | `adb pair` output does not contain `Successfully paired` (Ubuntu's adb 34.0.4 exits 0 on failure: verified 2026-10-19, `adb pair 127.0.0.1:1 000000` → `Failed: Unable to start pairing client.`, exit 0) | wrong code / expired pairing dialog; retry |
| `SIGNATURE_MISMATCH` | output contains `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | D6 confirm uninstall + install |
| `VERSION_DOWNGRADE` | `INSTALL_FAILED_VERSION_DOWNGRADE` (e.g. user pinned an older version) | button "install with downgrade" → `install -r -d` (`-d` allow downgrade) |
| `INSUFFICIENT_STORAGE` | `INSTALL_FAILED_INSUFFICIENT_STORAGE` | show text |
| `NO_MATCHING_ABI` | `INSTALL_FAILED_NO_MATCHING_ABIS`, or a followed project's release has no APK for the phone's ABIs and no universal (follow-project spec §5.4) | show phone ABI vs built ABIs |
| `NO_MATCHING_ARTIFACT` | planner: no succeeded build has an artifact for the phone's first ABI and none is `universal` (§4.3 step 3) | show phone ABI vs built ABIs |
| `NOT_DOWNLOADED` | item: the artifact file is gone on both re-resolves before install (retention); project: still no downloaded tag after the pull, its file is gone, or the pull was cancelled | rebuild or pull again; the next OTA retries |
| `SIGNER_CHANGED` | followed project has `signer_alert` set (follow-project spec §5.3) | review the signer on the Projects page |
| `USER_REJECTED` | `INSTALL_FAILED_USER_RESTRICTED` / `INSTALL_FAILED_ABORTED` (OEM "install via USB" setting or on-phone prompt) | show text: allow install via adb in phone settings |
| `TIMEOUT` | install exceeds 15 min | kill that adb invocation, mark failed |
| `DEPENDENCY_FAILED` | dependency install failed | see dependency's error |
| `ADB_ERROR` | any other non-zero exit / `Failure [...]` | show last 2 KB of output |

adb server crash: Adb runner runs `adb start-server` and retries the command once.
The `INSTALL_FAILED_*` strings are Android's `pm` result names; their exact presence in output is verified in test T4 (not in fetched official docs, RT §7).

### 6. Notification wire formats (verified 2026-10-19)

| type | request | source |
|---|---|---|
| telegram | `POST https://api.telegram.org/bot<token>/sendMessage` JSON `{chat_id, text}`; text 1–4096 chars; error JSON `{ok:false, description, parameters.retry_after}` | core.telegram.org/bots/api |
| discord | `POST <webhook_url>` JSON `{content}`; content ≤ 2000 chars; success `204` | docs.discord.com/developers/resources/webhook |
| ntfy | `POST <server_url>/<topic>` body = text; headers `Title`, `Priority` (1–5), `Tags`; optional `Authorization: Bearer <token>`; message ≤ 4096 bytes | docs.ntfy.sh/publish |

Message text is truncated to the smallest limit (2000 chars) with a link back to the web UI page (job URL built from core setting `base_url`; empty = no link).

### 7. Testing

| id | test | kind |
|---|---|---|
| T1 | Adb runner parsers: `adb devices` states, `getprop` facts, `dumpsys package` versionCode, `INSTALL_FAILED_*` → error_code, using recorded output fixtures | unit |
| T2 | OTA planner: ABI selection, D7/D8 build-job and tag rules, dependency ordering/dedup, dependency failure propagation | unit, fake artifact/catalog inputs |
| T3 | Notifier: each channel against a local HTTP stub (request shape, truncation, retry_after); a stub that sleeps 30 s does not delay `events.emit` (returns in < 100 ms) | unit |
| T4 | Real phone, Linux host (user-run): pair via discovered service, OTA one app with MicroG, reboot phone → reconnect via mDNS, signature mismatch flow with a store-installed app | manual e2e checklist |
| T5 | Real phone, Mac Docker Desktop (user-run): manual pair + connect, OTA one app, toggle Wireless debugging → offline error → enter new port → OTA ok | manual e2e checklist |
| T6 | Boot check: inside the container `adb version` ≥ 34 and `adb pair 127.0.0.1:1 000000` prints `Failed: Unable to start pairing client.` (not "unknown command") | container smoke |
| T8 | mDNS parser: recorded DNS-SD reply packets (PTR+SRV+A, with name compression) → `instance → ip:port` map | unit |
| T7 | Key persistence: recreate container, phone stays connectable without re-pair | manual, part of T4 |

### 8. Known limitations

- Mac: no discovery; after the phone toggles Wireless debugging or reboots, the user types the new connect port.
- Scheduled OTA only works while the phone has Wireless debugging on and is on the same network; otherwise the job is `skipped_offline` and retried at the next schedule (no "install when it comes online" in v1).
- Removing a device does not revoke trust on the phone (user taps Forget).
- Signature identity: artifacts signed with different keystores (`morphe.keystore` vs `fiorenmas.ks`, RT §2) for the same package will hit `SIGNATURE_MISMATCH` when switching source; this is the expected D6 flow.

### 9. Open questions for the user

None. Uninstall-on-signature-mismatch is manual-only with a data-loss warning.


# Part 5 — UI: mockup ↔ spec mapping and frontend structure

Owner: integrator. Input: `docs/superpowers/mockups/final/` (user-chosen "Workbench" series, 7 screens + index).
Rule: every mockup element maps to a spec section, or it is added to the spec minimally ("add", the section named is where it now lives), or it is dropped with a reason ("drop"). "client" = pure browser logic over data an existing route already returns; no server change.
Section references: core/catalog/build/device §N = Part 1–4 of `2026-10-19-webui-design.md`.

### 1. Frontend structure

Served by core `server.py` from `local/webui/static/` (stdlib Python, no npm, no bundler; core §2).

| file | made from (mockup) | content |
|---|---|---|
| `static/app.css` | the first `<style>` block, identical in 6 of the 8 mockup files (`apps.html`, deleted) | tokens (light/dark), components (Card, Button, Badge, Switch, Tabs, Dialog, dropdown, Toast, OTP input), shell, responsive drawer. The two variants (`login.html` adds `.auth`, `schedules.html` adds one media query) move into those pages' own `<style>`. |
| `static/app.js` (ES module) | theme, toast, drawer, dropdown close and tabs roving focus from the first `<script>` of the deleted mockup `apps.html` | plus: `api(method, path, body)` (JSON fetch, `Content-Type: application/json` on mutations, 401 → `location = '/login.html'`, `{error}` → toast); `logStream(jobId, onText, onEnd)` (`EventSource` on `/api/jobs/{id}/log`); `esc()` HTML-escape; `fmt.rel/abs/bytes/duration`; `shell()` fills the sidebar status (§2 row "Sidebar") and sets `aria-current` from `location.pathname`. |
| `static/<page>.html` | each mockup page body; the sidebar/top-bar markup is copied into every page as in the mockup (static HTML, no templating) | page markup with sample rows removed; page CSS stays in the page's second `<style>`. |
| `static/<page>.js` (ES module) | the page's second `<script>` | the mockup interaction logic kept (filters, drag-reorder, tabs, save bar, log follow/wrap/find) but fed by `api()` instead of sample DOM. |

Pages and URLs: `/` → `index.html` (App list, from `apps.html`), `/item.html?id=N` (`app-detail.html`), `/builds.html[?job=N][&item=N]` (`build-log.html`), `/schedules.html`, `/devices.html`, `/settings.html`, `/login.html`. Assets under `/static/`. `index.html` of the mockup is a design index, not a page (drop, §2 row "Index").

Rendering rules: lists are rebuilt from JSON with template strings; every server string (app names, patch names, option values, fail details) goes through `esc()`; log text is appended with `textContent` only. The App list and Builds queue refresh every 5 s while visible (`document.visibilityState`); the Devices page calls `GET /api/devices/discovered` every 10 s (device §2). One owner per page in the plan: `<page>.html` + `<page>.js`; `app.css`/`app.js` have one shared owner.

### 2. Gap table

#### Global (all pages)

| mockup element | spec | decision |
|---|---|---|
| Sidebar nav, sign out (`apps.html`, deleted) | `POST /api/auth/logout` (core §8) | covered |
| Sidebar "Worker busy · 1 running · 2 queued" | `GET /api/jobs?status=running` / `?status=queued` (core §8) | covered, client counts |
| Sidebar "Disk 6.9 / 63 GB · arm64", host name | — | **add** `server` object in `GET /api/settings` (core §8): `{arch, tz, now, network_mode, disk:{total, free, artifacts, apk_cache, upstream, logs, artifacts_count}}`; host name = `location.hostname` (client) |
| Light/dark toggle saved in localStorage | — | client |
| Toasts, drawer < 768 px, skip link, focus ring, reduced motion | — | client (`app.css`/`app.js`) |

#### Login (`login.html`)

| mockup element | spec | decision |
|---|---|---|
| Password field, show/hide, busy state, `aria-invalid` | core §7 | covered / client |
| Username field | first-run account contract (`GET /api/auth/state`, `POST /api/auth/setup`, `POST /api/auth/login`) | **restored**: username + password in both modes; the page reads `setup_required` from `GET /api/auth/state`. Setup mode: heading "Create admin account", extra "Confirm password" field (client check: ≥ 10 chars and equal), button "Create account" → `POST /api/auth/setup`. Login mode: button "Sign in" → `POST /api/auth/login`. After success → `/` |
| "Initial password comes from the server's environment" | first-run account contract | **drop**: no password is printed at boot; the password hint shows only in setup mode (≥ 10 chars) |
| "Wrong password. 4 attempts left before a 5-minute lockout" | core §7 brute-force counter | 401 `{"error":"wrong credentials","attempts_left":N}` → "Wrong username or password. N attempts left before a 5-minute lockout."; 429 `{"error":"locked","retry_after":S}` → "Too many attempts. Try again in M min S s." and the button stays disabled; 400 → server message; 409 → page re-reads the state and switches mode |
| "Keep me signed in (30 days)" checkbox | core §7 `remember` | **restored** (§0.6 R2): checked by default; sends `remember` with the login; unchecked = browser-session cookie, 12 h server limit |
| "Forgot password?" button (login mode only) | first-run account contract (`POST /api/auth/reset`) | opens a confirm dialog; on 202 shows "the new password is in the server log" with the places to look (Portainer > Containers > relume-app > Logs, or `docker logs relume-app`); 429 `{"error":"cooldown","retry_after":S}` → "A reset was just done. Try again in M min S s." and the button stays disabled; 409 → page switches to setup mode |
| Server summary (host, arch, item count, devices) | `GET /api/health` (core §8, no auth) | **restored** (§0.6 R5) with non-sensitive data only: host = `location.host` (client, the address the user typed), "Server up" + app name, version and "up since" from `GET /api/health`; health error → "Server unreachable". Not shown before sign-in: item, app or device names, item/device counts, IPs or network details, arch, tokens, paths. |
| Footer "Relume v1.0" | — | static text in `login.html` |

#### App list (`apps.html`)

| mockup element | spec | decision |
|---|---|---|
| Groups (collapsible), items, icon, label "YouTube · morphe", package, variant ABIs | `GET /api/catalog` (catalog §3, §3.2, §3.3, §6) | **add** the item object field list to catalog §6 (one JSON shape for list and detail) |
| Letter tile when no icon | catalog §3.3 | client (`<img onerror>`) |
| "Built app 20.12.46", "Patches · local → upstream v1.4.0 → v1.5.0" (amber when stale) | — | **add** `last_success` (app/patches version) and `upstream.downloaded_tag/latest_tag` in the item object; item status from new in-process `build.item_status(item_ids)` (build §3.4) |
| Status badge Up to date / Update available / Building 62 % / Queued / Failed #412 · reason · Log / Not built | — | **add** `last_build` (job id, status, reason), `active_job` (id, status, step) via `build.item_status`; progress % = step index / step count from `::step::` markers (build §3.2) |
| Tally cards (all, updates + "checked 04:00", building + queued, failed), status filter, search, empty state, per-group summary | `upstream.checked_at` (catalog §5.1) + item object | client |
| Update / Rebuild / Retry / Build / Log / Download (last good build when current failed) | `POST /api/upstreams/{id}/pull`, `POST /api/items/{id}/build`, `GET /api/items/{id}/artifacts`, `GET /api/artifacts/{id}/download` | covered. Update sends `rebuild:true` (decision Q4, §0.6); the button tooltip says it updates the source for all its items (catalog §5.2). Several variants → Download is a menu of the newest successful job's artifacts. |
| Disabled Update when current, disabled Rebuild when building | item object | client |
| Drag reorder groups and items (mouse, touch, Alt+↑/↓), move between groups, "Order saved" | `PUT /api/groups/order`, `PUT /api/groups/{id}/items/order` (catalog §3.1) | covered; **add** id `0` = Ungrouped for the item-order route (catalog §3.1) so items can be dropped there |
| Group collapsed by default | — | client, open state in localStorage |
| Group menu: Update all / Update all upstream | `POST /api/upstreams/{id}/pull` per distinct upstream of the group | covered, client fans out; both menu entries map to the same action (one entry kept) |
| Group: Rebuild all, select-all checkbox | `POST /api/builds {item_ids}` (build §3.4) | covered |
| Group: OTA group to devices | `POST /api/devices/{id}/ota {item_ids}` per device that has group items assigned (device §3.2) | covered, client fans out |
| Group: Download all (zip) | `GET /api/groups/{id}/download` (build §3.4) | **restored** (§0.6 R1): menu entry is a plain link (`<a href download>`, the session cookie is sent); ZIP streamed with no temp file; menu text notes "newest successful build of each item" |
| Group: Rename / Delete, New group | `PUT/DELETE /api/groups/{id}`, `POST /api/groups` | covered |
| (not in mockup) Group colour | `grp.color`, `POST /api/groups` / `PUT /api/groups/{id}` `color` (catalog §3.1, §0.6 R6) | **add**: native `<input type="color">` in the New group / Rename dialog plus a "No colour" button (sends null); the group header shows a 4 px left border and a dot in that colour; no colour = no border |
| Top bar "Check upstream" | `POST /api/upstreams/check` (catalog §5.4) | covered |
| Top bar "Add app" (pick source, then app) | `POST /api/items`, `POST /api/sources`, `PUT /api/apps/{pkg}` | covered; **add** body fields of `POST /api/items` and `POST /api/sources` to catalog §6. Package is typed (no bundle package list); new items get the seed default variants (decision Q5). |

#### App detail (`app-detail.html`)

| mockup element | spec | decision |
|---|---|---|
| Header: name, source, package, upstream repo + channel, built version, "v1.5.0 available" | — | **add** `GET /api/items/{id}` (catalog §6, same item object + `version`, `variants`, `project_ids`, `upstream.repo/channel`) |
| Header buttons Update upstream / Rebuild / Download | as App list | covered |
| "Installed on" devices with installed version and "(behind)", Manage devices | — | **add** per-assignment state in `GET /api/devices` (device §3.2): `installed`, `available`, `pending`; client filters devices by item |
| Patches tab: switch per patch, description, counts "133/142", live counter, All on / All off / Reset to defaults, show filter, search | `GET/PUT /api/items/{id}/patches` (catalog §4.1) | covered / client. Reset = PUT with empty overrides. |
| "From morphe-patches-1.4.0.mpp" | — | **add** `files` (patches file names) to the §4.1 response |
| Tags default on / default off / included by you / excluded by you | `default_enabled`, `enabled` (catalog §4.1) | client |
| Locked "required" patches (GmsCore support) | — | **deferred to next phase** (§0.6 F2): the bundle has no "required" attribute; in v1 GmsCore support is a default-on patch the user may turn off |
| Pending diff "against `src/options/morphe.json`" | catalog §4 (that file is deleted) | client diff of unsaved vs saved overrides; label "saved settings" |
| Sticky save bar Discard / Save / Save & rebuild | `PUT …/patches`, `PUT …/version`, `POST …/build` | covered, client sends the dirty PUTs then the build |
| Options tab: fieldset per patch, type tag, default, "changed", option jump, "kept but unused" when patch excluded | catalog §4.1 `options[{key,title,type,default,value}]`; overrides keep options of disabled patches | covered / client |
| Enum select (Icon: morphe_original / youtube / custom) | — | **add** `values` (preset values from `list-patches --with-options`, may be empty) to each option (catalog §4.1) |
| Colour picker | catalog §4.1 option `type`, `default`, `value` | **restored** (§0.6 R3), client only: a string option whose value or default matches `^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$` gets an `<input type="color">` next to its text field, both kept in sync; for 8 digits (`#AARRGGBB`) the picker edits the RGB part and keeps the alpha. Server validation stays "string". |
| Format hint "major.minor.patch" | — | **drop**: the bundle gives no format; server validates type only |
| "Options are shared by every item from the morphe source" | catalog §3 rule: each item owns its overrides | **text changed** to "applies to this item only" (spec wins; per-item overrides are a catalog decision) |
| Raw JSON preview | catalog §4 options-file format | client renders `[{"patches": overrides}]` (the mockup's `patchName` format is sample) |
| Version tab: Auto (resolves to X today) / Pin / Latest + warning, allow nearest | `PUT /api/items/{id}/version` (catalog §4.2); auto value = `compatible_versions[0]` | covered |
| Pin list tagged patches recommend / compatible / last built OK | `compatible_versions` + build history `app_version` | covered, client; pin is a text input with a datalist |
| Pin list "not in patch list ⚠" newer versions | `GET /api/items/{id}` `apkmirror {versions, checked_at}` (catalog §6) | **restored** (§0.6 R4): versions newer than `compatible_versions[0]` are added to the datalist marked "not in patch list ⚠"; hint "APKMirror checked <relative time>", or "filled by the next build" when null. The data comes from builds only (build §4 step 5d: list page reused, else ≤1 request per package per 24 h); page loads never scrape APKMirror. Choosing one gets the catalog §4.2 warning. |
| (Xposed items, not in mockup) Patches + Options tabs | xposed §8 | replaced by one **Module** tab (module repo, tag, engine + tag read-only; signature bypass level select, inject-dex checkbox → `PUT /api/items/{id}` `engine_options`); Version tab hides Auto; App list badge "Xposed · LSPatch/NPatch" from `engine` |
| History tab: build #, started, app ver, patches, result + reason, duration, download, keep count, "All builds →", failed row opens log | `GET /api/jobs?kind=build&item_id=` | **add** list rows carry `detail` (core §8); keep count from `artifact_retention` |

#### Builds (`build-log.html`)

| mockup element | spec | decision |
|---|---|---|
| Queue card: running · waiting · 1 worker, queued rows, Remove | `GET /api/jobs?kind=build&status=…`, `POST /api/jobs/{id}/cancel` | covered; **add** "cancelling a queued job marks it `cancelled` without running" (core §5) |
| Trigger "schedule 'Nightly rebuild'" / "patch update" / manual | `jobs.trigger`, `jobs.schedule_id` + `GET /api/schedules` | covered, client. Trigger `upstream` shows "patch update"; the tag in the queued row ("1.10.0") is **dropped** (job input is built at start, build §3.1). |
| Header: started, live elapsed, Cancel, status flips on end | `started_at`, SSE `event: end` (core §8) | covered / client |
| Facts: patches ver, app ver, arch, CLI, APK source | build_jobs versions are written at the end (build §4 step 7) | **add** build handler writes `patches_version`/`cli_version` at start and `app_version` on the runner's `::version::` line (build §3.2, §4); arch = variant ABIs, APK source = `download.kind` |
| Step tracker with per-step time | — | **add** runner marker lines `::step::<name>[::<detail>]` (build §3.2); client derives times from the log's `HH:MM:SS` prefixes |
| Live patch counter "Patch 4 / 41" | — | **drop** per-patch count: depends on the CLI's per-patch line format, which is unverified (§0.7); the `patch` step shows variant `i/n` instead |
| Log: follow (auto-off on scroll up), wrap, find + count, line numbers, line count, Copy | SSE log | client |
| Download .log | — | client: `Blob` of the received text (a finished job's SSE sends the whole file); no route |
| History table: #, item, trigger, result + failure reason, started, duration, artifact size, View log; filters All / Failed / Success / This app; Load older | `GET /api/jobs?kind=build&status=&item_id=&before_id=` + list `detail` | covered after the core §8 list-`detail` add |
| Suggested fix on failed rows ("Pin version…", "Retry") | `fail_code` (build §5) | client map: `version_unresolved`/`patch_failed` → link to the Version tab, `upstream_missing` → Update upstream, others → Retry. The specific version in "Pin 511.0.0.47.67 instead" is **dropped** (sample). |

#### Schedules (`schedules.html`)

| mockup element | spec | decision |
|---|---|---|
| List: name, kind label, enable switch, Run now, paused dimmed | `GET/PUT /api/schedules`, `POST /api/schedules/{id}/run` (core §6, §8) | covered |
| Server time and timezone | — | **add** `server.tz`, `server.now` (core §8 settings `server`) |
| Next run absolute + relative, 24 h timeline, "Next runs" preview | `next_run_at`; `POST /api/schedules/preview` returned 3 times | **add** preview returns 5 times, and each `GET /api/schedules` row carries `next_runs` (5) (core §8) |
| Last run result "4 built · 1 failed" | — | **add** `last_counts` per schedule = job count by status for jobs of that schedule created at/after `last_run_at` (core §8); text is "N success · N failed" |
| Editor Repeat: Hourly (every 1/3/6/12 h) / Daily at / Weekly days at | core §6 spec forms `hours` / `day` / `week` | covered (UI Sun..Sat maps to 0 = Monday) |
| Editor Repeat: Cron | core §6 "No cron strings" | **deferred to next phase** (§0.6 F1): in v1 the three forms cover the need without a parser |
| Applies to: several sources / several groups / Pick items / several devices / All | core §4 targets allowed one `group_id` | **add** list targets `{"source_ids":[…]}` (upstream), `{"group_ids":[…]}`, `{"item_ids":[…]}` (build), `{"device_ids":[…]}` (ota), `{"all":true}` (core §4) |
| Check: "Also pull new patches" (check vs check + pull) | upstream schedule always pulled | **add** `options.pull` (default true; false = check only) (core §6); options now reach the job (core `jobs.options`, below) |
| Rebuild after pull | `options.rebuild` (default true, decision Q4) | covered |
| Rebuild: "Only items whose patches or app version changed" vs "Rebuild everything" | — | **add** build `options.only_changed` (default false) using `build.needs_build(item_id)` = never built, last build failed, or source/CLI `downloaded_tag` differs from the last success (build §3.4). App-version change is not detected (needs APKMirror). |
| OTA: Skip offline devices, retry N× | device §4.2 step 4 (`skipped_offline`) | **add** ota `options.retries` (0–5, default 0): reconnect attempts 60 s apart before `skipped_offline` (device §4.2) |
| OTA: Skip devices with nothing new | device D7 planner | covered by behaviour: a device with nothing pending installs nothing; no option |
| Notify on result | device §4.4 global toggles | **add** `options.notify` (default true): the notifier skips events of jobs whose schedule has `notify:false` (device §4.4) |
| "Order of a night: … rebuild waits for a running check; OTA waits for running builds" | core §5: one worker per kind, kinds run in parallel | **text changed**: order is set by the schedule times; a pull with rebuild enqueues builds after its download; phones with "install after build" get OTA from `build.succeeded`. "A missed run runs once at startup" is kept (core §6). |
| Delete / Run now / Cancel / Save, New schedule resets editor, click loads editor | core §8 | covered / client |

#### Devices (`devices.html`)

| mockup element | spec | decision |
|---|---|---|
| Host line "Linux host · host network · mDNS discovery on" | — | **add** `server.network_mode` (core §8); text only, discovery runs the same in both modes (device D4) |
| "Preview host: Linux / macOS" toggle | — | **drop**: mockup demo control; the discovered card is always shown and an empty result shows the "enter IP and port" note (device D4) |
| Paired devices count, name, rename, IP:port, ABI, last seen, online/offline | `GET /api/devices`, `PATCH /api/devices/{id}` (device §3.1–3.2) | covered |
| Android version ("Android 15") | `devices.sdk` | **add** column `android_release` (`getprop ro.build.version.release`) (device §3.1) |
| Online latency "4 ms" | — | **deferred to next phase** (§0.6 F3): adb has no cheap ping |
| Assigned apps editor | `PUT /api/devices/{id}/assignments` | covered |
| Assigned app installed → available, ✓ when current, "OTA now · 2 pending" | — | **add** per-assignment `{item_id, installed:{version_name, build_job_id, at}|null, available:{version_name, build_job_id}|null, pending}` in `GET /api/devices`, from `ota_installs` and `latest_artifact` without calling adb (device §3.2) |
| Dependency projects: installed / available tag, required by, pending | device D8, `item_dependency`, `device_projects` | per-device `projects: [DP]` in `GET /api/devices` (`docs/superpowers/specs/2026-10-23-follow-project-design.md` §6). |
| Last OTA result on the card | `GET /api/jobs?kind=ota&device_id=&limit=1` + list `detail` | covered |
| Offline guidance, disabled OTA reason | device §5 `DEVICE_OFFLINE` | client text |
| Unpair, Reconnect, OTA now, OTA all online | `DELETE /api/devices/{id}`, `POST …/connect`, `POST …/ota` | covered; OTA all = client loop over online devices |
| Discovered list: IP, pairing port, "Pairing mode" / "Paired", last seen, Scanning | `GET /api/devices/discovered` (device §2, §3.2) | covered |
| Discovered row model name and Android version ("Pixel 7a · Android 15") | mDNS gives instance name and address only | **drop**: shown as instance name + IP:port |
| Pair dialog, 6-box code (auto-advance, paste) | `POST /api/devices/pair` | covered / client |
| Manual pairing: IP, pairing port, code, connect port | `POST /api/devices/pair {host, pair_port, code, connect_port}` | covered |
| OTA history: filter by device, time, trigger, installed list, n / n, "already current, skipped", "Skipped · offline" | `GET /api/jobs?kind=ota&device_id=` + list `detail` (`ota_jobs`, `ota_installs`) | covered after core §8 list-`detail` add |
| "Uninstall & retry" on signature mismatch | `POST /api/ota/{job_id}/force-reinstall` (device D6) | covered, confirm dialog with the data-loss warning |
| (not in mockup) "Install with downgrade" on `VERSION_DOWNGRADE` rows | `POST /api/ota/{job_id}/downgrade` | kept; same row-action slot as Uninstall & retry |

#### Settings (`settings.html`)

| mockup element | spec | decision |
|---|---|---|
| One-of-three channel cards with their fields, ntfy optional token | `GET/PUT /api/notify` (device §3.1) | covered |
| "Stored encrypted" | device §3.1: secrets are write-only, masked in GET | **text changed** to "write-only, shown masked"; no encryption (the key would sit next to the DB) |
| Notify on: new upstream, build succeeded, build failed, OTA finished | `notify.events` (device §3.3) | covered: "OTA finished" = `ota.done` + `ota.failed`; a fifth chip "Device offline" = `device.offline` (default off) |
| Send test, "Last test: today 14:02 · delivered" | `POST /api/notify/test` | **add** `notify.last_test {at, ok, detail}` stored by the test route (device §3.2) |
| Keep per item, live estimate "≈ 55 APKs (~5.1 GB)" | `artifact_retention` (build §3.3) | covered; estimate is client = items × variants × keep, size from `server.disk.artifacts / artifacts_count` |
| "Clean up now" | build §6 retention runs after each build | **add** `PUT /api/settings` that changes `artifact_retention` runs retention for all items at once (build §6); the button is **dropped** (saving cleans) |
| Disk breakdown APKs / download cache / logs / free | — | **add** `server.disk` (core §8, Global row) |
| GitHub token: state, masked token, file path, expiry, rate-limit gauge + reset, 60/h hint, Re-check, scopes | `GET /api/settings` token status (core §8) | **add** `github {state, limit, remaining, reset_at, token_hint ("****" + last 4), path, expires_at, scopes}`; `expires_at`/`scopes` from the `github-authentication-token-expiration` / `X-OAuth-Scopes` response headers when present, else null; Re-check = `GET /api/settings?recheck=1` (bypasses the 5-min cache) |
| Change password: current, new, confirm, strength meter, mismatch error, "other sessions signed out" | `POST /api/auth/password` (core §7, §8) | covered; **add** response `{"signed_out":N}`; minimum length is core's 10 (mockup "12" is sample); meter and mismatch are client |
| Appearance toggle | — | client |

#### Index (`index.html`)

| mockup element | spec | decision |
|---|---|---|
| Series lede, palette swatches, screen cards | — | **drop**: design-review index, not a product page; `/` is the App list |

### 3. Spec features without a mockup element

| spec feature | UI placement |
|---|---|
| `PUT /api/apps/{pkg}` (display name, APKMirror/APKPure URLs), `POST /api/apps/{pkg}/icon/refresh` | App detail header menu "Edit app…" (dialog) |
| `PUT /api/items/{id}` enabled, display name, group, variants, dependency projects (`project_ids`, list from `GET /api/projects`) | App detail header menu "Item settings…" (dialog); variants as ABI chips (universal, arm64-v8a, armeabi-v7a, x86, x86_64) |
| `DELETE /api/items/{id}`, `POST/PUT/DELETE /api/sources/{id}` | item settings dialog; Add app dialog → "New source…" sub-form (id, name, engine Morphe/LSPatch/NPatch, repo, channel/tag, asset regex, keystore, CLI upstream; xposed §8) |
| `PATCH /api/devices/{id}` `ota_after_build` | switch "Install after build" on the device card |
| Global "Update CLI" (catalog §5.2) | Settings, GitHub token card: button "Update CLI" = pull of the CLI upstream |
| `job_history_count`, `base_url` | Settings, Artifacts card: two number/text fields |
| Catalog `check_error` / token rejected banner (catalog §9) | App list: amber banner under the tallies when any upstream has `check_error` |


# Part 7 — Xposed module items (LSPatch / NPatch)

Owner: arch-xposed.
Decision basis: contract "Decisions" Q2 = (b) keep Xposed/LSPatch apps as a second item type in v1 (`docs/logs/2026-10-19-webui-contract.md`).
References "catalog §N", "build §N", "device §N", "core §N" mean sections of the merged spec.

### 1. What `Xposed.sh` does today (verified in code)

| Fact | Evidence |
|---|---|
| Entry: `bash src/build/Xposed.sh <1..5>`, only from the GitHub workflow "3. Another Patch" (matrix 1–5). Not in the local build table. | `src/build/Xposed.sh`, `.github/workflows/ci_.yml` job `Patch-Xposed` matrix and `Patch apk` step, `local/build.sh` `ORGS` table |
| Engines: LSPatch from `JingMatrix/LSPatch` latest (`dl_gh … latest` downloads every asset; the jar is picked by glob `lspatch-*-release.jar`). NPatch from `7723mod/NPatch` pinned `v1.0.5` (glob `jar*.jar`). `NPatch_dl` (latest) is defined but never called. | `Xposed.sh`, `utils.sh` `lspatch`, `npatch` |
| Modules (Xposed module APKs embedded into the app): `revenge-mod/revenge-xposed` latest, file `app-release.apk`; `gnadgnaoh/NexAlloy` pinned `v1.1`, glob `NexAlloy*.apk`, first match wins. | `Xposed.sh`, `utils.sh` `lspatch` |
| Apps (8 items, one module each): Discord (Revenge, LSPatch), Facebook, Messenger, Instagram, Threads, Zalo (NexAlloy, LSPatch), TikTok, TikTok Asia (NexAlloy, NPatch). | `Xposed.sh` |
| Base APK download: the same `get_apk` (APKMirror) and `get_apkpure` (Zalo) as Morphe items; `bundle` is merged to one APK by APKEditor before patching. | `Xposed.sh`, `utils.sh` `get_apk`, `get_apkpure` |
| Version: Facebook pinned `580.0.0.51.74`; all others newest (no `.mpp` in the work dir, so `detect_version` returns empty and `get_apk` takes the newest non-beta). `lspatch` unsets `version`, so Messenger is not pinned. | `Xposed.sh`, `utils.sh` `detect_version`, `get_apk`, `lspatch` |
| Engine flags: Discord none; Facebook, Messenger, Instagram, Threads, Zalo `--injectdex --sigbypasslv 3`; TikTok, TikTok Asia `--sigbypasslv 3`. | `Xposed.sh` |
| Signing: LSPatch `-k src/keystore/lspatch-signing.p12 morphe fiorenmas morphe`; NPatch `-k src/keystore/fiorenmas.ks fiorenmas morphe fiorenmas` (path, store password, alias, alias password). Morphe items use `morphe.keystore`. | `utils.sh` `lspatch`, `npatch`, `patch` |
| Output: engine writes `<input base>-<n>-lspatched.apk` / `-npatched.apk` into `-o`; the script renames it to `release/<key>-<module owner>.apk`. | `utils.sh` `lspatch`, `npatch` |
| Telegram: `telegram_dl` (tdl client) needs secret `TDL_BACKUP` = base64 backup of a logged-in Telegram account session. **It has no caller anywhere** (grep of the repo: only its definition). Workflows pass `TDL_BACKUP` to `Piko.sh`/`Piko-Beta.sh`, which do not use it. | `utils.sh` `telegram_dl`, `.github/workflows/manual-patch.yml` `workflow_call` secrets and the `patch_*` job envs |

Third-party CLI facts (checked 2026-10-19 against source at the release tags, via `gh api`):
- LSPatch `v1.2` (latest, `lspatch-v1.2-487-release.jar`): `-o` output dir, `-f` force, `-l/--sigbypasslv` 0–3 (default 0), `--injectdex`, `-k` arity 4, `-m/--embed` list, `--manager` "cannot work with embedding modules" (`patch/src/main/java/org/lsposed/patch/LSPatch.java:35-66`). Output name `%s-%d-lspatched.apk` (`patch/.../ApkPatcher.java:138-143`). Modules are stored inside the APK at `assets/lspatch/modules/<pkg>.apk` (`ApkPatcher.java:394`, `share/.../Constants.java:9`).
- NPatch `v1.0.5` (pinned, `jar-v1.0.5-639-release.jar`): same `-o -f -k -m --injectdex`, `-l` 0–4 (default **1**), output `%s-%d-npatched.apk` (`patch/src/main/java/top/nkbe/patch/NPatch.java:77-125,206-209`). Latest NPatch is `v1.0.9`; there levels 3–4 "require --manager" (`NPatch.java:101` at HEAD), which conflicts with embedding — one reason the seed keeps the `v1.0.5` pin.
- Release assets now: `revenge-xposed` `1801` = `app-release.apk` + `app-debug.apk`; `NexAlloy` `v1.1` = `NexAlloy-refactor-release-v1.1.apk` (one asset); `NexAlloy` latest is `v1.0` (published after v1.1) with two assets `-nonroot-` and `-root-`.

### 2. Design summary

An Xposed item is still **(source × app)**. The source is "a module applied with an engine" (e.g. `nexalloy-lspatch`); its upstream is the module's GitHub release instead of a patches bundle. The engine (LSPatch or NPatch) takes the role the Morphe CLI has today. The same runner, job queue, artifacts, retention, OTA and notifications are used. No new table, route or job kind.

Additions (all small):
1. `source.engine` (`'morphe'|'lspatch'|'npatch'`, default `'morphe'`) — the item type is derived from it.
2. `source.cli_upstream_id → upstream(kind='cli')` — replaces "one global CLI upstream row". Morphe sources all point to the Morphe CLI row.
3. `upstream.kind` gets value `'module'`.
4. `item.engine_options` (JSON, null for Morphe items) = `{"sigbypass": 0..4, "injectdex": bool}`.
5. `job.json` gets `engine` and `engine_options`; the runner's `patch_variant` gets an engine switch.

Telegram is **not** a module source in v1: nothing uses it today, and it needs a logged-in Telegram account session secret. `telegram_dl` is deleted as build §7 already states. Module sources are GitHub releases only.

### 3. Components

| Component | Change |
|---|---|
| Catalog store (catalog §2) | Holds `source.engine`, `source.cli_upstream_id`, `item.engine_options`; validates Xposed rules (§5). |
| Release tracker (catalog §5.1–5.2) | Unchanged logic; also checks/pulls `kind='module'` rows and the LSPatch/NPatch `kind='cli'` rows. Skips the inspect step for `module` (it already runs for `patches` only). New check: a `module` row must match exactly one asset. |
| Bundle inspector | Not used for Xposed items. |
| Job input writer (catalog §5.3) | Fills `engine`, `engine_options`; `patches_files` = module APK path(s); `cli_jar` = engine jar; `options_file_json` = null. |
| Build runner `build-item.sh` (build §2) | `patch_variant` dispatches on `engine`; keystore credentials looked up by keystore file name. |
| Device / OTA | No change in code. Modules are inside the APK; no manager app on the phone. |

### 4. Data model changes (catalog `003_catalog.sql`)

```
upstream  kind ('patches'|'cli'|'project'|'module')                          -- + 'module'; 'project' since 005_follow.sql
source    + engine TEXT NOT NULL DEFAULT 'morphe' CHECK (engine IN ('morphe','lspatch','npatch'))
          + cli_upstream_id → upstream (kind='cli')                         -- per source, was one global row
          keystore: unchanged column; seed 'lspatch-signing.p12' / 'fiorenmas.ks' for Xposed sources
item      + engine_options TEXT NULL   -- JSON {"sigbypass":int,"injectdex":bool}; null when source.engine='morphe'
```

Rules:
- Item type = `source.engine`: `morphe` → patches item; `lspatch`/`npatch` → Xposed item. No `item.kind` column (it would duplicate the source).
- Same module with two engines = two sources sharing one module upstream row (allowed by `UNIQUE(repo, channel, tag)`, catalog §3): `nexalloy-lspatch` and `nexalloy-npatch` both point to `gnadgnaoh/NexAlloy` tag `v1.1`.
- `bundle_info`, `item.patch_overrides`: unused for Xposed items (stay `{}`).
- `item_dependency`: allowed (generic), seed none. MicroG is not needed: LSPatch/NPatch keep the original package name and these apps do not use the MicroG GmsCore rename. NPatch's `--useMicroG` is not used today and not modelled.

#### 4.1 Seed (added to `local/webui/catalog-seed.json`)

Upstreams:

| repo | kind | channel / tag | asset_regex |
|---|---|---|---|
| `JingMatrix/LSPatch` | cli | stable | `^lspatch-.*-release\.jar$` |
| `7723mod/NPatch` | cli | tag `v1.0.5` | `^jar-.*-release\.jar$` |
| `revenge-mod/revenge-xposed` | module | stable | `^app-release\.apk$` |
| `gnadgnaoh/NexAlloy` | module | tag `v1.1` | `^NexAlloy-.*\.apk$` |

Sources: `revenge-lspatch` (Revenge, lspatch, `lspatch-signing.p12`), `nexalloy-lspatch` (NexAlloy, lspatch, `lspatch-signing.p12`), `nexalloy-npatch` (NexAlloy, npatch, `fiorenmas.ks`).

Items (one group "Xposed", appended after the Morphe groups; variants per Q5 = one variant each, the ABI the script downloads):

| source | package | key | version | download (kind, type, arch, dpi, minver) | abi | engine_options |
|---|---|---|---|---|---|---|
| revenge-lspatch | com.discord | discord | latest | apkmirror, bundle, –, –, – | universal | sigbypass 0, injectdex false |
| nexalloy-lspatch | com.facebook.katana | facebook-arm64-v8a | pinned 580.0.0.51.74 | apkmirror, bundle, arm64-v8a, 320-640dpi, Android 11+ | arm64-v8a | 3, true |
| nexalloy-lspatch | com.facebook.orca | messenger-arm64-v8a | latest | apkmirror, apk, arm64-v8a, nodpi, Android 9.0+ | arm64-v8a | 3, true |
| nexalloy-lspatch | com.instagram.android | instagram-arm64-v8a | latest | apkmirror, bundle, arm64-v8a, 120-640dpi, Android 9.0+ | arm64-v8a | 3, true |
| nexalloy-lspatch | com.instagram.barcelona | threads-arm64-v8a | latest | apkmirror, bundle, arm64-v8a, 160-640dpi, Android 9.0+ | arm64-v8a | 3, true |
| nexalloy-lspatch | com.zing.zalo | zalo | latest | apkpure, bundle, –, –, – | universal | 3, true |
| nexalloy-npatch | com.zhiliaoapp.musically | tiktok | latest | apkmirror, apk, –, –, – | universal | 3, false |
| nexalloy-npatch | com.ss.android.ugc.trill | tiktok-asian | latest | apkmirror, apk, –, –, – | universal | 3, false |

All values from `Xposed.sh`. Apps rows (`apkmirror_*`, `apkpure_url`) come from `src/build/helper/apps.json` like other items (all 8 packages are present there). Note: preflight-patches §1 says "9 items"; the script has 8.

### 5. Interfaces

#### 5.1 Catalog API (existing routes, extra validation)

| Route | Xposed behaviour |
|---|---|
| `POST/PUT /api/sources` | Body adds `engine` and `cli_upstream_id` (or `cli_repo/channel/tag`, deduped into an upstream row like `upstream_id`). `engine != 'morphe'` → upstream row created with `kind='module'`, `asset_regex` required. |
| `POST/PUT /api/items` | Body adds `engine_options`. For Xposed sources: `sigbypass` integer, 0–3 for lspatch, 0–4 for npatch; `injectdex` boolean; unknown keys → 400. For Morphe sources: must be null. Variants of Xposed items: `exclude_splits` and `strip_libs` must be null, `download.type` ∈ `apk|bundle` (`bundle_extract` → 400; engines need one APK). |
| `GET/PUT /api/items/{id}/patches` | 409 `{"error":"not a patches item"}` for Xposed items. |
| `PUT /api/items/{id}/version` | `version_mode='auto'` → 400 for Xposed items (no compatible-version list exists). `latest` and `pinned` work as for Morphe items. |
| `GET /api/catalog` | Each item carries `engine` (from its source); the module state is the item's `upstream` object (catalog §6). |
| `POST /api/upstreams/{id}/pull` | Unchanged; for a module row it downloads the module APK, no inspect. |

#### 5.2 `job.json` (build §3.1) — new and reinterpreted fields

```json
{
  "engine": "lspatch",
  "engine_options": {"sigbypass": 3, "injectdex": true},
  "patches_files": ["DATA/upstream/<module id>/v1.1/NexAlloy-refactor-release-v1.1.apk"],
  "cli_jar": "DATA/upstream/<lspatch id>/v1.2/lspatch-v1.2-487-release.jar",
  "cli_tag": "v1.2", "upstream_tag": "v1.1",
  "options_file_json": null,
  "keystore": "lspatch-signing.p12",
  "version": {"mode": "latest", "value": null, "near": false},
  "recipe": {"variants": [{"name": "arm64-v8a", "abi": "arm64-v8a",
    "download": {"kind": "apkmirror", "type": "bundle", "arch": "arm64-v8a", "dpi": "320-640dpi", "minver": "Android 11+", "url": null},
    "exclude_splits": null, "strip_libs": null}]}
}
```

- `engine` is present for every job (`"morphe"` for patches items). `patches_files` = "engine payload files": patches bundles for Morphe, module APKs for LSPatch/NPatch.
- `upstream_tag` = module tag; `cli_tag` = engine tag. They flow into `build_jobs.patches_version` / `cli_version` and the artifact name (build §4 step 7) unchanged: `nexalloy-lspatch-facebook-arm64-v8a-arm64-v8a-580.0.0.51.74-v1.1.apk`.

#### 5.3 Runner `patch_variant` (build §4 step 5e)

```
engine=morphe  → unchanged (build §4 step 5e)
engine=lspatch|npatch →
  out=release/<variant>.d (empty dir)
  java -jar <cli_jar> <input.apk> -k src/keystore/<keystore> <storepass> <alias> <aliaspass>
       -l <sigbypass> [--injectdex] -m <module 1> [-m <module 2> …] -o <out> -f
  exactly one *.apk in <out> → move to release/<variant>.apk; else ::fail::patch_failed
```
- `-l` is always passed: NPatch's default is 1, LSPatch's is 0 (§1); explicit values make the seed reproduce today's flags exactly.
- The output is taken as "the only APK in an empty dir", not by predicting `-lspatched`/`-npatched` names (they contain the engine's internal version code, §1).
- Keystore credentials: a table in `build-item.sh` keyed by keystore file name, values copied from `utils.sh` `lspatch`, `npatch`: `lspatch-signing.p12 → morphe fiorenmas morphe`, `fiorenmas.ks → fiorenmas morphe fiorenmas`. Unknown keystore file for an Xposed engine → `::fail::internal::unknown keystore`. These passwords are already public in the repo; no new secret.
- Version step (build §4 step 5c) is unchanged: `latest` → empty version, `pinned` → value. `auto` cannot reach the runner (API rejects it); if it does, `detect_version` finds no `.mpp`, version is empty → `::fail::version_unresolved` (existing rule).
- Input download, APK cache, `result.json` (package, version, `signer_sha256`) are unchanged. `version_code` stays the original app's (no `--version-code`/`-r` passed).
- Engine flags are built only from validated `engine_options` fields; no free-text argument string is stored or passed (today's `$4` raw string, `utils.sh` `lspatch`, is not carried over).

### 6. Data flow

```
schedule/button ─► release tracker ─► module row (revenge/NexAlloy) new tag
                                     ├─► download module APK → /data/upstream/<id>/<tag>/
                                     ├─► event upstream.new_version → notification
                                     └─► rebuild enabled items of sources on that row (Q4 = a)
build ─► job_input(item) {engine, engine_options, module paths, engine jar}
      ─► build-item.sh: get_apk/get_apkpure (+APKEditor merge) → LSPatch/NPatch embed → result.json
      ─► artifacts row (abi, signer_sha256) ─► build.succeeded ─► OTA after build (unchanged)
```

"Upstream new version" for Xposed items:
- New **module** tag → same as a new patches tag: notify, and with `rebuild` enqueue the source's items.
- New **engine** tag (LSPatch/NPatch) → same as a new Morphe CLI tag today: downloaded by the pull, used by the next build, no automatic rebuild (catalog §5.4 rebuilds on the source's patches upstream only; unchanged).
- New **base app** version (e.g. a Discord release) → not tracked, same as Morphe items. Items with `version_mode='latest'` pick it up on the next build; a `build` schedule (core §6) gives periodic refresh.

### 7. Artifacts, signing, OTA

- Embedded modules: the module APK is inside the patched APK (`assets/lspatch/modules/`, §1). One APK per item; nothing extra is installed on the phone; no LSPatch manager. A module update reaches the phone only through rebuild + OTA.
- Signature: each engine signs with its fixed keystore (§5.3). Rebuilds keep the same signer, so `adb install -r` updates work (device D7 by `build_job_id`, unchanged).
- First install over a Play Store app fails with `SIGNATURE_MISMATCH` → existing D6 confirm-uninstall flow (data loss warning).
- Same package from two sources: Morphe sources also build Facebook, Messenger, Threads, TikTok (`DeRevanced.sh`, `HushFacebook.sh`, `icysymmetra.sh`) and Instagram (`Piko.sh`, `Piko-Beta.sh`) with `morphe.keystore`. Switching a phone between such items hits `SIGNATURE_MISMATCH` (device §8, already a listed limitation). See §10 item 6 for the assignment rule.
- MicroG / dependency apps: not applicable to the seeded Xposed items (§4).
- Retention, download route, `latest_artifact(item_id, abi)`: unchanged.

### 8. UI (final mockup `docs/superpowers/mockups/final/`)

| Page | Morphe item | Xposed item |
|---|---|---|
| App list row (`apps.html`) | source badge, "Update upstream" | badge "Xposed · LSPatch" or "Xposed · NPatch" + module name; "Update upstream" tooltip "updates module <name> for all its apps"; Rebuild, Download unchanged |
| Item detail tabs (`app-detail.html`: Patches, Options, Version, History) | as mocked | **Patches** + **Options** replaced by one **Module** tab: module repo, channel/tag, downloaded and latest tag, engine and engine tag (read-only, from source); editable "Signature bypass level" select (0–3 LSPatch, 0–4 NPatch) and "Inject loader dex" checkbox |
| Version tab | auto / latest / pinned | latest / pinned only; "auto" hidden (explained: modules list no compatible versions) |
| Variants editor | all fields | download fields only; split/strip fields hidden |
| Source editor (Add app dialog → "New source…", ui §3) | patches repo | engine select (Morphe / LSPatch / NPatch); for LSPatch/NPatch: module repo, channel/tag, asset regex (must match exactly one asset); engine upstream select |
| Devices, history, build log, schedules, notifications | – | no difference |

### 9. Error handling

| Failure | Behaviour |
|---|---|
| Module release has 0 matching assets | existing `check_error="no matching asset"` |
| Module release has >1 matching asset (e.g. NexAlloy `v1.0`: `-root-` and `-nonroot-`) | `check_error="ambiguous module asset (<n> matches)"`, `downloaded_tag` unchanged; the user narrows `asset_regex`. Embedding both would be wrong. |
| Module or engine never downloaded | existing `upstream_missing` (catalog §5.3) |
| Engine exits non-zero, or not exactly one APK in the output dir | `patch_failed`; engine output is in the log |
| `version_mode='auto'`, bad `engine_options`, split/strip fields, `bundle_extract` on an Xposed item | 400 from the API; never stored |
| Unknown keystore for an engine | `::fail::internal::unknown keystore` |
| Install over a store app / other source | device `SIGNATURE_MISMATCH` (D6) |

### 10. Integration

The section changes this part needs are applied in the parts above; conflicts and their resolution are in §0.5.

### 11. Testing

- Runner self-check (`src/build/test-build-item.sh`, no network): fixture `job.json` with `engine=lspatch` and a `java` shim on PATH that records argv and writes `x-1-lspatched.apk` into `-o`. Assert argv contains `-k <ks> morphe fiorenmas morphe -l 3 --injectdex -m <module> -o <dir> -f` and `release/<variant>.apk` exists. Same for `npatch` (`-l 3`, no `--injectdex`, `fiorenmas.ks` credentials). Shim writing 0 or 2 APKs → `::fail::patch_failed` and non-zero exit. Unknown keystore → `::fail::internal`.
- Catalog unit tests: `auto` rejected for Xposed item; `sigbypass` 4 rejected for lspatch, accepted for npatch; `strip_libs` and `bundle_extract` rejected; patches route 409; `job_input` of an Xposed item has `engine`, module path in `patches_files`, engine jar in `cli_jar`, `options_file_json` null; module release fixture with two matching assets → `ambiguous module asset`; new module tag with `rebuild` enqueues exactly that source's enabled items.
- End-to-end (user-run, long): pull Revenge and LSPatch, build Discord; `apksigner verify --print-certs` shows the `lspatch-signing.p12` certificate; `unzip -l` shows `assets/lspatch/modules/` with one APK; OTA to a phone and open the app.

### 12. Uncertainties

- Whether today's flags still work with LSPatch `v1.2` against current app versions (the script floats LSPatch on latest; nobody has run it locally). The first build shows the result, like BiliRoamingM (Q3).
- Whether NexAlloy `v1.1` (pin) is still the right module version; latest is `v1.0` with root/non-root assets. The pin is kept as today; the user can change the tag in the source editor.
