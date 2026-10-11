# Local Docker Build — Execution Plan (2 rounds)

Spec (contract): `docs/superpowers/specs/2026-10-12-local-docker-build-design.md` — acceptance criteria referenced as AC1–AC7.
Implementation plan: `docs/superpowers/plans/2026-10-12-local-docker-build.md`.

## Current state (read before dispatching)

Round 1's deliverables already exist on `main` in commit `b16a2c6` (written before the scope change to plan-only).
Round 1 tasks therefore start as "verify and fix the existing file", not "write from scratch". If the lead wants a clean re-implementation, revert `b16a2c6` first; this plan works either way.

## Honest split note

The whole change is 6 small files. Round 1 has 4 real, file-disjoint implementer tasks. Round 2 has no new code. Its tasks are run-and-check tasks plus fix-ups, each owning a different file. Round 2 fix-up tasks only start if their check fails. No task was invented to reach the count.

## Long-running commands (dedicated runner only, never an implementer)

| Command | Why |
|---|---|
| `docker compose -f local/docker-compose.yml build cfbypass` | git-context build plus CloakBrowser download, over 1 min |
| `docker compose -f local/docker-compose.yml build builder` | apt plus JDK install |
| `./local/build.sh morphe` | 3 matrix runs, each 1–3 min; well over the 150 s foreground cap |
| `./local/build.sh all` | hours; not required by the spec beyond iteration |

Runner rule: GitHub API calls go unauthenticated (60 requests/hour per IP, shared WARP exit IP). Check `curl -s https://api.github.com/rate_limit | jq .rate.remaining` before the e2e run. If it is below 15, wait for `reset`. Do not retry blindly.

## Round 1 — build the pieces (4 implementers + 1 reviewer, parallel)

| # | Task | Owned files | Depends on | Acceptance (mechanical) | Model |
|---|---|---|---|---|---|
| 1A | Upstream patch | `src/build/utils.sh` | — | `git diff fb56773 -- src/build/utils.sh` shows only: pup asset chosen by `uname -m` (aarch64/arm64 → `linux_arm64`, else `linux_amd64`); `${FLARESOLVERR_PORT:-8191}` in `_fs_get`; `${CFBYPASS_PORT:-8000}` in `_cfb_get`. `bash -n src/build/utils.sh` exits 0. (AC6) | sonnet |
| 1B | Builder image | `local/Dockerfile`, `local/Dockerfile.dockerignore` | — | Packages from spec, plus `python-is-python3` (`utils.sh` calls `python`) and `apksigner`. ggplay_dl requirements installed in a venv on PATH. `CMD sleep infinity`. The dockerignore admits only `src/build/helper/ggplay_dl/requirements.txt`. Image build is done by the runner in Round 2. | sonnet |
| 1C | Compose stack | `local/docker-compose.yml` | — | `docker compose -f local/docker-compose.yml config` succeeds. `grep -c 'published\|ports:'` on that output is 0 (AC3). flaresolverr gets `PORT=47191`. cfbypass is built from the upstream git URL pinned to a full commit SHA, with `command: [/app/docker-entrypoint.sh, --port, 47800]` (upstream has CMD only). Builder and cfbypass use `network_mode: service:flaresolverr`. Builder gets env `FLARESOLVERR_PORT=47191` and `CFBYPASS_PORT=47800`, and the repo mounted at `/repo`. The task verifies CloakBrowser's linux-arm64 mapping in its source (cloakbrowser `config.py`) and cites file:line in its report (AC4 input). | opus (arch fallback judgment) |
| 1D | Entry script and ignores | `local/build.sh`, `.gitignore` | interface only: compose service name `builder`, mount `/repo` | `bash -n local/build.sh` exits 0. `./local/build.sh` with no args lists 18 orgs, equal to `grep -c "^          - '" .github/workflows/manual-patch.yml` (AC5). An unknown org exits 2. `.gitignore` contains `local/out/` and `local/work/` (AC7). Per-run clean `local/work/<org>-<run>` with a `src` symlink. `GITHUB_OUTPUT` points to a file in the work dir. `docker compose exec` gets `</dev/null` (otherwise it eats the loop's stdin). PASS means exit 0 and at least 1 APK. The summary is printed and the script exits non-zero on any failure. Optional `[run]` argument. | sonnet |
| 1R | Reviewer | read-only | 1A–1D done | Review against spec "Files", "Upstream changes" and the review protocol L1–L3. Check the interface between 1C and 1D: service name, mount path and env names match. | sonnet |

**Gate into Round 2:** reviewer reports no blockers. `git diff --name-only fb56773 HEAD` lists only `.gitignore`, `src/build/utils.sh`, `local/*` and `docs/superpowers/plans/*`. Every 1A–1D check above passes. Each task commits its own files (pathspec commit).

## Round 2 — run, verify, fix (runner tasks + contingent fix-ups + 1 reviewer)

| # | Task | Owned files | Depends on | Acceptance (mechanical) | Model |
|---|---|---|---|---|---|
| 2A | Image build runner | none (runs commands only) | 1B, 1C | `docker compose ... build builder` and `build cfbypass` both report RC=0. `docker image inspect -f '{{.Architecture}}'` prints `arm64` for both images (AC4). The cfbypass build log contains the linux-arm64 Chromium pin (`chromium-146.0.7680.177.3` at cloakbrowser 0.6.1). If the build fails, report the error; the fix goes to 2D, and the amd64 fallback needs a lead decision. | haiku (run only) |
| 2B | Service probe runner | none | 2A | `docker compose ... up -d`. Inside the builder: `curl localhost:47191` returns JSON with `FlareSolverr is ready!`, and `curl -o /dev/null -w '%{http_code}' localhost:47800/` returns any HTTP code (not 000) (AC3). | haiku |
| 2C | End-to-end runner | none | 2A, 2B, rate-limit check | `./local/build.sh morphe` with a long timeout runs to its summary (AC1). `ls local/out/morphe/*youtube*.apk` is not empty (AC1). `apksigner verify` RC=0 on each APK (AC2). Exit code and summary are recorded. Run 3 (YouTube Music) may FAIL on APKMirror 403; the spec says report, do not work around. Then: `git status --porcelain` is empty (AC7) and `git diff --name-only fb56773 HEAD` is checked (AC6). | haiku (run only) |
| 2D | Fix-up: images/compose | `local/Dockerfile`, `local/Dockerfile.dockerignore`, `local/docker-compose.yml` | only if 2A or 2B fails | Same checks as 1B/1C, then 2A/2B rerun green | sonnet |
| 2E | Fix-up: entry script / upstream | `local/build.sh`, `.gitignore`, `src/build/utils.sh` | only if 2C fails for a script reason (not a mirror or rate-limit reason) | Same checks as 1A/1D, then 2C rerun | sonnet |
| 2R | Reviewer | read-only | 2C done | Check the runners' raw outputs against AC1–AC7 one by one, with a quoted output line for each. Confirm no published ports, both images arm64, and the upstream diff limited to the two `utils.sh` changes plus `.gitignore`. | sonnet |

**Exit gate (done):** 2R marks AC1–AC7 PASS with evidence. AC5's `all` is accepted on listing plus the selection count (18 rows) without a full `all` run.
Round budget: at most 3 attempts at 2C. If they fail, stop and report the failure trail.

## Known blockers seen during the pre-scope-change run (b16a2c6)

- `morphe 1` passed: 5 APKs, `apksigner verify` RC=0. `morphe 2` passed. `morphe 3` failed with APKMirror 403.
- One full `morphe` run failed on all runs: GitHub API rate limit at 0. This is why 2C has the rate-limit precheck.
