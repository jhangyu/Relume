# Local Docker Build — Follow-up Design

Parent spec: `docs/superpowers/specs/2026-10-12-local-docker-build-design.md` (called "original spec" below).
First implementation: commit `b16a2c6`. Its run report: `docs/logs/2026-10-12-local-docker-build-report.md`.

## Contract

| Field | Value |
|---|---|
| End state | `./local/build.sh morphe`, run by a dedicated runner with a GitHub token mounted, finishes with runs 1 and 2 PASS and run 3 either PASS or FAIL with a written, evidenced root cause that matches the "no honest fix" rule below. |
| In-scope deliverables | (1) Review-and-fix of the `b16a2c6` files against the original spec. (2) GitHub token support: host file → read-only bind mount → `GITHUB_TOKEN` in the container → every GitHub REST API call in `src/` sends it when present. (3) APKMirror 403 investigation report and, if one exists, a fix in `get_apk`. (4) One full end-to-end `./local/build.sh morphe` run by a dedicated runner, with the log kept. |
| Out of scope | morphe-beta `*_experimental_support` variables; clearing `local/out/` between runs; making any other org build; `.github/actions/*` and workflow files; scheduling, release upload, Telegram backup; deleting the unused `src/etc/_ci.sh`. |
| Acceptance criteria | AC1–AC10 below. |
| Round budget | 2 rounds. If AC1–AC10 are not all met after round 2, stop and report the failure trail; do not open round 3. |

## Decisions (user-approved, frozen)

1. Keep `b16a2c6`. Round 1 reviews and fixes its files against the original spec.
2. The GitHub token reaches the builder only through a read-only bind mount of a host file. It is never on a docker command line, never in the image, never committed, never printed.
3. The failed items are fixed: rate limit (through decision 2), APKMirror 403 (investigate; fix with existing mechanisms or document honestly), and a full end-to-end run by a dedicated runner.

## Verified facts (checked in code and live, 2026-10-12)

### GitHub API calls

Every call to `api.github.com` in the repo (`grep -rn api.github.com --exclude-dir=.git --exclude-dir=docs`):

| Location | Tool | Runs locally? |
|---|---|---|
| `src/etc/connection.sh:5` | curl | yes (gate before every run) |
| `src/build/utils.sh:42` (`dl_gh`, prerelease branch) | wget | yes |
| `src/build/utils.sh:86` (`dl_gh`, latest/tag branch) | wget | yes (morphe: 2 calls per run) |
| `src/build/utils.sh:687` (`telegram_dl`) | wget | only for orgs that use Telegram |
| `src/etc/ci.sh:5` | wget | no (ci.yml) |
| `src/etc/_ci.sh:23` | wget | no (no caller) |
| `.github/actions/experimental-check/action.yml:27` | curl | no; already sends `Authorization: token $GITHUB_TOKEN` (out of scope) |

- None of the `src/` calls send a token today. `manual-patch.yml:5` already sets `GITHUB_TOKEN` at workflow level, so in GitHub Actions these calls will become authenticated after this change (limit 1,000/h per repository instead of 60/h per IP). This is the intended behavior and keeps Actions valid.
- Release asset downloads (`github.com/.../releases/download/...`, e.g. `utils.sh:7`, `utils.sh:11`, and the `browser_download_url` fetches) are not REST API calls and do not count against the API limit. They stay unauthenticated.
- Live probe at 2026-10-12 (epoch 1791736766): `curl https://api.github.com/rate_limit` → `limit 60, remaining 0, reset 1791738474`. The shared WARP exit IP is still exhausted.
- GitHub docs (REST "Authenticating to the REST API"): header `Authorization: Bearer <token>` (or `token <token>`). Authenticated personal token: 5,000/h. Repeated invalid credentials cause a temporary `403` lockout for all auth attempts.

### APKMirror download path (morphe run 3)

`morphe.sh:3()` calls `get_apk "com.google.android.apps.youtube.music" "youtube-music-arm64-v8a" "apk" "arm64-v8a"`. In `get_apk` (`utils.sh:275`):
1. All HTML pages (release page, variant page, download page) are fetched through `_cf_get` (`utils.sh:266`): FlareSolverr first; after the first FlareSolverr failure, `_FFS_FAILED=1` and every later page in that script uses CloudflareBypass `/html`.
2. The APK file itself is fetched with plain `wget` at `utils.sh:511-516`, sending the cookies (`FS_COOKIES`) and user agent captured by the last `_cf_get`.
3. The previous run logged `ERROR 403: Forbidden`. That is a wget message, so the 403 came from step 2 (the binary download), not from a page fetch. The full log of that run was not kept, so it is not known whether FlareSolverr or CloudflareBypass served the pages.
4. Live probe: `curl` of the YouTube Music APKMirror page from the host returned 200 (no challenge on HTML).

Code-grounded candidate causes (round 1 must confirm or reject each with evidence):
- **A. CloudflareBypass cookie bug (confirmed bug, cause not yet confirmed).** `_cfb_get` sets `FS_COOKIES` from response header `x-cf-bypasser-cookies` (`utils.sh:250`). In CloudflareBypassForScraping at the pinned commit `0f628b9`, that header is the cookie **count**: `cf_bypasser/server/routes.py:161` → `"x-cf-bypasser-cookies": str(len(data["cookies"]))` (also `docs/USAGE.md`: "number of cookies generated"). So whenever the CloudflareBypass fallback served the pages, wget sent `Cookie: 3` (or similar) instead of real cookies. Real cookies and the matching user agent are available from `GET /cookies?url=...` (JSON `cookies` + `user_agent`).
- **B. wget fingerprint rejected on the download endpoint.** Cloudflare can reject a non-browser TLS fingerprint even with valid cookies. CloudflareBypass "mirror mode" (`GET http://localhost:$CFBYPASS_PORT/<path>` with header `x-hostname: www.apkmirror.com`) replays the request with a Chrome TLS fingerprint and returns the body unchanged (README "Mirror mode"; `routes.py:227` catch-all route). Note: it buffers the whole response in memory (`routes.py:308`); YouTube Music is ~100–150 MB.
- **C. IP reputation / per-file block on the WARP exit IP.** Not fixable with existing mechanisms.
- **D. Arch-specific variant.** Run 1 (YouTube, no arch filter) downloaded through the same code and passed; run 3 is the only call with an `arch` filter. Must be ruled in or out.
- Side issue to verify: with `-O`, wget may leave a 0-byte file after an HTTP error; `utils.sh:518` only checks `-f`, so a failed download could be logged as success. Verify inside the builder before relying on it.

Alternative sources already in the scripts: `get_apkpure` (`utils.sh:535`) has no YouTube Music entry in `apps.json`; `get_apk_chplay` (`utils.sh:622`, Google Play via `ggplay_dl.py`) always fetches the newest Play version (`ggplay_dl.py:481-494`, no version pin), so it cannot honour the patch-compatible version from `detect_version`. Neither is an honest drop-in fix; switching source would also edit `morphe.sh`. Not chosen.

### Docker Compose bind mounts (compose-spec `05-services.md`, Docker Compose v5.1.2 installed)

- Long syntax: `type: bind`, `source`, `target`, `read_only: true`, `bind.create_host_path`.
- `create_host_path` defaults to `true` and creates a **directory** at a missing source. It must be set to `false`, and `build.sh` must create the file first.
- Compose `secrets:` with `file:` would also bind-mount to `/run/secrets/<name>`, but the docs do not state it is read-only. A plain read-only bind mount is used instead, at the same conventional path.

## Design

### Token path (interface contract — all tasks use these exact names)

| Item | Value |
|---|---|
| Host file | `$RV_GITHUB_TOKEN_FILE`, default `$REPO_ROOT/src/keystore/github_token` (inside the repo but listed in .gitignore, so it cannot be committed). Content: the token on one line. Empty file = no token. |
| Container path | `/run/secrets/github_token`, read-only |
| Container variable | `GITHUB_TOKEN` (the name `manual-patch.yml:5` and `experimental-check` already use) |
| Header | `Authorization: Bearer $GITHUB_TOKEN` |

Flow:
1. `local/build.sh` (host): `RV_GITHUB_TOKEN_FILE=${RV_GITHUB_TOKEN_FILE:-$REPO_ROOT/src/keystore/github_token}`; export it; if the file is missing, create its directory and an empty file with mode `600`. Never read or print its content on the host.
2. `local/docker-compose.yml`, builder service:
   ```yaml
   volumes:
     - ..:/repo
     - type: bind
       source: ${RV_GITHUB_TOKEN_FILE:-../src/keystore/github_token}
       target: /run/secrets/github_token
       read_only: true
       bind:
         create_host_path: false
   ```
3. `local/build.sh`, inside the in-container `bash -c` string (single-quoted part, so the host shell never expands it): `[ -s /run/secrets/github_token ] && export GITHUB_TOKEN=$(tr -d "[:space:]" </run/secrets/github_token)`. The docker command line carries only that literal text, never the value.
4. Once per invocation, `build.sh` prints one status line from inside the builder: `GitHub API: authenticated` / `unauthenticated` / `token rejected (HTTP 401)` plus `remaining=<n>`, from `GET https://api.github.com/rate_limit` (this endpoint does not count against the limit). No token text is printed.
5. Upstream call sites add the header only when the variable is non-empty, as one extra argument, using bash's `${VAR:+word}`:
   - wget: `wget -qO- ${GITHUB_TOKEN:+--header="Authorization: Bearer $GITHUB_TOKEN"} "https://api.github.com/..."`
   - curl: `curl -s ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} "https://api.github.com/..."`
   When `GITHUB_TOKEN` is unset or empty this expands to nothing, so the call is byte-for-byte the old unauthenticated call.
   Files: `src/etc/connection.sh:5` (curl part only; the inner `wget` of `browser_download_url` stays as is), `src/build/utils.sh:42,86,687`, `src/etc/ci.sh:5`, `src/etc/_ci.sh:23`.

Token creation (user action, documented in the `build.sh` usage text): a fine-grained personal access token with **public repositories read-only** access and no extra permissions is enough; all queried repos are public.

### APKMirror 403

Round 1: an investigation task reproduces `get_apk` for YouTube Music arm64-v8a and, as a control, YouTube, inside the running builder, in a scratch work dir under `local/work/` (gitignored), with `version` preset so no GitHub API call is needed. It records, per hop, which service served the page and the HTTP status, and for the final download the status with (a) the current wget call, (b) wget with real cookies from CloudflareBypass `/cookies`, and (c) CloudflareBypass mirror mode. Report: `docs/logs/2026-10-12-apkmirror-403-investigation.md`, with a classification A/B/C/D (or other) and a proposed `get_apk` change.

Round 2: one task applies the proposed change to `src/build/utils.sh`, limited to `_cfb_get` and the download step of `get_apk`. Allowed mechanisms: FlareSolverr, CloudflareBypass (`/cookies`, `/html`, mirror mode). Cause A's fix (read real cookies) is applied regardless of the root cause, because it is a confirmed bug in the CloudflareBypass fallback. GitHub Actions behavior stays valid: CI runs the same sidecars on the default ports.

**No honest fix rule:** if the investigation shows cause C (or any cause not fixable with the existing sidecars), no workaround is added. "Done" for run 3 is then: run 3 FAIL in the end-to-end summary, the investigation report shows the evidence (statuses for all three download variants, at least 2 attempts each, at least 10 minutes apart), and runs 1 and 2 PASS.

### Review-and-fix of b16a2c6

Each round 1 task reviews the `b16a2c6` files it owns against the original spec's "Files", "Upstream changes" and AC1–AC7, fixes any gap, and lists what it checked. Known items to check:
- `build.sh` runs `compose up -d --build` on every invocation (re-fetches the cfbypass git context). Keep or drop `--build`; justify.
- `build.sh` PASS rule (exit 0 and ≥1 APK) and the `connection.sh` gate match the workflow.
- Run output is only on the terminal. Add nothing unless needed for AC3 below (the runner redirects output to a log file).

## File changes

| File | Change |
|---|---|
| `src/etc/connection.sh` | Token header on the curl call (line 5). |
| `src/etc/ci.sh`, `src/etc/_ci.sh` | Token header on the wget call. |
| `src/build/utils.sh` | Round 1: token header at lines 42, 86, 687. Round 2: `_cfb_get` cookie fix and `get_apk` download fix from the investigation. |
| `local/docker-compose.yml` | Read-only token bind mount (above). Review-and-fix. |
| `local/build.sh` | Token file bootstrap, in-container export, status line, usage text. Review-and-fix. |
| `local/Dockerfile`, `local/Dockerfile.dockerignore` | Review-and-fix only. |
| `docs/logs/2026-10-12-apkmirror-403-investigation.md` | New investigation report. |
| `docs/logs/2026-10-12-local-docker-build-e2e.log` | Runner's end-to-end log. `docs/logs/` is untracked today; the lead decides whether to commit it after AC3 passes. |

The original spec's AC6 ("upstream diff touches only `utils.sh` and `.gitignore`") is replaced by AC9 below.

## Acceptance criteria

Each is checked by a command; the reviewer quotes the output line.

1. **Token header present when set, absent when unset.** With stub `wget` and `curl` first on `PATH` that append their argv to a file: running `bash src/etc/connection.sh` and sourcing `src/build/utils.sh` in a scratch dir and calling `dl_gh morphe-patches MorpheApp latest` and `dl_gh morphe-patches MorpheApp prerelease` with `GITHUB_TOKEN=dummy-not-a-token` logs `Authorization: Bearer dummy-not-a-token` for every `api.github.com` URL; with `GITHUB_TOKEN` unset, the log contains no `Authorization`. On each of the four `src/` files, `grep 'api.github.com' FILE | grep -vc 'GITHUB_TOKEN:+'` prints 0 (header on the same line as every API URL).
2. **Mount is read-only and correctly placed.** `docker compose -f local/docker-compose.yml config` shows the builder bind with `target: /run/secrets/github_token`, `read_only: true`, `create_host_path: false`, and still no `ports:`/`published`. In the running builder: `test -r /run/secrets/github_token` exits 0 and `touch /run/secrets/github_token` fails.
3. **Token never leaks.** After the end-to-end run, with a non-empty token file: `grep -cF -f "$RV_GITHUB_TOKEN_FILE" <run log>` prints 0 (the token never appears on a command line or screen); `docker image history --no-trunc rv-local-builder` and `docker inspect rv-local-builder-1 --format '{{json .Config.Env}}'` contain no `GITHUB_TOKEN`; `git grep -n 'github_pat_\|ghp_'` is empty.
4. **Authenticated in practice.** With a valid token file, `build.sh`'s status line says `authenticated` and in-container `rate_limit` reports `limit` ≥ 1000. With an empty token file, the log of `./local/build.sh morphe 2` shows `GitHub API: unauthenticated` (PASS of that run not required: the shared IP quota may be 0).
5. **Bootstrap.** The bootstrap runs before argument handling. With `RV_GITHUB_TOKEN_FILE` pointing to a non-existent path under a temp dir, `./local/build.sh` (no args) creates it as a regular file with mode `600` (`stat -f %Lp` → `600`), not a directory.
6. **Investigation report exists and is evidenced.** `docs/logs/2026-10-12-apkmirror-403-investigation.md` contains, for YouTube Music arm64-v8a and for YouTube: which service served each page, and HTTP status for download variants (a), (b), (c), at least 2 attempts each; the wget 0-byte-file check result; one classification; and the proposed change or the "no honest fix" statement.
7. **CloudflareBypass cookie bug fixed.** `grep -n 'x-cf-bypasser-cookies' src/build/utils.sh` returns nothing; `_cfb_get` sets `FS_COOKIES` and `user_agent` from `/cookies` JSON (`.cookies` map joined as `k=v; ...`, `.user_agent`). A run of `_cfb_get` against a live APKMirror page inside the builder yields `FS_COOKIES` containing `=`.
8. **End-to-end.** A dedicated runner runs `./local/build.sh morphe > <log> 2>&1; echo RC=$? >> <log>` detached with the token mounted (no pipe, so RC is `build.sh`'s own). Summary shows `PASS morphe run 1` and `PASS morphe run 2`; run 3 is `PASS`, or `FAIL` with criterion 6 showing a "no honest fix" classification. `apksigner verify` RC=0 on every APK in `local/out/morphe/` produced by this run.
9. **Upstream diff bounded.** `git diff --name-only fb56773 HEAD -- src .github` lists only `src/build/utils.sh`, `src/etc/connection.sh`, `src/etc/ci.sh`, `src/etc/_ci.sh`. `bash -n` exits 0 on each.
10. **Original spec still holds.** Original AC2, AC3, AC4, AC5, AC7 re-checked after round 2 with quoted output.

## Known risks

- **Token in process arguments.** `wget --header=...` / `curl -H ...` put the token in the process argv inside the container, visible to `ps` there. Accepted: the builder runs only these scripts. Not visible on the host command line or in logs.
- **Bind-mounted file and editors.** A bind mount of a single file pins the inode. If the user replaces the file (most editors write a new file), the running container keeps the old content until it is recreated. `build.sh` usage text tells the user to run `docker compose -f local/docker-compose.yml up -d --force-recreate builder` after changing the token, or to write it with `printf %s TOKEN > file` (same inode).
- **Invalid token lockout.** GitHub temporarily rejects all auth after repeated invalid credentials. The status line shows `token rejected` so the user can fix it before the run burns attempts.
- **CloudflareBypass mirror mode memory.** If cause B is the fix, the mirror buffers the full APK in memory; the cfbypass container needs enough memory (~2× APK size).
- **APKMirror may still block the WARP exit IP** (cause C). Then run 3 stays FAIL by design (see the "no honest fix" rule).
- **GitHub quota during round 1.** The shared WARP IP is at 0/60 until the token lands. Round 1 work avoids GitHub API calls (investigation presets `version`; checks use stubs).

## Shortcuts (declared)

- Header added inline at 6 call sites instead of a shared helper: `connection.sh` and `ci.sh` do not source `utils.sh`, and `utils.sh` has side effects on source (mkdir, downloads), so a shared helper would mean a new upstream file for a one-token expansion. Reason: design-improving move (fewer files). Ticket/owner: TBD by lead.

## Token location (user decision, supersedes the default above where they differ)

The user stores the token at `src/keystore/github_token` inside the repo. Lead added it to `.gitignore` and set mode 600. The builder's existing repo bind mount (`..:/repo`) also exposes it at `/repo/src/keystore/github_token`; this is acceptable (still a volume mount, never in the image: `local/Dockerfile.dockerignore` excludes everything except the requirements file). Acceptance adds: `git check-ignore -q src/keystore/github_token` exits 0, and `git log --all -- src/keystore/github_token` is empty.
