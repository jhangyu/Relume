# Local Docker Build Follow-up — Implementation Plan

> Written by hand following the podium `writing-plans` format (no Skill tool in the authoring session). Steps use checkbox syntax.

**Spec (contract):** `docs/superpowers/specs/2026-10-12-local-docker-build-followup-design.md` — acceptance criteria AC1–AC10 live there. Task split and owners: `docs/superpowers/plans/2026-10-12-local-docker-build-followup-execution.md`.

**Goal:** Authenticated GitHub API calls via a read-only mounted token file, an evidenced fix (or honest write-up) for the APKMirror 403 on YouTube Music, and one full `./local/build.sh morphe` run.

**Architecture:** Host file → compose read-only bind at `/run/secrets/github_token` → `build.sh` exports `GITHUB_TOKEN` inside the in-container `bash -c` → each `api.github.com` call adds `${GITHUB_TOKEN:+<auth header>}`. APKMirror: investigate in a scratch dir inside the builder, then patch `_cfb_get` / the `get_apk` download step only.

**Interface names (frozen; every task uses these exactly):** `RV_GITHUB_TOKEN_FILE` (host, default `$HOME/.config/rv-local/github_token`), `/run/secrets/github_token` (container, read-only), `GITHUB_TOKEN` (container env), header `Authorization: Bearer $GITHUB_TOKEN`.

**Shared rules for all tasks**
- Never write a real token anywhere. Tests use `dummy-not-a-token`.
- Round 1 tasks do not run `docker compose up`, `build`, or `down` (the stack from `b16a2c6` stays as is for the investigation). One-off checks use `docker run --rm` on the existing `rv-local-builder` image or `docker compose ... exec` on the running stack.
- Host bash is 3.2; run bash-level tests inside the builder image.
- Git: explicit `git add <own files>` and `git commit -- <own files>` on `main`. No stash/reset/checkout --/clean/add -A.

---

## Task 1 — Token header in upstream scripts (round 1)

**Files:** `src/etc/connection.sh`, `src/etc/ci.sh`, `src/etc/_ci.sh`, `src/build/utils.sh` (only lines 42, 86, 687 in round 1).
**Behavior:** every `api.github.com` request carries `Authorization: Bearer $GITHUB_TOKEN` when the variable is non-empty; unchanged otherwise. No other line changes. Also review the `b16a2c6` hunks in `utils.sh` (pup arch, two port variables) against original spec "Upstream changes".
**Acceptance:** spec AC1; `bash -n` on the four files; the `utils.sh` diff vs `fb56773` is only the `b16a2c6` hunks plus the three header insertions.

- [ ] Write the check first (it must fail before the change). Save as `local/work/token-test/run.sh` (gitignored scratch):
  ```bash
  #!/bin/bash
  # usage: run.sh <expect: set|unset>
  set -u
  d=/repo/local/work/token-test; rm -rf "$d/w" "$d/bin" "$d/argv.log"; mkdir -p "$d/w" "$d/bin"
  for t in wget curl; do printf '#!/bin/sh\necho "%s $*" >> %s/argv.log\n' "$t" "$d" > "$d/bin/$t"; chmod +x "$d/bin/$t"; done
  cd "$d/w" && ln -s /repo/src src
  export PATH="$d/bin:$PATH" GITHUB_OUTPUT="$d/w/out"
  [ "$1" = set ] && export GITHUB_TOKEN=dummy-not-a-token || unset GITHUB_TOKEN
  bash src/etc/connection.sh >/dev/null
  bash src/etc/ci.sh MorpheApp/morphe-patches latest youtube-morphe.apk >/dev/null 2>&1
  bash -c 'source src/build/utils.sh >/dev/null 2>&1; dl_gh morphe-patches MorpheApp latest; dl_gh morphe-patches MorpheApp prerelease' >/dev/null 2>&1
  api=$(grep -c 'api.github.com' "$d/argv.log"); auth=$(grep 'api.github.com' "$d/argv.log" | grep -c 'Authorization: Bearer dummy-not-a-token')
  echo "api_calls=$api with_auth=$auth any_auth=$(grep -c Authorization "$d/argv.log")"
  ```
- [ ] Run it red: `docker run --rm -v "$PWD:/repo" rv-local-builder bash /repo/local/work/token-test/run.sh set` → expect `with_auth=0` while `api_calls>=4`.
- [ ] Edit each call site (header on the same line as the URL):
  - `src/etc/connection.sh:5`: `curl -s ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} "https://api.github.com/repos/MorpheApp/morphe-patches/releases/tags/v1.21.1"` (leave the outer `wget -q $(...)` of the asset URL unchanged).
  - `src/build/utils.sh:42`: `releases=$(wget -qO- ${GITHUB_TOKEN:+--header="Authorization: Bearer $GITHUB_TOKEN"} "https://api.github.com/repos/$owner/$repo/releases")`
  - `src/build/utils.sh:86`: `wget -qO- ${GITHUB_TOKEN:+--header="Authorization: Bearer $GITHUB_TOKEN"} "https://api.github.com/repos/$2/$repo/releases/$tags" \`
  - `src/build/utils.sh:687`: same pattern on the `iyear/tdl` call.
  - `src/etc/ci.sh:5`, `src/etc/_ci.sh:23`: same wget pattern.
- [ ] Run green: `... run.sh set` → `with_auth == api_calls`; `... run.sh unset` → `any_auth=0`.
- [ ] `for f in src/etc/connection.sh src/etc/ci.sh src/etc/_ci.sh src/build/utils.sh; do bash -n $f && grep 'api.github.com' $f | grep -vc 'GITHUB_TOKEN:+'; done` → every count `0`.
- [ ] `grep -n 'echo.*GITHUB_TOKEN\|set -x' src/etc/connection.sh src/etc/ci.sh src/build/utils.sh` → no new hits.
- [ ] Commit: `git add src/etc/connection.sh src/etc/ci.sh src/etc/_ci.sh src/build/utils.sh && git commit -m "feat: authenticate GitHub API calls when GITHUB_TOKEN is set" -- src/etc/connection.sh src/etc/ci.sh src/etc/_ci.sh src/build/utils.sh`

## Task 2 — Compose token mount + image review (round 1)

**Files:** `local/docker-compose.yml`, `local/Dockerfile`, `local/Dockerfile.dockerignore`.
**Behavior:** builder gets the read-only bind from the spec "Token path" step 2. Review all three files against original spec "Files" (packages, venv, no ports, shared network namespace, pinned cfbypass commit, arm64) and fix gaps.
**Acceptance:** compose half of spec AC2 (config output); no `ports:`/`published`; Dockerfile/dockerignore unchanged unless a gap is found and named.

- [ ] Check red: `RV_GITHUB_TOKEN_FILE=/tmp/x docker compose -f local/docker-compose.yml config | grep -c /run/secrets/github_token` → `0`.
- [ ] Add under `builder.volumes` (keep `..:/repo`):
  ```yaml
      - type: bind
        source: ${RV_GITHUB_TOKEN_FILE:-${HOME}/.config/rv-local/github_token}
        target: /run/secrets/github_token
        read_only: true
        bind:
          create_host_path: false
  ```
  Add a comment line above it: token file is created by `build.sh`; `create_host_path: false` stops Docker from creating a directory there.
- [ ] Check green: `RV_GITHUB_TOKEN_FILE=/tmp/x docker compose -f local/docker-compose.yml config` shows `source: /tmp/x`, `target: /run/secrets/github_token`, `read_only: true`, `create_host_path: false`; `... config | grep -c 'published\|ports:'` → `0`. Also without the variable: `source:` ends with `/.config/rv-local/github_token`.
- [ ] Review Dockerfile/dockerignore against original spec; write findings into the commit message body (one line each, "no change" allowed).
- [ ] Commit with pathspec on the files actually changed.

## Task 3 — build.sh token wiring + review (round 1)

**Files:** `local/build.sh`, `.gitignore`.
**Behavior (spec "Token path" steps 1, 3, 4):**
- [ ] Before argument handling (right after `cd`), add:
  ```bash
  # GitHub token: host file, bind-mounted read-only at /run/secrets/github_token (docker-compose.yml).
  # Never read or printed on the host. Empty file = unauthenticated (60 req/h per IP).
  export RV_GITHUB_TOKEN_FILE=${RV_GITHUB_TOKEN_FILE:-$HOME/.config/rv-local/github_token}
  [ -e "$RV_GITHUB_TOKEN_FILE" ] || { mkdir -p "$(dirname "$RV_GITHUB_TOKEN_FILE")" && (umask 077 && : >"$RV_GITHUB_TOKEN_FILE"); }
  # Expanded inside the builder only (single quotes): the docker command line never carries the value.
  tok='[ -s /run/secrets/github_token ] && export GITHUB_TOKEN=$(tr -d "[:space:]" </run/secrets/github_token);'
  ```
- [ ] Usage text (no-arg branch): add three lines — token file path, "fine-grained token, public repositories read-only", and "after changing the token: `printf %s TOKEN > file`, or `docker compose -f local/docker-compose.yml up -d --force-recreate builder`".
- [ ] After the FlareSolverr readiness check, print the status line once:
  ```bash
  "${compose[@]}" exec -T builder bash -c "$tok"'
  	code=$(curl -s -o /tmp/rl.json -w "%{http_code}" ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} https://api.github.com/rate_limit)
  	if [ "$code" = 401 ]; then echo "GitHub API: token rejected (HTTP 401)"
  	else echo "GitHub API: $([ -n "${GITHUB_TOKEN:-}" ] && echo authenticated || echo unauthenticated) remaining=$(jq -r .rate.remaining /tmp/rl.json 2>/dev/null)"; fi' </dev/null
  ```
- [ ] Prefix the per-run command string with `$tok`: `"$tok bash src/etc/connection.sh && grep -q 'internet_error=0' github_output && bash src/build/$script $arg"`.
- [ ] Review the rest of `build.sh` and `.gitignore` against original spec ("Files" → `local/build.sh`, AC5, AC7). Decide on `up -d --build` per invocation (keep or drop; one-line reason in the commit body). `.gitignore` needs no token entry (file lives outside the repo) — confirm, do not add speculative entries.
- [ ] Checks: `bash -n local/build.sh`; `RV_GITHUB_TOKEN_FILE=$(mktemp -d)/sub/tok ./local/build.sh >/dev/null; stat -f '%HT %Lp' <that path>` → `Regular File 600`; `./local/build.sh | grep -c '^  [a-z]'` → 18 orgs still listed; `./local/build.sh nosuch; echo $?` → `2`; `grep -n 'GITHUB_TOKEN' local/build.sh` shows no `-e GITHUB_TOKEN`, no `echo $GITHUB_TOKEN`.
- [ ] Commit with pathspec.

## Task 4 — APKMirror 403 investigation (round 1)

**Files:** `docs/logs/2026-10-12-apkmirror-403-investigation.md` (new; commit it). Scratch scripts under `local/work/apkm-probe/` (gitignored, not committed).
**Constraints:** use the running stack via `docker compose -f local/docker-compose.yml exec -T builder ...`; do not restart it. No GitHub API calls: preset `version` (e.g. the version in `apps.json` `example_url`, or the newest non-beta from the APKMirror list page) so `detect_version` is skipped (`utils.sh:189`). No edits to `src/`.
**Acceptance:** spec AC6.

- [ ] Make a probe dir `local/work/apkm-probe/<n>` with `src` symlink (as `build.sh` does), `source src/build/utils.sh`, set `version=...`, then call `get_apk` for (1) `com.google.android.apps.youtube.music` `youtube-music-arm64-v8a` `apk` `arm64-v8a` and (2) `com.google.android.youtube` `youtube` `apk` (control). Capture the full output to a log in the probe dir.
- [ ] Record which service served each page (`FlareSolverr` vs `CFB` log lines; `_FFS_FAILED`), and the final URL, cookies (names only, never values in the report), and user agent.
- [ ] For the final download URL of each, record the HTTP status and size for:
  (a) the current call (`wget -S` with `FS_COOKIES`, `user_agent`, `--referer`),
  (b) wget with cookies + UA from `curl -sG --data-urlencode url=<page> http://localhost:$CFBYPASS_PORT/cookies` (JSON `.cookies`, `.user_agent`),
  (c) CloudflareBypass mirror mode: `curl -s -o f -w '%{http_code} %{size_download}' -H 'x-hostname: www.apkmirror.com' "http://localhost:$CFBYPASS_PORT<final path>"`.
  At least 2 attempts each, ≥10 minutes apart if any variant returns 403.
- [ ] Check the 0-byte question: `wget -nv -O /tmp/z https://httpbin.org/status/403; ls -l /tmp/z` inside the builder.
- [ ] Classify as A (CloudflareBypass cookie count bug), B (wget fingerprint), C (IP/file block), D (arch variant) or other, citing the evidence lines. Write the proposed `utils.sh` change (as a short code block, ≤30 lines) or the "no honest fix" statement into the report.
- [ ] Commit the report with pathspec.

## Task 5 — APKMirror fix in utils.sh (round 2)

**Files:** `src/build/utils.sh` (only `_cfb_get` and the download block of `get_apk`, `utils.sh:505-523` area).
**Input:** Task 4 report. **Acceptance:** spec AC7, plus the Task 4 probe for YouTube Music re-run green (HTTP 200, file size > 10 MB, `unzip -l` lists `AndroidManifest.xml`) — or, for a "no honest fix" classification, only AC7.

- [ ] Always (confirmed bug, spec cause A) — in `_cfb_get`, replace the `x-cf-bypasser-cookies` read with:
  ```bash
  local ck
  ck=$(curl -s --max-time 120 -G --data-urlencode "url=$url" "http://localhost:${CFBYPASS_PORT:-8000}/cookies")
  FS_COOKIES=$(echo "$ck" | jq -r '.cookies // {} | to_entries | map(.key + "=" + .value) | join("; ")')
  cfb_ua=$(echo "$ck" | jq -r '.user_agent // empty')
  ```
  (keep the existing `[[ -n "$cfb_ua" ]] && user_agent="$cfb_ua"`; drop the header-based UA read only if `/cookies` always returns it).
- [ ] If Task 4 shows the 0-byte file: change the success test after the download (`utils.sh:518`) from `-f` to `-s` and delete the empty file on failure. Same fix at the `get_apkpure` twin (`utils.sh:596`) since it shares the bug.
- [ ] Only if Task 4 classifies B: when the wget download fails, retry once through mirror mode (variant (c) command from Task 4), writing to the same `./download/$base_apk`. ponytail comment naming the ceiling: mirror mode buffers the whole APK in cfbypass memory.
- [ ] Checks: `bash -n src/build/utils.sh`; `grep -c x-cf-bypasser-cookies src/build/utils.sh` → `0`; re-run Task 4's probe for YouTube Music and YouTube; Task 1's `run.sh set|unset` still green.
- [ ] Commit with pathspec.

## Task 6 — Stack + probes runner (round 2, run only)

Precondition: the user has written a real token into `$HOME/.config/rv-local/github_token` (lead confirms; the runner never prints it).
- [ ] `docker compose -f local/docker-compose.yml up -d --build > local/work/r2-up.log 2>&1; echo RC=$?` (rebuild may exceed 150 s; detach with `nohup` if so and check the log once within 5 minutes).
- [ ] `docker image inspect -f '{{.Architecture}}' rv-local-builder rv-local-cfbypass:0f628b9` → `arm64` twice.
- [ ] In builder: `curl -s localhost:47191` contains `FlareSolverr is ready!`; `curl -s -o /dev/null -w '%{http_code}' localhost:47800/` ≠ `000`.
- [ ] In builder: `test -r /run/secrets/github_token; echo $?` → `0`; `touch /run/secrets/github_token; echo $?` → non-zero.
- [ ] In builder: `bash -c '[ -s /run/secrets/github_token ] && export GITHUB_TOKEN=$(tr -d "[:space:]" </run/secrets/github_token); curl -s ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} https://api.github.com/rate_limit | jq .rate.limit'` → ≥ 1000.
- [ ] `docker inspect rv-local-builder-1 --format '{{json .Config.Env}}' | grep -c GITHUB_TOKEN` → `0`; `docker image history --no-trunc rv-local-builder | grep -c GITHUB_TOKEN` → `0`.
- [ ] Report raw outputs only.

## Task 7 — End-to-end runner (round 2, run only)

Depends on Tasks 5 and 6.
- [ ] `nohup bash -c './local/build.sh morphe > docs/logs/2026-10-12-local-docker-build-e2e.log 2>&1; echo RC=$? >> docs/logs/2026-10-12-local-docker-build-e2e.log' >/dev/null 2>&1 &` — record PID. Within 5 minutes confirm the log grows and shows `GitHub API: authenticated` and `=== morphe run 1`. Then end the turn; the lead wakes the runner later (no sleep/poll loops).
- [ ] After `RC=` appears: copy the `===== Summary =====` block; `for f in local/out/morphe/*.apk; do docker compose -f local/docker-compose.yml exec -T builder apksigner verify "/repo/$f"; echo "RC=$? $f"; done` (only APKs newer than the log start).
- [ ] `grep -cF -f "$HOME/.config/rv-local/github_token" docs/logs/2026-10-12-local-docker-build-e2e.log` → `0`.
- [ ] Empty-token check (AC4 second half): `f=$(mktemp); RV_GITHUB_TOKEN_FILE=$f docker compose -f local/docker-compose.yml up -d builder` then `RV_GITHUB_TOKEN_FILE=$f ./local/build.sh morphe 2 > local/work/r2-notoken.log 2>&1` (detached as above); `grep 'GitHub API:' local/work/r2-notoken.log` → `unauthenticated`. Then restore with `docker compose -f local/docker-compose.yml up -d builder` (default file).
- [ ] `git status --porcelain` (only `docs/logs/` expected untracked); `git diff --name-only fb56773 HEAD -- src .github`.

## Self-review

- Spec coverage: AC1 → T1; AC2 → T2 + T6; AC3 → T6 + T7; AC4 → T3 + T6 + T7; AC5 → T3; AC6 → T4; AC7 → T5; AC8 → T7; AC9 → T1/T5 + T7; AC10 → T6 + T7 + reviewer.
- Interface names appear identically in T1, T2, T3, T6 (`RV_GITHUB_TOKEN_FILE`, `/run/secrets/github_token`, `GITHUB_TOKEN`, `Bearer`).
- Open: Task 5's third step is conditional on Task 4; its exact code depends on the report.
