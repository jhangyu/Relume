# Local Docker Build Follow-up — Execution Plan (2 rounds)

Spec (contract): `docs/superpowers/specs/2026-10-12-local-docker-build-followup-design.md` — AC1–AC10, round budget 2.
Implementation plan (steps and code per task): `docs/superpowers/plans/2026-10-12-local-docker-build-followup.md` — "Task N" below refers to it.

## Honest split note

Round 1 has 4 real, file-disjoint implementer tasks; a 5th would be busywork (the token change in `src/` is 6 one-line edits of one pattern, splitting it gains nothing).
Round 2 has 1 implementer task (the APKMirror fix — it depends on round 1's investigation, so it cannot move earlier), 2 runner tasks, 1 contingent fix-up that only starts if a runner fails for a script reason, and the reviewer. There is no honest 4–5-implementer split for round 2.

## Before round 2: user action

The user writes a GitHub token to `$REPO_ROOT/src/keystore/github_token` (fine-grained, public repositories read-only; `printf %s <token> > file`). The lead confirms the file is non-empty with `test -s`, never by reading it. Without it, AC2's mount check, AC3, AC4 and AC8 cannot pass (the shared WARP IP's unauthenticated quota was 0/60 at epoch 1791736766).

## Long-running commands (runners only, never implementers)

| Command | Owner |
|---|---|
| `docker compose -f local/docker-compose.yml up -d --build` (may rebuild cfbypass from git) | 2B |
| `./local/build.sh morphe` (3 runs, several minutes) | 2C, detached with `nohup`, first liveness check within 5 minutes |
| `./local/build.sh morphe 2` with an empty token file | 2C |

Round 1 tasks must not run `compose up/build/down`; the investigation (1D) depends on the current stack staying up.

## Round 1 — token support, review-and-fix, investigation (4 implementers + 1 reviewer, parallel)

| # | Name | Owned files | Inputs / depends on | Acceptance (mechanical) | Model |
|---|---|---|---|---|---|
| 1A | token-upstream | `src/etc/connection.sh`, `src/etc/ci.sh`, `src/etc/_ci.sh`, `src/build/utils.sh` | spec "Token path" step 5; plan Task 1 | Plan Task 1 `run.sh set` red before, then `with_auth == api_calls` (≥4); `run.sh unset` → `any_auth=0`; per file `grep 'api.github.com' F \| grep -vc 'GITHUB_TOKEN:+'` → 0; `bash -n` RC 0 on all four; `git diff fb56773 -- src/build/utils.sh` = `b16a2c6` hunks + 3 header lines (AC1, part of AC9) | sonnet |
| 1B | compose-mount | `local/docker-compose.yml`, `local/Dockerfile`, `local/Dockerfile.dockerignore` | spec "Token path" step 2; plan Task 2 | `RV_GITHUB_TOKEN_FILE=/tmp/x docker compose -f local/docker-compose.yml config` shows `source: /tmp/x`, `target: /run/secrets/github_token`, `read_only: true`, `create_host_path: false`; `grep -c 'published\|ports:'` on config → 0; review findings for the three files listed in the commit body (compose half of AC2) | sonnet |
| 1C | build-script | `local/build.sh`, `.gitignore` | interface names only (`RV_GITHUB_TOKEN_FILE`, `/run/secrets/github_token`, `GITHUB_TOKEN`); plan Task 3 | `bash -n` RC 0; no-arg run with `RV_GITHUB_TOKEN_FILE=<tmpdir>/sub/tok` → `stat -f '%HT %Lp'` prints `Regular File 600` (AC5); no-arg lists 18 orgs; unknown org → exit 2; `grep -n 'GITHUB_TOKEN' local/build.sh` has no `-e GITHUB_TOKEN` and no `echo $GITHUB_TOKEN`; `$tok` prefix present in the per-run command and the status-line command | sonnet |
| 1D | apkmirror-investigation | `docs/logs/2026-10-12-apkmirror-403-investigation.md` (new); scratch only under `local/work/apkm-probe/` | running stack (do not restart); plan Task 4; spec "APKMirror download path" | Report has, for YouTube Music arm64-v8a and YouTube: page source service per hop; HTTP status + size for download variants (a)/(b)/(c), ≥2 attempts each; 0-byte-file check result; one classification (A/B/C/D/other) with evidence lines; proposed `utils.sh` change (≤30 lines) or "no honest fix" statement (AC6). No cookie values or tokens in the report (`grep -c 'cf_clearance=' report` → 0) | opus |
| 1R | round1-reviewer | read-only | 1A–1D committed | Review `git diff fb56773 HEAD` against the original spec and the follow-up spec, plus review protocol L1–L3. Check the interface names are identical across 1A/1B/1C (`grep -rn 'run/secrets/github_token\|RV_GITHUB_TOKEN_FILE\|Bearer' local src`). Check 1D's classification follows from its quoted evidence. Output: blocker / should-fix / nit list with file:line | sonnet (podium:team-reviewer) |

**Gate into round 2:** 1R reports no blockers; every 1A–1C check above re-run by 1R passes; 1D's report exists with a classification; each task committed only its own files (`git log --name-only` per commit); user token file present (`test -s`).

## Round 2 — fix, run, verify (1 implementer + 2 runners + 1 contingent fix-up + 1 reviewer)

| # | Name | Owned files | Inputs / depends on | Acceptance (mechanical) | Model |
|---|---|---|---|---|---|
| 2A | apkmirror-fix | `src/build/utils.sh` | 1D report; plan Task 5. Code first; its probe re-run waits for 2B's stack | `grep -c x-cf-bypasser-cookies src/build/utils.sh` → 0; `_cfb_get` live probe in builder → `FS_COOKIES` contains `=` (AC7); 1D's YouTube Music probe re-run → HTTP 200, size > 10 MB, `unzip -l` lists `AndroidManifest.xml` (skip only if 1D classified "no honest fix"); 1A's `run.sh set/unset` still green; `bash -n` RC 0 | sonnet (opus if 1D's proposed change is more than the cookie fix plus one fallback) |
| 2B | stack-runner | none (commands only) | gate passed; plan Task 6 | `up -d --build` RC 0; both images `arm64`; `FlareSolverr is ready!`; 47800 code ≠ `000`; `test -r` RC 0 and `touch` RC ≠ 0 on `/run/secrets/github_token`; in-container `rate_limit` `.rate.limit` ≥ 1000; `GITHUB_TOKEN` count 0 in container env and image history (AC2, AC3 part, AC4 first half, original AC3/AC4) | haiku (run only) |
| 2C | e2e-runner | none (writes only `docs/logs/2026-10-12-local-docker-build-e2e.log` and `local/work/r2-*.log`) | 2A committed, 2B green; plan Task 7 | Detached run; liveness check within 5 min shows `GitHub API: authenticated` and `=== morphe run 1`; final log has `RC=`, summary with `PASS morphe run 1`, `PASS morphe run 2`, run 3 line; `apksigner verify` RC 0 for each new APK; `grep -cF -f <token file> <log>` → 0; empty-token run log shows `GitHub API: unauthenticated`; `git status --porcelain`; `git diff --name-only fb56773 HEAD -- src .github` (AC3, AC4 second half, AC8, AC9) | haiku (run only) |
| 2D | script-fixup (contingent) | `local/build.sh`, `local/docker-compose.yml`, `src/etc/connection.sh` | only if 2B or 2C fails for a script reason (not APKMirror, not GitHub quota) | Same checks as 1B/1C, then the failed runner step re-run green | sonnet |
| 2R | round2-reviewer | read-only | 2C finished | Check AC1–AC10 one by one against the runners' raw outputs, quoting one output line each. For run 3 FAIL: confirm 1D's classification is C (or another "no fix" cause) and the evidence meets the spec's "no honest fix" rule; otherwise run 3 FAIL is a blocker | sonnet (podium:team-reviewer) |

Ordering inside round 2: 2A (coding) and 2B start together; 2A's live probes wait for 2B's `up`; 2C starts after both. 2C may run at most 2 attempts (round budget); after that, stop and report the failure trail.

**Exit gate (done):** 2R marks AC1–AC10 PASS with quoted evidence. If not, stop and report; no round 3.

## Red lines for every task prompt

- Never print, log, commit or pass on a docker command line the token value. Tests use `dummy-not-a-token`.
- Git: no `git stash` (including `stash list`), `reset`, `checkout --`, `clean`, `add -A`. Commit with `git add <own files>` and `git commit -- <own files>` on `main` (check `git rev-parse --abbrev-ref HEAD`). No push.
- Only owned files. `docs/logs/2026-10-12-local-docker-build-report.md` stays untouched.
- Implementers do not run `compose up/build/down` or `./local/build.sh <org>`; runners do.
- No sleep or polling loops; runners end the turn after the first liveness check and wait to be woken.
