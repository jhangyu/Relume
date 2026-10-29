# apkeep Android source picker (contract item 3)

- AppSettingsScreen.kt: "Download source" chips (APKMirror / APKPure / Google Play (apkeep), URL only if the app already uses kind url). Save sends variants with every download.kind set (withKind). APKMirror listing field only for apkmirror, APKPure field for apkmirror/apkpure, Google Play hint (latest only, credentials in web Settings).
- Kind is a raw string in the variants JSON, so "googleplay" parses with no Models.kt change. MarketParseTest does not cover kinds.
- A5: `ANDROID_HOME=$HOME/Library/Android/sdk python3 scripts/test.py --legs android` RC=0 (self-captured). Decisive: "android: 43 tests, 0 failed, skipped: 0". Code state: base 93c4f57 plus this commit.
