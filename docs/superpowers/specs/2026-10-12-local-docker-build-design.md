# Local Docker Build — Design

## Goal

Run this repo's patch build scripts (`src/build/*.sh`) on a local Apple Silicon Mac inside Docker,
without GitHub Actions, and collect the patched APKs locally.

## Decisions (user-approved)

| Topic | Decision |
|---|---|
| Scope | Entry script supports one org or `all`. Acceptance requires only `morphe` to succeed end to end. |
| Architecture | Builder runs native `linux/arm64`. |
| usque (WARP SOCKS5 proxy) | Not installed. `ggplay_dl.py:225` falls back to a direct connection when `127.0.0.1:9099` is closed; the host already runs Cloudflare WARP, so container traffic goes through WARP. |
| Version control | New files live in `local/` and are committed. Build outputs and caches are gitignored. |
| Scheduling | None. Manual runs only. |
| Ports | Use 5-digit ports for the two anti-Cloudflare services: FlareSolverr `47191`, CloudflareBypassForScraping `47800`. Do not publish any port to the host. |
| CloudflareBypassForScraping image | Upstream image is single-arch (not arm64). Build it locally for arm64 from source (https://github.com/sarperavci/CloudflareBypassForScraping). If the arm64 build cannot work (for example, its CloakBrowser Chromium has no linux arm64 binary), fall back to the upstream image with `platform: linux/amd64` and report the reason. |

## Files

### `local/Dockerfile` (builder)
- Base `ubuntu:24.04`, native arm64.
- Packages: bash, curl, wget, jq, unzip, zip, tar, git, ca-certificates, openjdk-21-jdk-headless, python3, python3-pip (or venv).
- Python deps from `src/build/helper/ggplay_dl/requirements.txt` (or whatever requirements file `manual-patch.yml` passes as `python_requirements`).

### `local/cfbypass/` (only if the arm64 source build is used)
- Build context or Dockerfile that builds CloudflareBypassForScraping for arm64 from a pinned upstream commit.

### `local/docker-compose.yml`
- `flaresolverr`: `ghcr.io/flaresolverr/flaresolverr:latest` (multi-arch), listening on `47191` (via its `PORT` env).
- `cfbypass`: locally built arm64 image (or amd64 fallback), listening on `47800`.
- `builder`: built from `local/Dockerfile`; `network_mode: service:flaresolverr`. `cfbypass` also joins the same network namespace, so `localhost:47191` and `localhost:47800` both resolve inside the builder.
- Repo bind-mounted into the builder. No `ports:` entries.

### `local/build.sh` (entry point, runs on the host)
- Usage: `./local/build.sh <org>` or `./local/build.sh all`; with no args, print the list of orgs.
- Org table: maps each org name to its `src/build/*.sh` script and matrix values, copied from `.github/workflows/manual-patch.yml`.
- For each org × matrix value, run the script inside the builder with `GITHUB_OUTPUT` set to a temp file and other env the scripts expect (`repository`, etc., as found in the workflow).
- Each run gets its own clean working dir (scripts drop temp files in cwd); copy resulting `release/*.apk` to `local/out/<org>/`.
- One failing org does not stop the others. Print a per-org PASS/FAIL summary at the end. Exit non-zero if any org failed.

## Upstream changes (minimal)

`src/build/utils.sh`:
1. Line 6: choose the pup asset by `uname -m` (`x86_64` → `linux_amd64`, `aarch64` → `linux_arm64`). GitHub Actions behavior is unchanged.
2. Service URLs (`utils.sh:211` FlareSolverr, `utils.sh:244` CloudflareBypass): read ports from env vars with the current values as defaults, e.g. `${FLARESOLVERR_PORT:-8191}` and `${CFBYPASS_PORT:-8000}`. `local/` sets them to `47191` / `47800`. GitHub Actions behavior is unchanged.

No other upstream file changes.

## gitignore

Add: `local/out/`, and any per-run work/cache dirs created under `local/`.

## Acceptance criteria

1. `./local/build.sh morphe` runs to completion and `local/out/morphe/` contains at least one YouTube APK.
2. `apksigner verify` (or `jarsigner -verify`) passes on that APK.
3. Inside the running builder, `curl localhost:47191` and `curl localhost:47800` respond; `docker compose config` shows no published ports.
4. `docker image inspect` shows the builder image is `arm64`; the cfbypass image is `arm64`, or the fallback reason is reported.
5. `./local/build.sh` with no args lists every org from `manual-patch.yml`; `all` iterates all of them (individual org success not required).
6. `git diff` on upstream files touches only `src/build/utils.sh` (the two changes above) and `.gitignore`.
7. After the run, `git status` shows no untracked build artifacts outside gitignored paths.

## Out of scope

- Scheduling, new-version detection, GitHub release upload, Telegram backup (`TDL_BACKUP`).
- Making every org build successfully.
- usque.

## Known risks

- Patcher (morphe-desktop CLI) may bundle an aapt2 without linux arm64 support. If so, stop and report; fallback is running the builder as `linux/amd64` (user decides).
- APK mirror sites may block residential IPs; the failure is reported, not worked around.
