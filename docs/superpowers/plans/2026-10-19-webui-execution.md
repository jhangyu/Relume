# Relume Web UI — Execution Plan (2 rounds)

Plan: `docs/superpowers/plans/2026-10-19-webui.md` (packages P1–P11, task steps, Plan-fixed names, coverage). Spec: `docs/superpowers/specs/2026-10-19-webui-design.md`. Contract: `docs/logs/2026-10-19-webui-contract.md`.

## 1. Rules for every implementer (copy into every dispatch prompt)

- First line of every dispatch: `ROLE: WORKER — you may NOT spawn agents, teams, or workflows. If the task needs delegation, STOP and report back to leader.`
- Read first: your package section of the plan, the "Global Constraints", "Test commands" and "Plan-fixed names" sections, and `docs/superpowers/plans/parts/ui-api-shapes.md` (frontend and route owners).
- Edit only the files in your row below. Need a change in another member's file → SendMessage that member (name below); do not edit it.
- Shared tree git rules: no `git stash` (also no `stash list`), no `reset`, no `checkout --`, no `clean`, no `git add -A`. Commit with `git add <own files> && git commit -m "<msg>" -- <own files>`; no `;` in the message; run `git rev-parse --abbrev-ref HEAD` before and `git status --porcelain -- <own files>` after (must be empty).
- No `sleep`, no polling loops in shell commands; to wait for another member, end your turn and wait for the message.
- Foreground limit 150 s. Anything longer (image builds, real builds, phone checks, GitHub Actions) is user-run: do not start it, list it in your report.
- TDD per task: failing check → implement → passing check → commit (steps are in the plan).
- Report (SendMessage to `lead`, fallback `main`): conclusions with `file:line`; changed files; each acceptance item pass/fail with the command and its exit code or output line; open items; last line `READY_FOR_SIGNOFF` or `BLOCKED`. Over 30 lines → write `docs/logs/2026-10-19-<member>-report.md` and send the path. Do not mark tasks completed.
- Context limit: at the hand-off threshold write `docs/logs/2026-10-19-baton-<member>.md` (package, finished tasks with commit hashes, next task and step, failures so far) and report `READY_FOR_HANDOFF`.

Test commands (from the plan): `T_PY <pattern>`, `T_SH <script>`, `T_JS <file>` = `node --check <file>`, `T_COMPOSE <mode>`. The test image is `relume-builder:latest` until the user builds the new `app` image.

## 2. Round 1 — 6 implementers + 1 test runner + 1 reviewer

| member | model | agent type | package | owned files (exclusive in round 1) |
|---|---|---|---|---|
| impl-core | opus | podium:team-implementer | P1 | `local/webui/{__init__,__main__,server,db,auth,jobs,events,scheduler,settings}.py`, `local/webui/schema/001_core.sql`, `local/webui/tests/{__init__,helpers}.py`, `local/webui/tests/test_core_*.py` |
| impl-catalog | opus | podium:team-implementer | P2 | `local/webui/{catalog,upstream}.py`, `local/webui/schema/003_catalog.sql`, `local/webui/catalog-seed.json`, `local/webui/tests/test_catalog_*.py`, `local/webui/tests/catalog_rows.py`, `local/webui/tests/fixtures/catalog/`, `docs/logs/2026-10-19-seed-report.md`, scratch `.claude/tmp/` |
| impl-runner | sonnet | podium:team-implementer | P3 | `src/build/{build-item,inspect,test-build-item,utils}.sh`, `src/build/test-fixtures/` (incl. `bin/{java,apksigner,wget,curl}` shims and `*.txt` captures), `local/test-compose.sh`, `local/Dockerfile`, `local/Dockerfile.dockerignore`, `local/docker-compose.yml`, `local/.env.example`, `local/solver/solver.py`, `.gitignore` |
| impl-build | sonnet | podium:team-implementer | P4 | `local/webui/build.py`, `local/webui/schema/002_build.sql`, `local/webui/tests/test_build_*.py`, `local/webui/tests/fixtures/build/` |
| impl-device | sonnet | podium:team-implementer | P5 | `local/webui/{devices,ota,notify}.py`, `local/webui/schema/004_device.sql`, `local/webui/tests/test_device_*.py`, `local/webui/tests/device_helpers.py`, `local/webui/tests/fixtures/device/` |
| impl-ui-shell | sonnet | podium:team-implementer | P6 | `local/webui/static/{app.css,app.js,login.html,login.js,index.html,index.js}`, `local/webui/tests/test_ui_static.py` |
| gate-r1 | haiku | podium:team-test-runner | runs gate commands only | none |
| reviewer-r1 | opus | podium:team-reviewer | review | none (writes `docs/logs/2026-10-19-round1-review.md` only) |

Order inside round 1:
1. All six start at once. impl-core lands P1.1 → P1.3 first (the interface window: `db`, `events`, `jobs`, `server`, `helpers`) and messages the other five with each commit hash.
2. impl-runner lands P3.1 first (`.gitignore` entry for `.claude/tmp/` and the fixture `src/build/test-fixtures/job.json`) and announces it: P2.6/P4.2 tests read that fixture and P2.9 uses `.claude/tmp/`.
3. In the same window impl-build commits `schema/002_build.sql` and impl-catalog commits `schema/003_catalog.sql` (first tasks of P4 / P2) and announce them; impl-device commits `004_device.sql` after both, so every test can call `db.migrate()` on the real schema directory (foreign keys to `item` and `jobs`).
4. Until the window closes: impl-runner works on P3 (no Python dependency); impl-catalog on the seed script and fixtures; impl-device on the adb/mDNS parsers; impl-ui-shell on P6.1 CSS/JS (P6.1's server test needs P1.4 + P1.7; run it after impl-core announces P1.7).
5. Cross-package calls inside round 1 are faked in unit tests (spec signatures and Plan-fixed names only). No member waits for another member's feature code.

### Round-1 integration gate (gate-r1 runs; lead reads the outputs)

Trigger: all six members report `READY_FOR_SIGNOFF`. Writers broadcast `SETTLED`; no writes during the gate window; gate-r1 confirms the end before anybody writes again.

| # | command | pass rule |
|---|---|---|
| G1 | `T_PY 'test_*.py'` | exit 0, `OK` line (first run with every real module) |
| G2 | `T_SH src/build/test-build-item.sh` | exit 0 |
| G3 | `T_COMPOSE host` and `T_COMPOSE bridge`; same command with `RELUME_NETWORK_MODE` unset | `RC=0`, `RC=0`, non-zero |
| G4 | `for f in local/webui/static/*.js; do node --check "$f" \|\| echo FAIL $f; done` | no `FAIL` |
| G5 | `mkdir -p .claude/tmp/smoke-r1 && docker run --rm -v "$PWD:/repo:ro" -v "$PWD/.claude/tmp/smoke-r1:/data" -e PYTHONPATH=/repo/local -e PYTHONDONTWRITEBYTECODE=1 relume-builder sh -c 'timeout 20 python3 -m webui & curl -sf --retry 15 --retry-connrefused --retry-delay 1 localhost:47000/api/health' \| python3 -c 'import json,sys;assert sorted(json.load(sys.stdin))==["app","ok","started_at","version"];print("SMOKE OK")'` (fresh data dir each run) | prints `SMOKE OK` (real start-up incl. seed import, every `register()` and `start()`) |
| G6 | `docker run --rm -v "$PWD:/repo:ro" -e PYTHONPATH=/repo/local -e PYTHONDONTWRITEBYTECODE=1 -e RELUME_DATA=/tmp/d relume-builder python3 -c "import threading,webui.__main__ as m;b=threading.active_count();m.boot();assert threading.active_count()==b;print('REGISTER OK')"` | prints `REGISTER OK` |
| G7 | `grep -n 'RELUME_GITHUB_TOKEN_FILE\|RELUME_UID\|RELUME_DATA_DIR' local/docker-compose.yml \| wc -l` | ≥ 3 (P7 depends on these names) |
| G8 | `git grep -n 'shell=True' -- local/webui` | no output |

Fix loop: a failing gate line goes back to the owning member (lead names the member from the failing test file); at most 2 fix rounds, then the lead reports the failure trail to the user.

### Round-1 reviewer checklist (reviewer-r1, fresh context, after the gate is green)

1. Interface conformance: every name in each package's "Interfaces/Produces" and in the Plan-fixed table exists with the frozen signature (`grep -n 'def <name>'`); every cross-package call uses those names; event names and payload keys equal spec L50–59; round-1 routes equal spec L67–92; response shapes equal `ui-api-shapes.md`.
2. Security: validation regexes of spec L243–249 applied before any path, argv or bash use; no shell strings; no `eval` in bash; secrets never returned (settings, notify); runner env has no `GITHUB_TOKEN`; sidecars bind `127.0.0.1`; static rule (only `/login.html` and `/static/*` without session).
3. Stdlib-only imports in `local/webui/*.py`; `register()` starts no thread (G6).
4. File ownership: `git log --format='%h %s' --name-only <round-1 base>..HEAD` shows no file outside its owner's row.
5. Removed behaviour: for every function P3 removed from `utils.sh`, `git grep` shows no remaining caller outside files that P11 deletes in round 2.
Findings: blocker / should-fix / nit with `file:line`; blockers go back to the owner (max 2 review rounds); should-fix and nits go to the parking lot.

### User-run steps after round 1 (lead asks the user once, in one message)

| id | step | expected |
|---|---|---|
| U1 | `docker compose -f local/docker-compose.yml build app` (arm64 host) | build succeeds |
| U2 | in the new image: `adb version`; `adb pair 127.0.0.1:1 000000`; `pup --version`; APKEditor usage | adb ≥ 34; `Failed: Unable to start pairing client.`; `0.4.0`; usage text (device T6) |
| U3 | `docker buildx build --platform linux/amd64 -f local/Dockerfile ..` | build succeeds (CI image) |
| U4 | U-core-smoke on the Mac (bridge) and on the Linux host (host) | per P1/P6 user-run table in the plan |
| U5 | upstream check with the token: on the running server `POST /api/upstreams/check` | every upstream `check_error` null |
| U6 | one real build: pull morphe, edit one override, build morphe YouTube | artifact name carries app + patches version; `apksigner verify` passes; the override appears in the CLI log line |
| U7 | U-ui-round1 browser walkthrough | per the plan's P6 user-run row |
| U8 | device T4 (Linux phone) or T5 (Mac phone), T7 key persistence | per spec device §7 |
| U9 | every other check marked user-run in P2, P3, P4, P5 of the plan | as written there |

APKMirror rate-limits the home IP: U6 runs one item at a time with gaps. Round 2 may start before U1–U9 finish (round-2 code does not need them), but the round-2 reviewer runs after the user has reported U1, U4 and U6.

### Hand-off round 1 → round 2

The lead writes `docs/logs/2026-10-19-round1-handoff.md`: final commit hash of round 1, gate outputs (G1–G8 lines), reviewer verdict, parking-lot items, user-run results so far, and per round-2 member the files they take over from round 1 (`catalog.py`, `utils.sh`, `build-item.sh`, `test-build-item.sh`, `app.css`, `app.js`, `test_ui_static.py`). Every round-2 dispatch prompt says: read the hand-off file first.

## 3. Round 2 — 5 implementers + 1 test runner + 1 reviewer

| member | model | agent type | package | owned files (exclusive in round 2) |
|---|---|---|---|---|
| impl-ci | sonnet | podium:team-implementer | P7 | `local/webui/ci.py`, `local/webui/tests/test_ci.py`, `local/webui/tests/fixtures/ci/`, `local/docker-compose.ci.yml`, `.github/workflows/build.yml` |
| impl-xposed | sonnet | podium:team-implementer | P8 | `src/build/{build-item,utils,test-build-item}.sh`, `src/build/test-fixtures/`, `local/webui/catalog.py`, `local/webui/tests/test_catalog_xposed.py` |
| impl-pages-a | sonnet | podium:team-implementer | P9 | `local/webui/static/{item.html,item.js,builds.html,builds.js,app.css,app.js}`, `local/webui/tests/test_ui_static.py` |
| impl-pages-b | sonnet | podium:team-implementer | P10 | `local/webui/static/{schedules,devices,settings}.{html,js}` |
| impl-cleanup | haiku | podium:team-implementer | P11 | `local/webui/tests/test_catalog_seed.py` (removes the two committed-seed tests that read deleted files, Task P2.9 note), `README.md`, `docs/Frequently Asked Questions.md`, `docs/image/FAQ/patching-app-{3,4}.png`, and the deletions listed in P11 only (incl. `src/build/helper/ggplay_dl/`, `src/etc/_ci.sh`, the six `docs/superpowers/{specs,plans}/2026-10-12-local-docker-build*.md` docs, and the untracked `src/options/hushfacebook.json`, `src/options/piko-newx.json` after the seed is committed) |
| gate-r2 | haiku | podium:team-test-runner | runs gate commands only | none |
| reviewer-r2 | opus | podium:team-reviewer | review | none (writes `docs/logs/2026-10-19-round2-review.md` only) |

Order inside round 2: impl-ci, impl-xposed, impl-pages-a, impl-pages-b start at once. impl-cleanup prepares its deletion list and the dangling-reference command at once, and deletes only after impl-ci and impl-xposed report `READY_FOR_SIGNOFF` (P8 reads reference values with `git show 9fd15dc:<path>`). impl-pages-b sends `app.css`/`app.js` change requests to impl-pages-a. `local/out/` and `local/work/` are not deleted and stay ignored.

### Round-2 integration gate (gate-r2)

| # | command | pass rule |
|---|---|---|
| H1 | `T_PY 'test_*.py'` | exit 0 |
| H2 | `T_SH src/build/test-build-item.sh` | exit 0 (incl. LSPatch/NPatch cases) |
| H3 | `for f in local/webui/static/*.js; do node --check "$f" \|\| echo FAIL $f; done` | no `FAIL` |
| H4 | `RELUME_NETWORK_MODE=host docker compose -f local/docker-compose.yml -f local/docker-compose.ci.yml config \| grep -A2 'command:'` | shows `sleep` and `infinity` under `app` |
| H5 | `actionlint .github/workflows/build.yml` | exit 0 |
| H6 | P11 dangling-reference command from the plan | no output |
| H7 | G5 smoke (fresh data dir) | `SMOKE OK` |

### Round-2 reviewer checklist (reviewer-r2)

1. Full regression = H1–H7 outputs read, not re-run by the reviewer.
2. UI ↔ spec: every ui §2 row marked covered/add/restored has its control or text in the owning page (grep the route each row names in the page JS); deferred/dropped rows (cron editor, latency, locked patches, username, preview-host toggle, "Clean up now", "Stored encrypted") are absent.
3. Deletions complete per P11 acceptance; no reference outside `docs/` to deleted files; no reference to the 2026-10-12 local-docker-build docs outside `docs/logs` and the 2026-10-19 plans.
4. CI workflow equals spec L1279–1347 (permissions `contents: write`, concurrency group, `actions/checkout@v7.0.1`, `xargs -d '\n'` upload).
5. Xposed: validation rules and runner argv equal spec xposed §5.1, §5.3.
6. Lists every user-run check still pending.
Same fix-loop limits as round 1.

### User-run steps after round 2

| id | step | expected |
|---|---|---|
| V1 | U-ui-round2 browser walkthrough | per the plan's P9/P10 user-run row |
| V2 | Xposed: pull Revenge + LSPatch, build Discord, `apksigner verify --print-certs`, `unzip -l` | lspatch-signing certificate; one APK under `assets/lspatch/modules/`; OTA and open the app |
| V3 | push the branch; `workflow_dispatch` with `source=hushfacebook`; rerun without force | asset `hushfacebook-<key>-<variant>.apk` with a patches-tag label; second run `plan` outputs `[]` |
| V4 | every other user-run check of P7, P8, P11 in the plan | as written there |
| V5 | one-time (parking lot): delete old-name release assets; rewrite README download links (old → new table in `docs/logs/2026-10-19-seed-report.md`) | user decision |

## 4. Close

Done when: both gates green, both reviewers without open blockers, the user-run results U1–U9 and V1–V4 reported (failures become parking-lot or fix tasks by user decision), and the lead closes the team per `~/.claude/reference/team-shutdown-protocol.md`. The lead reports the parking lot (contract file + reviewer should-fix/nits) to the user.
