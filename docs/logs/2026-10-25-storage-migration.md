# Storage migration: named volumes → host dirs local/data + local/key (runbook, nothing executed)

Goal: the Portainer stack (compose project `relume`, container `relume-app`) stores its state in two host directories instead of the named volumes `relume_data`, `relume_work`, `relume_apk-cache`, `relume_upstream`:

- `RELUME_DATA_DIR=/Users/jhangyu/project/relume/local/data`: everything that regrows from an empty folder (database, logs, artifacts, APK outputs, apk-cache, upstream downloads, work, icons). Deleting it is safe: the app and Compose recreate it.
- `RELUME_KEY_DIR=/Users/jhangyu/project/relume/local/key` (new): what must survive. The adb key (`HOME` is now `/private/key`, so `local/key/.android/adbkey*`) and the GitHub token entered on the Settings page (`local/key/github_token`). Mounted at `/private/key` inside the root-only `/private` tmpfs, so the build runner uid cannot reach it (same isolation as before).

Code change: `local/docker-compose-portainer.yml` and `local/docker-compose.yml` (both binds, `RELUME_KEY_DIR` required in the Portainer file with `create_host_path: false`), `local/portainer.env`, `local/webui/{db,settings,__main__}.py` (`db.KEY`, env `RELUME_KEY`).
Host `local/data` does not exist now (deleted on purpose); Compose creates it and its `work`, `apk-cache`, `upstream` on first deploy, but the migration below fills it first.
Run every command in a terminal on the Mac that runs Docker Desktop.
`D=/Users/jhangyu/project/relume/local/data`, `K=/Users/jhangyu/project/relume/local/key`, `IMG=busybox:1.38.0-musl`.

## Inventory of non-regrowable state

| What | Where (before → after) | Code | Regrowable |
|---|---|---|---|
| adb key pair + `adb_usb.ini`, known hosts | `<DATA>/home/.android` → `<KEY>/.android` (via `HOME`) | `local/docker-compose*.yml` `HOME`; adb reads `$HOME/.android` | No: every paired phone must be authorized again |
| GitHub token (Settings page) | `<DATA>/secrets/github_token` → `<KEY>/github_token` | `local/webui/settings.py` `ui_token_path` | No (retype it); empty in the live stack today |
| Optional token file mount | `RELUME_GITHUB_TOKEN_FILE` → `/run/secrets/github_token` | `settings.py` `TOKEN_PATH`; only `docker-compose.yml` mounts it | Host file, outside DATA already |
| Admin password hash, sessions, device tokens, signer pins, device pairing records, settings, catalog edits, schedules | database `<DATA>/webui.db` | `local/webui/schema/*.sql` | No, but the user accepts a regrown DB (admin account is created again on first open) |
| Icons | `<DATA>/icons` | `catalog.py:768` | Yes (re-fetched/rebuilt) |
| Artifacts, logs, apk-cache, upstream, work | `<DATA>/...` | `build.py`, `jobs.py`, `upstream.py` | Yes (rebuilt / re-downloaded) |

Nothing else under DATA is written by the app (grep of `write_text`, `open(..., 'w')`, `os.open` in `local/webui/*.py`: only job/options JSON in `work`, `apks.json` in a temp dir, and the token file).

## Read-only findings (volumes, taken earlier)

| Volume | Size | Entries (find count) | Notes |
|---|---|---|---|
| relume_data | 2.0G | 198 | `webui.db` 675840 B + `-wal` 4.1 MB + `-shm`, `artifacts logs icons home secrets`, plus empty dirs `work apk-cache upstream` |
| relume_work | 0 | 1 | empty |
| relume_apk-cache | 624.5M | 14 | 8 package dirs owned by uid 10001 |
| relume_upstream | 437.4M | 88 | dirs `1..28` owned by root |

`relume_data/secrets` is empty (no UI GitHub token in the live stack). `relume_data/work|apk-cache|upstream` are empty mount points of the other three volumes.

## 1. Stop the stack

Portainer > Stacks > relume > Stop this stack (keeps Portainer's stack record). Check (expect no output):

```
docker ps --filter name=relume- --format '{{.Names}}'
```

## 2. Copy the volumes into the new layout (volumes mounted read-only)

`home` and `secrets` do not go to DATA: `home` becomes the key dir, `secrets` content (if any) becomes `K/github_token`.

```
D=/Users/jhangyu/project/relume/local/data
K=/Users/jhangyu/project/relume/local/key
IMG=busybox:1.38.0-musl
mkdir -p "$D" "$K" && chmod 700 "$K"
docker run --rm -v relume_data:/from:ro -v "$D":/to $IMG sh -c 'for e in $(ls -A /from); do case $e in home|secrets) ;; *) cp -a "/from/$e" /to/ ;; esac; done'; echo RC=$?
docker run --rm -v relume_work:/from:ro -v "$D/work":/to $IMG sh -c 'cp -a /from/. /to/'; echo RC=$?
docker run --rm -v relume_apk-cache:/from:ro -v "$D/apk-cache":/to $IMG sh -c 'cp -a /from/. /to/'; echo RC=$?
docker run --rm -v relume_upstream:/from:ro -v "$D/upstream":/to $IMG sh -c 'cp -a /from/. /to/'; echo RC=$?
docker run --rm -v relume_data:/from:ro -v "$K":/to $IMG sh -c 'cp -a /from/home/. /to/ && if [ -s /from/secrets/github_token ]; then cp -a /from/secrets/github_token /to/github_token; fi'; echo RC=$?
```

All five must print `RC=0`. `cp -a /from/. ` includes dotfiles (`.android`). The empty `work`, `apk-cache`, `upstream` directories are copied by the first command, then filled by the next three.
On Docker Desktop macOS, copied files show up on the host owned by your user and read as root in the container; no chown needed. (Linux host: `sudo chown -R RELUME_UID:RELUME_GID "$D" "$K"` and `chmod 0770 "$D/work" "$D/apk-cache"`, see the header of `local/docker-compose-portainer.yml`.)

## 3. Verify the copy (all must match)

Entry counts and sizes (the key part is compared separately):

```
D=/Users/jhangyu/project/relume/local/data
K=/Users/jhangyu/project/relume/local/key
IMG=busybox:1.38.0-musl
for v in work apk-cache upstream; do
  echo "== relume_$v"
  docker run --rm -v relume_$v:/v:ro $IMG sh -c 'echo "volume: $(du -s /v | cut -f1) KB, $(find /v | wc -l) entries"'
  docker run --rm -v "$D/$v":/v:ro $IMG sh -c 'echo "hostdir: $(du -s /v | cut -f1) KB, $(find /v | wc -l) entries"'
done
echo "== relume_data minus home and secrets"
docker run --rm -v relume_data:/v:ro $IMG sh -c 'find /v -mindepth 1 -not -path /v/home -not -path "/v/home/*" -not -path /v/secrets -not -path "/v/secrets/*" -not -path "/v/work/*" -not -path "/v/apk-cache/*" -not -path "/v/upstream/*" | wc -l'
docker run --rm -v "$D":/v:ro $IMG sh -c 'find /v -mindepth 1 -not -path "/v/work/*" -not -path "/v/apk-cache/*" -not -path "/v/upstream/*" | wc -l'
echo "== key: home vs key dir (add 1 for github_token only if it existed)"
docker run --rm -v relume_data:/v:ro $IMG sh -c 'find /v/home -mindepth 1 | wc -l; ls -la /v/home/.android'
docker run --rm -v "$K":/v:ro $IMG sh -c 'find /v -mindepth 1 | wc -l; ls -la /v/.android'
```

Per volume the `volume:` and `hostdir:` lines must show the same entry count (`du` may differ by a few KB from block rounding). The two `relume_data` counts must be equal, and the key counts must be equal (the `adbkey` and `adbkey.pub` files present in both listings).

Database integrity and row counts on a scratch copy (the copy must include the `-wal` file):

```
D=/Users/jhangyu/project/relume/local/data
V=$(mktemp -d) && cp "$D"/webui.db "$D"/webui.db-wal "$D"/webui.db-shm "$V"/
sqlite3 "$V/webui.db" 'PRAGMA integrity_check;'
sqlite3 "$V/webui.db" 'select count(*) from item; select count(*) from source; select count(*) from upstream;'
rm -r "$V"
docker run --rm -v relume_data:/v:ro keinos/sqlite3 sh -c 'mkdir /t && cp /v/webui.db* /t/ && sqlite3 /t/webui.db "select count(*) from item; select count(*) from source; select count(*) from upstream;"'
```

Expected: `ok`, then three counts, identical to the three counts from the volume. Do not continue if any number differs.

## 4. Point the Portainer stack at the new layout and redeploy

1. Portainer > Stacks > relume > Editor: replace the whole content with `local/docker-compose-portainer.yml` from this repo (paste as is).
2. Environment variables: set `RELUME_DATA_DIR` = `/Users/jhangyu/project/relume/local/data` and add `RELUME_KEY_DIR` = `/Users/jhangyu/project/relume/local/key` (keep `RELUME_KEYSTORE_DIR`, `RELUME_NETWORK_MODE`, `RELUME_WEB_PORT`, `RELUME_UID`, `RELUME_GID`, `TZ`; `local/portainer.env` holds the full set).
3. "Update the stack". Without `RELUME_KEY_DIR` the deploy fails with `required variable RELUME_KEY_DIR is missing a value`; a missing `local/key` directory fails the deploy too (on purpose: the adb key is never silently recreated).
4. Docker Desktop must share `/Users/jhangyu/project` (Settings > Resources > File sharing; `/Users` is shared by default).

Post-deploy checks:

```
docker ps --filter name=relume- --format '{{.Names}} {{.Status}}'
docker inspect relume-app --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
docker exec relume-app sh -c 'echo HOME=$HOME; ls -la $HOME/.android; adb devices'
```

Expected: five containers up, `relume-app` healthy; mounts are `bind` only, including `local/key -> /private/key`; `HOME=/private/key`, `adbkey` and `adbkey.pub` listed. Then in the web UI (http://localhost:47000 in bridge mode): the item list is as before, an old job log opens, and a previously paired phone is still connected and usable WITHOUT a new "Allow USB/wireless debugging" prompt on the phone. That last check proves the key was preserved. If the phone asks to authorize again, stop and do not remove the volumes.

If anything fails: stop the stack, repoint it at the old compose (the volumes still hold the original data, untouched), and report.

## 5. Cleanup (only after step 4 is confirmed OK by the user)

List first, then delete.

```
docker volume ls --filter name=relume_ --format '{{.Name}}'
docker ps -a --filter volume=relume_data --format '{{.Names}}'
```

Expected volumes exactly: `relume_apk-cache relume_data relume_upstream relume_work`; the second command prints nothing (no container uses them). Then:

```
docker volume rm relume_data relume_work relume_apk-cache relume_upstream
```

Afterwards back up `local/key` (it is the only copy of the adb key); `local/data` needs no backup.
