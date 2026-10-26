#!/usr/bin/env bash
# Fake runner: success with two variants. $1 = job.json, cwd = work dir.
echo "::step::version"
echo "::version::20.1.0"
echo "::apkm_versions::20.1.0,20.0.9"
echo "::step::patch::1/2"
mkdir -p release
printf 'u' > release/universal.apk
printf 'a64' > release/arm64-v8a.apk
echo "::step::verify"
cat > result.json <<'JSON'
{"app_version": "20.1.0", "patches_version": "v1.2.3", "cli_version": "v1.0.0", "variants": [
 {"name": "universal", "abi": "universal", "file": "release/universal.apk", "size": 1, "sha256": "aa", "package": "com.google.android.youtube", "version_name": "20.1.0", "version_code": 1, "signer_sha256": "bb"},
 {"name": "arm64-v8a", "abi": "arm64-v8a", "file": "release/arm64-v8a.apk", "size": 3, "sha256": "cc", "package": "com.google.android.youtube", "version_name": "20.1.0", "version_code": 1, "signer_sha256": "bb"}]}
JSON
