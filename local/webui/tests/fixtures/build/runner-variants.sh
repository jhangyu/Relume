#!/usr/bin/env bash
# Fake runner with an APKMirror version of several variants (design variant §7). $1 = job.json, cwd = work dir.
# job.json "_fake": {codes: [variant versionCodes in page order], patchable: [codes some patch applies to],
#   refuse: true = an unpatchable variant makes the patcher exit non-zero instead of applying 0 patches,
#   serves: a code the remembered page serves instead of its own, noresult: true = no patcher result file,
#   log: file the used code is appended to}
# One jq run reads every job.json value: each process start costs ms, VariantLoopTest runs this up to 3 times a build.
set -e
fx=${BASH_SOURCE[0]%/*}/../apkmanifest/signed.apk   # a real signed APK: the server reads its info
vars=$(jq -r '
	.recipe.variants[0].download as $d
	| ._fake as $f
	| (first(.apkmirror_picks[]? | select(.download == $d)) // {}) as $p
	| [$f.codes[] | select(. as $c | ($p.exclude_codes // []) | index($c) | not)] as $left
	| (if $p.version_code then ($f.serves // $p.version_code) else $left[0] end) as $code
	| {d: ($d | tojson), pick_code: ($p.version_code // ""), code: ($code // ""),
	   remaining: (if $p.version_code then null else ($left | length) end),
	   manual: (if $d.version_codes then 1 else "" end),
	   applied: (if $code != null and ($f.patchable | index($code)) != null then ["p"] else [] end | tojson),
	   refuse: ($f.refuse // false), noresult: ($f.noresult // false), logf: $f.log}
	| to_entries[] | "\(.key)=\(.value | tostring | @sh)"' "$1")
eval "$vars"
[ -n "$code" ] || { echo "::fail::download_failed::no variant left"; exit 1; }
printf '%s ' "$code" >> "$logf"
if [ -z "$manual" ]; then   # automatic mode only (build-item.sh rule)
	echo "::apkm_variant::{\"download\":$d,\"version_code\":$code,\"version_name\":\"1.0.0\",\"variant_url\":\"https://www.apkmirror.com/v/$code/\",\"bundle\":false,\"remaining\":$remaining}"
fi
if [ -n "$pick_code" ] && [ "$pick_code" != "$code" ]; then
	echo "::fail::download_failed::APKMirror download does not match the request: expected versionCode $pick_code, downloaded $code"
	exit 1
fi
if [ "$applied" = '[]' ] && [ "$refuse" = true ]; then
	echo "::fail::patch_failed::patcher exited 1 on $code"
	exit 1
fi
mkdir -p release
for v in universal arm64-v8a; do
	cp "$fx" "release/$v.apk"
	[ "$noresult" = true ] || printf '%s\n' "{\"appliedPatches\":$applied,\"failedPatches\":[{\"name\":\"q\",\"error\":\"no fit for $code\"}]}" > "release/$v.result.json"
done
printf '%s\n' '{"app_version": "1.0.0", "patches_version": "v1.2.3", "cli_version": "v1.0.0", "variants": [
 {"name": "universal", "abi": "universal", "file": "release/universal.apk"},
 {"name": "arm64-v8a", "abi": "arm64-v8a", "file": "release/arm64-v8a.apk"}]}' > result.json
