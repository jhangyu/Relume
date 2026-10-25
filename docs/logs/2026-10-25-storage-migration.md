# Storage migration: named volumes → host dir local/data (runbook, nothing executed)

Goal: the Portainer stack (compose project `relume`) stores data in `/Users/jhangyu/project/relume/local/data` instead of the named volumes `relume_data`, `relume_work`, `relume_apk-cache`, `relume_upstream`.
Code change: `local/docker-compose-portainer.yml` (bind mounts under required `RELUME_DATA_DIR`), `local/portainer.env`.
Run every command in a terminal on the Mac that runs Docker Desktop. `D=/Users/jhangyu/project/relume/local/data`.

## Read-only findings (taken before the runbook was written)

| Volume | Size | Entries (find count) | Notes |
|---|---|---|---|---|
| relume_data | 2.0G | 198 | `webui.db` 675840 B + `-wal` 4.1 MB + `-shm`, `artifacts logs icons home secrets`, plus empty dirs `work apk-cache upstream` |
| relume_work | 0 | 1 | empty |
| relume_apk-cache | 624.5M | 14 | 8 package dirs owned by uid 10001 |
| relume_upstream | 437.4M | 88 | dirs `1..28` owned by root |

- Nested paths: `relume_data` contains `work`, `apk-cache`, `upstream` but all three are empty directories (the mount points of the other three volumes). No collision: copy `relume_data` first (creates the empty dirs), then copy the other three over them.
- `relume_data/secrets` is empty, so the UI GitHub token is not set in the live stack (matches "no GitHub token").
- `github_token`: the app reads the token from `<DATA>/secrets/github_token` (UI token, `local/webui/settings.py:35`) and from `/run/secrets/github_token` (optional file mount, `settings.py:13`, fed by `RELUME_GITHUB_TOKEN_FILE`, which only `local/docker-compose.yml` mounts). Nothing reads `<DATA>/github_token`, and the Portainer file has no token mount. Moving to bind mounts therefore does NOT make `local/data/github_token` live. It only goes live if you enter that token on the Settings page (written to `secrets/github_token`). The stale copy is a 93-byte 0600 file; it goes to `local/data.stale-20261025/` in step 2.
- Live container mounts today: `docker inspect relume-app` shows `relume_*` volumes at `/private/data`, `/private/data/{work,apk-cache,upstream}` and `/data/{work,apk-cache,upstream}`.

## 1. Stop the stack

Portainer-managed stack: stop it in Portainer (Stacks > relume > Stop this stack). This is the correct way; Portainer keeps its own stack record and later "Update the stack" works on it. `docker compose -p relume stop` also stops the containers (same project label) but does not work from a directory without the compose file and bypasses Portainer's state, so use the Portainer button.

Check (expect no output):

```
docker ps --filter name=relume- --format '{{.Names}}'
```

## 2. Move the stale host data aside (do not delete yet)

```
D=/Users/jhangyu/project/relume/local/data
mv "$D" "$D.stale-20261025" && mkdir "$D" && ls -ld "$D" "$D.stale-20261025"
```

(`local/data.stale-*/` is gitignored.)

## 3. Copy the volumes into the new layout

Order matters: data first, then the three subdirectory volumes. `cp -a` keeps owner, mode, timestamps and symlinks. Volumes are mounted read-only.

```
D=/Users/jhangyu/project/relume/local/data
IMG=busybox:1.38.0-musl
docker run --rm -v relume_data:/from:ro -v "$D":/to $IMG sh -c 'cp -a /from/. /to/'; echo RC=$?
docker run --rm -v relume_work:/from:ro -v "$D/work":/to $IMG sh -c 'cp -a /from/. /to/'; echo RC=$?
docker run --rm -v relume_apk-cache:/from:ro -v "$D/apk-cache":/to $IMG sh -c 'cp -a /from/. /to/'; echo RC=$?
docker run --rm -v relume_upstream:/from:ro -v "$D/upstream":/to $IMG sh -c 'cp -a /from/. /to/'; echo RC=$?
```

All four must print `RC=0`. Dotfiles (`home/.android`) are included by `/from/.`.
Note: on Docker Desktop macOS, files copied into a bind mount show up on the host owned by your user whatever uid they had in the container; inside the container they read as root again. That is expected and needs no chown. (On a Linux host run `sudo chown -R RELUME_UID:RELUME_GID "$D"` and `chmod 0770 "$D/work" "$D/apk-cache"` afterwards, see the header of `local/docker-compose-portainer.yml`.)

## 4. Verify the copy (all must match)

Sizes and entry counts per source vs target:

```
D=/Users/jhangyu/project/relume/local/data
IMG=busybox:1.38.0-musl
for v in data:"" work:/work apk-cache:/apk-cache upstream:/upstream; do
  n=${v%%:*}; sub=${v#*:}
  echo "== relume_$n"
  docker run --rm -v relume_$n:/v:ro $IMG sh -c 'echo "volume: $(du -s /v | cut -f1) KB, $(find /v | wc -l) entries"'
  docker run --rm -v "$D$sub":/v:ro $IMG sh -c 'echo "hostdir: $(du -s /v | cut -f1) KB, $(find /v | wc -l) entries"'
done
```

Expected: `data` host dir = volume totals (it includes the three subdirectories, so its host numbers equal data + work + apk-cache + upstream; compare that sum), `work`/`apk-cache`/`upstream` equal their volumes. Entry counts must match exactly (host-side `du` may differ by a few KB from filesystem block rounding; `find` counts must not).

Database integrity and row counts, on a scratch copy so the live files stay untouched (the copy must include the `-wal` file):

```
D=/Users/jhangyu/project/relume/local/data
V=$(mktemp -d) && cp "$D"/webui.db "$D"/webui.db-wal "$D"/webui.db-shm "$V"/
sqlite3 "$V/webui.db" 'PRAGMA integrity_check;'
sqlite3 "$V/webui.db" 'select count(*) from item; select count(*) from source; select count(*) from upstream;'
rm -r "$V"
```

Expected: `ok`, then three counts. Get the same three numbers from the volume to compare:

```
docker run --rm -v relume_data:/v:ro keinos/sqlite3 sh -c 'mkdir /t && cp /v/webui.db* /t/ && sqlite3 /t/webui.db "select count(*) from item; select count(*) from source; select count(*) from upstream;"'
```

(`keinos/sqlite3` is already pulled on this machine.) Do not continue if any number differs.

## 5. Point the Portainer stack at the new layout and redeploy

1. Portainer > Stacks > relume > Editor: replace the whole content with `local/docker-compose-portainer.yml` from this repo (commit hash of this change; paste the file as is).
2. Environment variables: add exactly `RELUME_DATA_DIR` = `/Users/jhangyu/project/relume/local/data` (keep the existing `RELUME_KEYSTORE_DIR`, `RELUME_NETWORK_MODE`, `RELUME_WEB_PORT`, `RELUME_UID`, `RELUME_GID`, `TZ`; `local/portainer.env` holds the full set).
3. Click "Update the stack" (redeploy). Without `RELUME_DATA_DIR` the deploy fails with `required variable RELUME_DATA_DIR is missing a value`.
4. Docker Desktop must be allowed to share `/Users/jhangyu/project` (Settings > Resources > File sharing; `/Users` is shared by default).

Post-deploy checks:

```
docker ps --filter name=relume- --format '{{.Names}} {{.Status}}'
docker inspect relume-app --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
```

Expected: all five containers up, `relume-app` healthy; mounts show `bind /Users/jhangyu/project/relume/local/data[/work|/apk-cache|/upstream] -> ...` and no `volume` entries. Then open the web UI (http://localhost:47000 in bridge mode): the item list shows the same items as before, and open one old build's log (Jobs > a finished job > log) and see its text.

If anything fails: stop the stack, repoint it at the old compose (named volumes still hold the original data, untouched), and report.

## 6. Cleanup (only after step 5 is confirmed OK by the user)

List first, then delete.

```
docker volume ls --filter name=relume_ --format '{{.Name}}'
du -sh /Users/jhangyu/project/relume/local/data.stale-20261025
ls -la /Users/jhangyu/project/relume/local/data.stale-20261025
```

Expected volumes exactly: `relume_apk-cache relume_data relume_upstream relume_work`. They must not be used by any container (`docker ps -a --filter volume=relume_data` shows nothing after the redeploy). Then:

```
docker volume rm relume_data relume_work relume_apk-cache relume_upstream
rm -r /Users/jhangyu/project/relume/local/data.stale-20261025
```

The stale directory also holds old `webui.db.bak-*` backups; they are older than the volume data. Keep anything you still want before the `rm`.
