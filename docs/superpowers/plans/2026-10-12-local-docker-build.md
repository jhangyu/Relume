# Local Docker Build — Implementation Plan

Spec: `docs/superpowers/specs/2026-10-12-local-docker-build-design.md` (contract; acceptance criteria live there).

## Verified facts (checked in source, 2026-10-12)

- `src/build/utils.sh:6` hardcodes `pup_v0.4.0_linux_amd64.zip`; upstream also ships `linux_arm64`.
- `utils.sh:211` FlareSolverr `localhost:8191`, `utils.sh:244` CloudflareBypass `localhost:8000`.
- Build scripts source `./src/build/utils.sh` and write `release/`, `download/`, jars, `pup` into cwd → per-run work dir with a `src` symlink to the repo's `src`.
- Only `src/etc/ci.sh` reads `$repository` (out of scope). `connection.sh` writes `internet_error=` to `$GITHUB_OUTPUT`.
- `utils.sh:633` calls `python` (not `python3`) → builder needs `python-is-python3`.
- FlareSolverr reads `PORT` env (`src/flaresolverr.py:95`).
- CloudflareBypassForScraping (pinned `0f628b9ccbea9ab69ca61bc1c896c2ef766a0f06`): port via `server.py --port` (default 8000); Dockerfile base `ubuntu:rolling` (multi-arch).
- CloakBrowser 0.6.1 `config.py:194` maps `("Linux","aarch64") → linux-arm64` with a pinned binary → arm64 source build is viable; no fork needed, so compose builds straight from the pinned git URL (no `local/cfbypass/` dir).

## Tasks

1. `src/build/utils.sh`: pup asset by `uname -m`; ports via `${FLARESOLVERR_PORT:-8191}` / `${CFBYPASS_PORT:-8000}`.
2. `.gitignore`: `local/out/`, `local/work/`.
3. `local/Dockerfile`: ubuntu:24.04 + spec packages + `python-is-python3`, venv with ggplay_dl requirements; `sleep infinity`.
4. `local/docker-compose.yml`: flaresolverr (`PORT=47191`), cfbypass (git-context build, `--port 47800`), builder (`network_mode: service:flaresolverr`, repo at `/repo`, env ports). No `ports:`.
5. `local/build.sh`: org table from `manual-patch.yml`; `<org> [run]` / `all`; per run: clean `local/work/<org>-<run>`, `connection.sh` gate, run script, copy `release/*.apk` → `local/out/<org>/`; PASS/FAIL summary; non-zero exit on failure.
6. Verify: build images (separately, to fit foreground timeout), `./local/build.sh` (list), `./local/build.sh morphe 1`, apksigner/jarsigner verify, curl both ports, `docker compose config` has no `published`, `docker image inspect` arch, `git status`.
