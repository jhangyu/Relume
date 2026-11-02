#!/usr/bin/env bash
# Fake runner with an APKMirror version of several variants (design variant §7). $1 = job.json, cwd = work dir.
# job.json "_fake": {codes: [variant versionCodes in page order], patchable: [codes some patch applies to],
#   refuse: true = an unpatchable variant makes the patcher exit non-zero instead of applying 0 patches,
#   serves: a code the remembered page serves instead of its own, noresult: true = no patcher result file,
#   log: file the used code is appended to}
set -e
fx=$(cd "$(dirname "$0")/../apkmanifest" && pwd)/signed.apk   # a real signed APK: the server reads its info
job=$1
f() { jq -r "._fake.$1 // empty" "$job"; }
d=$(jq -c '.recipe.variants[0].download' "$job")
pick=$(jq -c --argjson d "$d" 'first(.apkmirror_picks[]? | select(.download == $d)) // {}' "$job")
code=$(jq -r '.version_code // empty' <<<"$pick")
remaining=null
if [ -n "$code" ]; then
	[ -n "$(f serves)" ] && code=$(f serves)
else
	left=$(jq -c --argjson p "$pick" '[._fake.codes[] | select(. as $c | ($p.exclude_codes // []) | index($c) | not)]' "$job")
	code=$(jq -r '.[0] // empty' <<<"$left")
	remaining=$(jq length <<<"$left")
	[ -n "$code" ] || { echo "::fail::download_failed::no variant left"; exit 1; }
fi
printf '%s ' "$code" >> "$(f log)"
if [ -z "$(jq -r '.version_codes // empty' <<<"$d")" ]; then   # automatic mode only (build-item.sh rule)
	echo "::apkm_variant::{\"download\":$d,\"version_code\":$code,\"version_name\":\"1.0.0\",\"variant_url\":\"https://www.apkmirror.com/v/$code/\",\"bundle\":false,\"remaining\":$remaining}"
fi
pick_code=$(jq -r '.version_code // empty' <<<"$pick")
if [ -n "$pick_code" ] && [ "$pick_code" != "$code" ]; then
	echo "::fail::download_failed::APKMirror download does not match the request: expected versionCode $pick_code, downloaded $code"
	exit 1
fi
applied='[]'
jq -e --argjson c "$code" '._fake.patchable | index($c)' "$job" >/dev/null && applied='["p"]'
if [ "$applied" = '[]' ] && [ "$(f refuse)" = true ]; then
	echo "::fail::patch_failed::patcher exited 1 on $code"
	exit 1
fi
mkdir -p release
for v in universal arm64-v8a; do
	cp "$fx" "release/$v.apk"
	[ "$(f noresult)" = true ] || echo "{\"appliedPatches\":$applied,\"failedPatches\":[{\"name\":\"q\",\"error\":\"no fit for $code\"}]}" > "release/$v.result.json"
done
cat > result.json <<JSON
{"app_version": "1.0.0", "patches_version": "v1.2.3", "cli_version": "v1.0.0", "variants": [
 {"name": "universal", "abi": "universal", "file": "release/universal.apk"},
 {"name": "arm64-v8a", "abi": "arm64-v8a", "file": "release/arm64-v8a.apk"}]}
JSON
