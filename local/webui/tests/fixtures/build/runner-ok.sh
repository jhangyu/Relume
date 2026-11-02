#!/usr/bin/env bash
# Fake runner: success with two variants. $1 = job.json, cwd = work dir.
fx=$(cd "$(dirname "$0")/../apkmanifest" && pwd)/signed.apk   # a real signed APK: the server reads its info
echo "::step::version"
echo "::version::20.1.0"
echo "::apkm_versions::20.1.0,20.0.9"
echo "::step::patch::1/2"
mkdir -p release
cp "$fx" release/universal.apk
cp "$fx" release/arm64-v8a.apk
echo "::step::verify"
cat > result.json <<'JSON'
{"app_version": "20.1.0", "patches_version": "v1.2.3", "cli_version": "v1.0.0", "variants": [
 {"name": "universal", "abi": "universal", "file": "release/universal.apk"},
 {"name": "arm64-v8a", "abi": "arm64-v8a", "file": "release/arm64-v8a.apk"}]}
JSON
