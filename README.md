<h1 align="center">Relume</h1>
<p align="center">Automated, no-root patching of Android apps with ReVanced, ReVanced Extended, Morphe and other patch sets.</p>

> "…that Promethean heat that can thy light relume." — Othello. Relume re-lights official apps: it downloads the official APK, applies community patches, and hands the patched APK back to you.

---

## What it does

- Downloads the official APK of each app in the catalog, patches it with the configured patch source, signs it, and keeps the result as an artifact.
- Apps and patch sources are defined in a catalog (`local/webui/catalog-seed.json`): YouTube, YouTube Music, Reddit, TikTok, Twitter/X, Instagram, Proton Mail/VPN and many more, each tied to a patch source such as Morphe, ReVanced Extended (anddea), Piko or Dropped Patches.
- Runs as a local Docker build with a Web UI (`local/`), using the build code in `src/build/`.
- The Android companion app is released by pushing a tag `companion-vX.Y.Z`: CI builds and publishes it.

> [!NOTE]
> For YouTube, YouTube Music or Google Photos you need [MicroG RE](https://github.com/MorpheApp/MicroG-RE) installed on the phone.
>
> Beta builds use development patches, not beta APKs. They share the package name of the stable build, so installing one overwrites the other.

## Run locally with Docker (Web UI)

The stack is defined in `local/docker-compose.yml`: the Web UI and builder (`app`), plus FlareSolverr, a Cloudflare-bypass browser and an APKMirror solver as sidecars, all sharing one network namespace.

```sh
cp local/.env.example local/.env        # set RELUME_NETWORK_MODE (host on Linux, bridge on macOS); on Linux also RELUME_UID and RELUME_GID
mkdir -p local/data/work local/data/apk-cache local/data/upstream
docker compose -f local/docker-compose.yml up -d --build
```

Open `http://localhost:47000` (in bridge mode the port is `RELUME_WEB_PORT`). All settings are listed in `local/.env.example`; the data directory (database, logs, artifacts, caches) is `RELUME_DATA_DIR`, default `local/data`.

On first start, open the Web UI and create the admin account (username + password). Forgot it: set `RELUME_ADMIN_PASSWORD` (at least 10 characters) in the `app` service environment and restart the container. At boot the admin password becomes that value and every browser session is signed out. Phone device tokens keep working. Remove the variable afterwards. It does nothing before the admin account exists. Web UI pages: app catalog (patches, options and version pin per app), builds, schedules, devices and settings. Helper command: `docker exec relume-app python3 -m webui export-seed`.

### Portainer

`local/docker-compose-portainer.yml` is the same stack for a Portainer web-editor stack: data lives in named volumes and the code is baked into the images `jhangyu/relume-app` and `jhangyu/relume-browser` (built from `local/Dockerfile`, published by `scripts/release.py`). Signing keystores are never in an image: set `RELUME_KEYSTORE_DIR` to a host directory holding them (see the top of that file).

## Android app

`android/` holds the companion app (Kotlin, Compose). It talks to the Web UI with a per-device token, installs the built APKs on the phone itself and reports its inventory back. The `Android` workflow runs only on a tag `companion-vX.Y.Z`: it builds the release APK (no tests in CI), signs it with the committed `src/keystore/lspatch-signing.p12` and publishes `relume-companion.apk` to the public repo `jhangyu/relume-companion`. Its only secret is the publish token `RELUME_COMPANION_RELEASE_TOKEN`. Local build: `ANDROID_HOME=<sdk> android/gradlew -p android assembleDebug --no-watch-fs`.

## Tests

`python3 scripts/test.py [--legs python,shell,android]` (macOS and Linux only) runs everything (default all three legs). The python and shell legs run on Linux in Docker: `docker build -f local/Dockerfile --target app -t relume-gate . && docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell`. The android leg needs `ANDROID_HOME`. The run fails when the suite takes over 90 s, a test takes over 1 s, a test source contains `sleep` or `delay(`, or a requested leg's tool is missing; skipped tests are counted per leg.

## More

- [docs/superpowers/specs/2026-10-19-webui-design.md](docs/superpowers/specs/2026-10-19-webui-design.md): Web UI and build design.
- [docs/Frequently Asked Questions.md](docs/Frequently%20Asked%20Questions.md): common questions.

## License

GPL-3.0, see [LICENSE](LICENSE). The patched apps belong to their original authors; please support them.

Relume builds on [FiorenMas/Revanced-And-Revanced-Extended-Non-Root](https://github.com/FiorenMas/Revanced-And-Revanced-Extended-Non-Root).

---

### ⚠️ Library, resources using in this repository:

<details>
  <summary>inotia00</summary>

[Patches](https://github.com/inotia00/revanced-patches) - [GPL-3.0](https://github.com/inotia00/revanced-patches/blob/revanced-extended/LICENSE)

[X Shim](https://gitlab.com/inotia00/x-shim) - [GPL-3.0](https://gitlab.com/inotia00/x-shim/-/blob/main/LICENSE)

[Patches arsclib](https://github.com/inotia00/revanced-patches-arsclib) - [GPL-3.0](https://github.com/inotia00/revanced-patches-arsclib/blob/arsclib-old/LICENSE)

[Integrations](https://github.com/inotia00/revanced-integrations) - [GPL-3.0](https://github.com/inotia00/revanced-integrations/blob/main/LICENSE)

[CLI](https://github.com/inotia00/revanced-cli) - [GPL-3.0](https://github.com/inotia00/revanced-cli/blob/main/LICENSE)

[CLI arsclib](https://github.com/inotia00/revanced-cli-arsclib) - [GPL-3.0](https://github.com/inotia00/revanced-cli-arsclib/blob/arsclib-old/LICENSE)

</details>

<details>
  <summary>anddea</summary>

[Patches](https://github.com/anddea/revanced-patches) - [GPL-3.0](https://github.com/anddea/revanced-patches/blob/main/LICENSE)

[Integrations](https://github.com/anddea/revanced-integrations) - [GPL-3.0](https://github.com/anddea/revanced-integrations/blob/main/LICENSE)

</details>

<details>
  <summary>Morphe</summary>

[Patches](https://github.com/MorpheApp/morphe-patches) - [GPL-3.0](https://github.com/MorpheApp/morphe-patches/blob/main/LICENSE)

[CLI](https://github.com/MorpheApp/morphe-cli) - [GPL-3.0](https://github.com/MorpheApp/morphe-cli/blob/main/LICENSE)

</details>

<details>
  <summary>crimera</summary>

[Patches](https://github.com/crimera/piko) - [GPL-3.0](https://github.com/crimera/piko/blob/main/LICENSE)

[Integrations](https://github.com/crimera/revanced-integrations) - [GPL-3.0](https://github.com/crimera/revanced-integrations/blob/main/LICENSE)

</details>

<details>
  <summary>kitadai31</summary>

[Patches](https://github.com/kitadai31/revanced-patches-android6-7) - [GPL-3.0](https://github.com/kitadai31/revanced-patches-android6-7/blob/revanced-extended/LICENSE)

[Integrations](https://github.com/kitadai31/revanced-integrations) - [GPL-3.0](https://github.com/kitadai31/revanced-integrations/blob/revanced-extended/LICENSE)

</details>

<details>
  <summary>d4n3436</summary>

[Patches](https://github.com/d4n3436/revanced-patches-android5) - [GPL-3.0](https://github.com/d4n3436/revanced-patches-android5/blob/revanced-extended/LICENSE)

[Integrations](https://github.com/d4n3436/revanced-integrations) - [GPL-3.0](https://github.com/d4n3436/revanced-integrations/blob/revanced-extended/LICENSE)

</details>

<details>
  <summary>BiliRoamingM</summary>

[Patches & Integrations](https://github.com/sakarie9/BiliRoamingM) - [GPL-3.0](https://github.com/sakarie9/BiliRoamingM/blob/main/LICENSE)

</details>

<details>
  <summary>indrastorms</summary>

[Patches](https://github.com/indrastorms/Dropped-Patches) - [GPL-3.0](https://github.com/indrastorms/Dropped-Patches/blob/main/LICENSE)

</details>

<details>
  <summary>Aunali321</summary>

[Patches](https://github.com/Aunali321/ReVancedExperiments) - [GPL-3.0](https://github.com/Aunali321/ReVancedExperiments/blob/main/LICENSE)

</details>

<details>
  <summary>scrazzz</summary>

[Patches](https://github.com/scrazzz/my-revanced-patches) - [GPL-3.0](https://github.com/scrazzz/my-revanced-patches/blob/main/LICENSE)

</details>

<details>
  <summary>RookieEnough</summary>

[Patches](https://github.com/RookieEnough/De-ReVanced) - [GPL-3.0](https://github.com/RookieEnough/De-ReVanced/blob/main/LICENSE)

</details>

<details>
  <summary>Paresh Maheshwari</summary>

[Patches](https://gitlab.com/Paresh-Maheshwari/paresh-patches) - [GPL-3.0](https://gitlab.com/Paresh-Maheshwari/paresh-patches/-/blob/main/LICENSE?ref_type=heads)

</details>

<details>
  <summary>gnadgnaoh</summary>

[Patches](https://github.com/gnadgnaoh/NexAlloy) - [GPL-3.0](https://github.com/gnadgnaoh/NexAlloy/blob/main/LICENSE)

</details>

<details>
  <summary>icysymmetra</summary>

[Patches](https://github.com/icysymmetra/tiktok-patches-for-morphe) - [GPL-3.0](https://github.com/icysymmetra/tiktok-patches-for-morphe/blob/main/LICENSE)

</details>

<details>
  <summary>docbt</summary>

[Patches](https://github.com/docbt/patched-up) - [GPL-3.0](https://github.com/docbt/patched-up/blob/main/LICENSE)

</details>

<details>
  <summary>rushiranpise</summary>

[Patches](https://github.com/rushiranpise/morphe-patches) - [GPL-3.0](https://github.com/rushiranpise/morphe-patches/blob/main/LICENSE)

</details>

<details>
  <summary>arandomhooman</summary>

[Patches](https://github.com/arandomhooman/hoomans-morphe-patches) - [GPL-3.0](https://github.com/arandomhooman/hoomans-morphe-patches/blob/main/LICENSE)

</details>

<details>
  <summary>bufferk</summary>

[Patches](https://github.com/bufferk/morphe-patches) - [GPL-3.0](https://github.com/bufferk/morphe-patches/blob/main/LICENSE)

</details>

<details>
  <summary>Revanced</summary>

[CLI](https://github.com/revanced/revanced-cli) - [GPL-3.0](https://github.com/revanced/revanced-cli/blob/main/LICENSE)

</details>

[AuroraStore](https://gitlab.com/AuroraOSS/AuroraStore) - [GPL-3.0](https://gitlab.com/AuroraOSS/AuroraStore/-/blob/master/LICENSE)


[pup](https://github.com/ericchiang/pup) - [MIT](https://github.com/ericchiang/pup/blob/master/LICENSE)

[APKEditor](https://github.com/REAndroid/APKEditor) - [Apache-2.0](https://github.com/REAndroid/APKEditor/blob/master/LICENSE)

[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) - [MIT](https://github.com/FlareSolverr/FlareSolverr/blob/master/LICENSE)


---
