# Job 298 (com.hinnka.mycamera, Google Play) – apkeep downloads nothing

Root cause: universal item → APKEEP_MULTI_DEVICE `mi_a1` (SDK 29). With that device profile Play serves nothing and apkeep 1.1.0 exits 0 without printing any error. The same account works with SDK 34/35 profiles. The app is free (Play listing offer price 0).

| cmd (in relume-app, ini 0600 in key dir, deleted after: ini_exists_after False) | RC | decisive output |
|---|---|---|
| job 298 log (redacted) | 0 | `device=mi_a1` / `Downloading com.hinnka.mycamera from Google Play...`, nothing after |
| apkeep 1.1.0 -o device=mi_a1,split_apk=true | 0 | files [], stderr empty |
| apkeep -o device=px_9a,split_apk=true | 0 | `com.hinnka.mycamera downloaded successfully!`, base + config.arm64_v8a/en/xxhdpi |
| apkeep -o device=nothing_p1,split_apk=true | 0 | downloaded successfully, same splits |

Profile SDK/ABIs (rs-google-play device.properties): mi_a1 29 arm64+v7a; nothing_p1 34 arm64+v7a; px_9a 35 arm64.
minSdk > 29 is inferred (aapt is not in the image), not measured.

Fix: APKEEP_MULTI_DEVICE = nothing_p1. The failure message now names the device and says apkeep downloads nothing when Play does not offer the app to that profile.
Gate (Linux, relume-gate, HEAD 8efda44 + diff, local/webui diff shasum 0d78678b…): python 379/0 failed, shell 25/0, skipped 0, wall 8.5 s, RC=0.
Leftover: probe download dirs /tmp/tmp*/com.hinnka.mycamera inside relume-app (removal command was denied). They are gone when the container restarts.
