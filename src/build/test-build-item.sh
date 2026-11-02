#!/usr/bin/env bash
# Runner self-check (spec build §8). Offline: wget/curl/java/apksigner are shims.
# Usage: bash src/build/test-build-item.sh   (exit 0 = all PASS)
# shellcheck disable=SC2034,SC2154  # globals are read by the sourced scripts
set -o pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
FIX=$REPO/src/build/test-fixtures
YT=com.google.android.youtube
fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1: $2"; fails=$((fails + 1)); }

new_env() {
	T=$(mktemp -d)
	export SHIM_LOG=$T/shim.log SHIM_SERVE=$T/serve SHIM_FIXTURES=$FIX
	export PATH="$FIX/bin:$PATH" RELUME_APKEDITOR=$T/APKEditor.jar
	unset SHIM_PATCH_FAIL SHIM_EMPTY
	mkdir -p "$SHIM_SERVE" "$T/work" "$T/up" "$T/cache" "$T/m"
	printf '<manifest/>' > "$T/m/AndroidManifest.xml"
	printf 'x' > "$T/m/classes.dex"
	(cd "$T/m" && zip -q "$SHIM_SERVE/input.apk" AndroidManifest.xml classes.dex)
	: > "$T/up/patches-1.0.0.mpp"
	: > "$T/up/morphe-desktop-1.0.0-all.jar"
	: > "$RELUME_APKEDITOR"
}

# make_job <jq filter>: fixture job.json with paths rewritten, plus options.json
make_job() {
	sed -e "s#@WORK@#$T/work#g" -e "s#@UP@#$T/up#g" "$FIX/job.json" | jq "$1" > "$T/job.json"
	printf '[{"patches":{}}]' > "$T/work/options.json"
}

# cache_f: jq filter giving the job the server's cache entry for its (single) download spec
cache_f() { echo ".apk_cache=[{download: .recipe.variants[0].download, file: \"$T/cache/$YT/1.0.0-apk-any-any.apk\"}]"; }

# ---- utils.sh cases ----
new_env
d=$(mktemp -d)
(cd "$d" && source "$REPO/src/build/utils.sh")
if [ -z "$(ls -A "$d")" ]; then pass utils_source_no_side_effects; else fail utils_source_no_side_effects "$(ls -A "$d")"; fi

out=$(source "$REPO/src/build/utils.sh" && apk_info "$SHIM_SERVE/input.apk")
if grep -qE '^package=[A-Za-z][A-Za-z0-9_.]+$' <<<"$out" && grep -qE '^version_code=[0-9]+$' <<<"$out" && grep -qE '^version_name=.+' <<<"$out"; then pass apk_info_capture; else fail apk_info_capture "$out"; fi

out=$(source "$REPO/src/build/utils.sh" && apk_signer "$SHIM_SERVE/input.apk")
if grep -qE '^[0-9a-f]{64}$' <<<"$out"; then pass apk_signer_capture; else fail apk_signer_capture "$out"; fi

out=$(cd "$T/work" && mkdir -p release && source "$REPO/src/build/utils.sh" &&
	engine=morphe cli_jar=$T/up/morphe-desktop-1.0.0-all.jar keystore=morphe.keystore RELUME_REPO=$REPO &&
	patches_files=("$T/up/patches-1.0.0.mpp") &&
	patch_variant "$SHIM_SERVE/input.apk" release/x.apk arm64-v8a && echo OK)
log=$(cat "$SHIM_LOG")
if [ "$out" = OK ] && [ -s "$T/work/release/x.apk" ] && grep -q -- "--striplibs arm64-v8a" <<<"$log" &&
	grep -q -- "--options-file options.json" <<<"$log" && grep -q -- "--keystore=$REPO/src/keystore/morphe.keystore" <<<"$log" &&
	grep -q -- "-p $T/up/patches-1.0.0.mpp" <<<"$log" && grep -q -- "--continue-on-error" <<<"$log" &&
	grep -q -- "-r release/x.result.json --out=release/x.apk" <<<"$log"; then
	pass patch_variant_argv
else fail patch_variant_argv "$out / $log"; fi

mkdir -p "$T/abi/lib/armeabi-v7a" "$T/abi/lib/arm64-v8a"
: > "$T/abi/lib/armeabi-v7a/libx.so"
: > "$T/abi/lib/arm64-v8a/libx.so"
(cd "$T/abi" && zip -q "$T/v7.apk" lib/armeabi-v7a/libx.so && zip -q "$T/both.apk" lib/armeabi-v7a/libx.so lib/arm64-v8a/libx.so)
out=$(source "$REPO/src/build/utils.sh" && check_abis "$T/v7.apk" pkg arm64-v8a; echo "rc=$?")
ok=$(source "$REPO/src/build/utils.sh" && check_abis "$T/both.apk" pkg arm64-v8a && check_abis "$SHIM_SERVE/input.apk" pkg arm64-v8a && echo OK)
if grep -q 'expected ABIs \[arm64-v8a\], found \[armeabi-v7a\]' <<<"$out" && grep -q 'rc=1' <<<"$out" && [ "$ok" = OK ]; then
	pass check_abis
else fail check_abis "$out / $ok"; fi

# apkm_candidates: hand-written variants table (rows 209 Android 9 480dpi, 203 480dpi, 202 120-640dpi, 203 640dpi,
# all BUNDLE arm64-v8a; 204 APK armeabi-v7a nodpi): order, exclusion, manual codes, type fallback
cand() { (source "$REPO/src/build/utils.sh" && apkm_candidates "$@" < "$FIX/apkm-variants.html" 2>/dev/null | cut -f2,3 | paste -sd' ' -); }
out="$(cand BUNDLE '' '' '' arm64-v8a '' '')|$(cand BUNDLE '' '' '' arm64-v8a '' '202 203')|$(cand BUNDLE '' '' '' arm64-v8a '203' '')|$(cand APK '' '' '' arm64-v8a '' '')"
want=$'202\tBUNDLE 203\tBUNDLE 203\tBUNDLE 209\tBUNDLE 204\tAPK|209\tBUNDLE 204\tAPK|203\tBUNDLE 203\tBUNDLE|204\tAPK 202\tBUNDLE 203\tBUNDLE 203\tBUNDLE 209\tBUNDLE'
if [ "$out" = "$want" ]; then pass apkm_candidates; else fail apkm_candidates "$out"; fi

out=$(source "$REPO/src/build/utils.sh" && read_version "$SHIM_SERVE/input.apk" && match_version APKMirror "" "1 1561173354" &&
	echo "ok=$dl_code" && match_version APKMirror "" "7 8"; echo "rc=$? $LAST_ERR")
if grep -q '^ok=1561173354$' <<<"$out" && grep -qxF 'rc=1 APKMirror download does not match the request: expected versionName any versionCode 7 or 8, downloaded versionName 21.20.405 versionCode 1561173354' <<<"$out"; then
	pass match_version
else fail match_version "$out"; fi

# solver chain: a shell function curl stands in for both services; calls are logged to $T/calls
solver_case() {   # FS_REPLY = FlareSolverr JSON, CFB_CODE / CFB_BODY = CFBypass /html answer, $1 = number of _cf_get calls
	(source "$REPO/src/build/utils.sh"
	curl() {
		local o="" a="$*"
		echo "$a" >> "$T/calls"
		if [[ $a == *"/v1"* ]]; then printf '%s' "$FS_REPLY"; return 0; fi
		if [[ $a == *"/cookies"* ]]; then echo '{}'; return 0; fi
		while [[ $# -gt 0 ]]; do [[ $1 == -o ]] && o=$2; shift; done
		printf '%s' "$CFB_BODY" > "$o"
		printf '%s' "$CFB_CODE"
	}
	for _ in $(seq 1 "$1"); do _cf_get https://www.apkmirror.com/x; echo "rc=$? html=$html"; done) 2>&1
}
new_env; : > "$T/calls"
out=$(FS_REPLY='{"status":"error","message":"Challenge not solved"}' CFB_CODE=200 CFB_BODY='<html>ok</html>' solver_case 2)
if grep -q 'FlareSolverr: Challenge not solved' <<<"$out" && [ "$(grep -c 'rc=0 html=<html>ok</html>' <<<"$out")" = 2 ] &&
	[ "$(grep -c '/v1' "$T/calls")" = 1 ] && [ "$(grep -c '/html' "$T/calls")" = 2 ]; then
	pass cf_get_switches_solver
else fail cf_get_switches_solver "$out / $(cat "$T/calls")"; fi

new_env; : > "$T/calls"
out=$(FS_REPLY='{"status":"error","message":"Challenge not solved"}' CFB_CODE=503 CFB_BODY='busy' solver_case 1)
if grep -q '^rc=1' <<<"$out" && [ "$(grep -c '/v1' "$T/calls")" = 1 ] && [ "$(grep -c '/html' "$T/calls")" = 1 ] &&
	grep -qF '[-] FlareSolverr and CFBypass both failed on https://www.apkmirror.com/x: FlareSolverr: Challenge not solved; CFBypass: HTTP 503: busy' <<<"$out"; then
	pass cf_get_both_fail_once_each
else fail cf_get_both_fail_once_each "$out / $(cat "$T/calls")"; fi

# ---- runner cases (Task P3.3) ----
run_runner() { (bash "$REPO/src/build/build-item.sh" "$T/job.json" > "$T/out.txt" 2>&1); echo $?; }
fail_lines() { grep -c '^::fail::' "$T/out.txt"; }

new_env; make_job '.'
rc=$(run_runner)
r=$T/work/result.json
if [ "$rc" = 0 ] && [ "$(jq '.variants|length' "$r")" = 2 ] &&
	[ "$(jq -r '.variants[0].file' "$r")" = release/universal.apk ] &&
	[ -s "$T/work/release/universal.apk" ] && [ -s "$T/work/release/arm64-v8a.apk" ] &&
	[ "$(jq -r '.app_version' "$r")" = 1.0.0 ] && [ "$(jq -r '.patches_version' "$r")" = v1.2.3 ] &&
	jq -e '.variants[0].package|length>0' "$r" >/dev/null && jq -e '.variants[0].version_code|type=="number"' "$r" >/dev/null &&
	jq -re '.variants[0].signer_sha256' "$r" | grep -qE '^[0-9a-f]{64}$' &&
	grep -q '^::step::patch::2/2$' "$T/out.txt" && grep -q '^::version::1.0.0$' "$T/out.txt" &&
	[ -z "$(ls -A "$T/cache")" ] && [ "$(grep -c -- '--striplibs arm64-v8a' "$SHIM_LOG")" = 1 ]; then
	pass runner_ok
else fail runner_ok "rc=$rc $(tail -5 "$T/out.txt")"; fi

new_env; make_job '.'; rm "$SHIM_SERVE/input.apk"
rc=$(run_runner)
if [ "$rc" != 0 ] && [ "$(fail_lines)" = 1 ] && grep -q '^::fail::download_failed::' "$T/out.txt"; then pass runner_missing_input; else fail runner_missing_input "rc=$rc $(tail -3 "$T/out.txt")"; fi

new_env; make_job '.'
rc=$(run_runner)
if [ "$rc" = 0 ] && ! grep -q 'apkmirror.com/uploads' "$SHIM_LOG"; then pass runner_no_list_page; else fail runner_no_list_page "rc=$rc"; fi

new_env; make_job '.'; export SHIM_PATCH_FAIL=1
rc=$(run_runner); unset SHIM_PATCH_FAIL
if [ "$rc" != 0 ] && [ "$(fail_lines)" = 1 ] && grep -q '^::fail::patch_failed::' "$T/out.txt"; then pass runner_patch_failed; else fail runner_patch_failed "rc=$rc"; fi

new_env; make_job '.version.value=null'
rc=$(run_runner)
if [ "$rc" = 0 ] && [ "$(grep -c '^::version::' "$T/out.txt")" = 1 ] && [ "$(jq -r '.app_version' "$T/work/result.json")" != 1.0.0 ] &&
	[ "$(jq -r '.app_version' "$T/work/result.json")" != "" ]; then
	pass runner_version_null_takes_downloaded
else fail runner_version_null_takes_downloaded "rc=$rc $(grep '^::' "$T/out.txt" | head -5)"; fi

new_env; make_job '.recipe.variants[].download.kind="apkmirror"'
rc=$(run_runner)
if [ "$rc" != 0 ] && grep -q '^::fail::apk_not_listed::' "$T/out.txt"; then pass runner_apk_not_listed; else fail runner_apk_not_listed "rc=$rc"; fi

# googleplay: the server's apkeep output (one <pkg>.apk, or a split dir <pkg>/) is named in job.json
GP='.recipe.variants[].download={"kind":"googleplay","type":"apk","arch":null,"dpi":null,"minver":null,"url":null}'
gp_job() { make_job "$GP | .version.value=$1 | .googleplay=[{download:.recipe.variants[0].download, path:\"$2\", version_code:$3}]"; }
new_env; mkdir -p "$T/gp"; cp "$SHIM_SERVE/input.apk" "$T/gp/$YT.apk"; gp_job null "$T/gp/$YT.apk" null
rc=$(run_runner)
if [ "$rc" = 0 ] && [ -s "$T/work/download/in-0.apk" ] && [ "$(jq -r .app_version "$T/work/result.json")" = 21.20.405 ]; then
	pass googleplay_single_apk
else fail googleplay_single_apk "rc=$rc $(tail -3 "$T/out.txt")"; fi

new_env; mkdir -p "$T/gp/$YT"; cp "$SHIM_SERVE/input.apk" "$T/gp/$YT/$YT.apk"; cp "$SHIM_SERVE/input.apk" "$T/gp/$YT/$YT.config.arm64_v8a.apk"
gp_job '"21.20.405"' "$T/gp/$YT" 1561173354
rc=$(run_runner)
if [ "$rc" = 0 ] && [ -s "$T/work/download/in-0.apk" ] && grep -q "java -jar $T/APKEditor.jar m -i ./download/in-0.xapk" "$SHIM_LOG" &&
	unzip -Z1 "$T/work/download/in-0.apk" | grep -qxF "$YT.config.arm64_v8a.apk"; then
	pass googleplay_split_dir
else fail googleplay_split_dir "rc=$rc $(tail -3 "$T/out.txt")"; fi

new_env; mkdir -p "$T/gp"; cp "$SHIM_SERVE/input.apk" "$T/gp/$YT.apk"; gp_job '"1.0.0"' "$T/gp/$YT.apk" 7
rc=$(run_runner)
if [ "$rc" != 0 ] && [ "$(fail_lines)" = 1 ] &&
	grep -qxF '::fail::download_failed::Google Play download does not match the request: expected versionName 1.0.0 versionCode 7, downloaded versionName 21.20.405 versionCode 1561173354' "$T/out.txt"; then
	pass googleplay_version_mismatch
else fail googleplay_version_mismatch "rc=$rc $(grep '^::fail::' "$T/out.txt")"; fi

new_env; mkdir -p "$T/gp"; cp "$SHIM_SERVE/input.apk" "$T/gp/$YT.apk"; gp_job '"21.20.405"' "$T/gp/$YT.apk" 7
rc=$(run_runner)
if [ "$rc" != 0 ] && [ "$(fail_lines)" = 1 ] &&
	grep -qxF '::fail::download_failed::Google Play download does not match the request: expected versionName 21.20.405 versionCode 7, downloaded versionName 21.20.405 versionCode 1561173354' "$T/out.txt"; then
	pass googleplay_code_mismatch
else fail googleplay_code_mismatch "rc=$rc $(grep '^::fail::' "$T/out.txt")"; fi

# ---- Xposed engine cases (P8, spec xposed 5.3 / 11) ----
XP_LS='.engine="lspatch" | .engine_options={"sigbypass":3,"injectdex":true} | .keystore={"file":"lspatch-signing.p12","password":"morphe","alias":"fiorenmas","alias_password":"morphe"} | .recipe.variants=[.recipe.variants[0] | .name="arm64-v8a" | .abi="arm64-v8a" | .strip_libs=null]'
XP_NP='.engine="npatch" | .engine_options={"sigbypass":3,"injectdex":false} | .keystore={"file":"fiorenmas.ks","password":"fiorenmas","alias":"morphe","alias_password":"fiorenmas"} | .recipe.variants=[.recipe.variants[0] | .strip_libs=null]'

new_env; make_job "$XP_LS"
rc=$(run_runner)
if [ "$rc" = 0 ] && [ -s "$T/work/release/arm64-v8a.apk" ] && [ ! -e "$T/work/release/arm64-v8a.d" ] &&
	grep -Eq 'src/keystore/lspatch-signing\.p12 morphe fiorenmas morphe -l 3 --injectdex -m [^ ]+ -o [^ ]*release/arm64-v8a\.d -f' "$SHIM_LOG"; then
	pass xposed_lspatch_argv
else fail xposed_lspatch_argv "rc=$rc $(tail -3 "$T/out.txt")"; fi

new_env; make_job "$XP_NP"
rc=$(run_runner)
if [ "$rc" = 0 ] && [ -s "$T/work/release/universal.apk" ] &&
	grep -Eq 'src/keystore/fiorenmas\.ks fiorenmas morphe fiorenmas -l 3 -m ' "$SHIM_LOG" && ! grep -q -- '--injectdex' "$SHIM_LOG"; then
	pass xposed_npatch_argv
else fail xposed_npatch_argv "rc=$rc $(tail -3 "$T/out.txt")"; fi

for n in 0 2; do
	new_env; make_job "$XP_LS"
	export SHIM_ENGINE_APKS=$n
	rc=$(run_runner)
	unset SHIM_ENGINE_APKS
	if [ "$rc" != 0 ] && [ "$(fail_lines)" = 1 ] && grep -q "^::fail::patch_failed::engine produced $n apks$" "$T/out.txt"; then
		pass "xposed_${n}_apks"
	else fail "xposed_${n}_apks" "rc=$rc $(grep '^::fail::' "$T/out.txt")"; fi
done

new_env; make_job "$XP_LS | .engine_options.sigbypass=9"
rc=$(run_runner)
if [ "$rc" != 0 ] && grep -q '^::fail::internal::bad engine_options$' "$T/out.txt"; then
	pass xposed_bad_sigbypass
else fail xposed_bad_sigbypass "rc=$rc"; fi

new_env; make_job "$(cache_f)"
rc1=$(run_runner)
rm -rf "${T:?}/work" && mkdir -p "$T/work" && make_job "$(cache_f)" && rm "$SHIM_SERVE/input.apk"
rc2=$(run_runner)
if [ "$rc1" = 0 ] && [ "$rc2" = 0 ] && grep -q 'Input from cache' "$T/out.txt"; then pass runner_cache_hit; else fail runner_cache_hit "rc1=$rc1 rc2=$rc2"; fi

# APKMirror cache (no versionCode in its key): served only for an expected code, checked against the file (shim 1561173354)
AM='.recipe.variants[].download.kind="apkmirror" | .recipe.variants[].download.url=null'
am_cache() { mkdir -p "$T/cache/$YT"; cp "$SHIM_SERVE/input.apk" "$T/cache/$YT/1.0.0-apk-any-any.apk"; }
new_env; am_cache; make_job "$AM | .recipe.variants[].download.version_codes=[1561173354] | $(cache_f)"
rc=$(run_runner)
if [ "$rc" = 0 ] && grep -q 'Input from cache' "$T/out.txt" && ! grep -q '^::apkm_variant::' "$T/out.txt"; then
	pass apkm_manual_code_cache_hit
else fail apkm_manual_code_cache_hit "rc=$rc $(tail -3 "$T/out.txt")"; fi

new_env; am_cache; make_job "$AM | .recipe.variants[].download.version_codes=[7] | $(cache_f)"
rc=$(run_runner)
if [ "$rc" != 0 ] && [ ! -e "$T/cache/$YT/1.0.0-apk-any-any.apk" ] &&
	grep -qF '[!] Cached input is another APKMirror variant, downloading: APKMirror download does not match the request: expected versionName any versionCode 7, downloaded versionName 21.20.405 versionCode 1561173354' "$T/out.txt"; then
	pass apkm_manual_code_cache_mismatch
else fail apkm_manual_code_cache_mismatch "rc=$rc $(tail -3 "$T/out.txt")"; fi

new_env; am_cache; make_job "$AM | .apkmirror_picks=[{download:.recipe.variants[0].download, version_code:1561173354, variant_url:\"https://www.apkmirror.com/v/\", bundle:false, exclude_codes:[]}] | $(cache_f)"
rc=$(run_runner)
if [ "$rc" = 0 ] && [ "$(grep -c '^::apkm_variant::' "$T/out.txt")" = 1 ] &&
	grep '^::apkm_variant::' "$T/out.txt" | cut -c17- | jq -e '.version_code == 1561173354 and .version_name == "21.20.405" and
		.variant_url == "https://www.apkmirror.com/v/" and .bundle == false and .remaining == null and .download.kind == "apkmirror"' >/dev/null; then
	pass apkm_remembered_cache_hit_marker
else fail apkm_remembered_cache_hit_marker "rc=$rc $(grep '^::' "$T/out.txt" | tail -3)"; fi

# ---- inspect cases (Task P3.4) ----
new_env
o=$T/inspect
(bash "$REPO/src/build/inspect.sh" "$T/up/morphe-desktop-1.0.0-all.jar" com.google.android.youtube "$o" "$T/up/patches-1.0.0.mpp" > "$T/out.txt" 2>&1)
rc=$?
if [ "$rc" = 0 ] && [ -s "$o/options.json" ] && [ -s "$o/list-patches.txt" ] &&
	grep -q -- "options-create -p $T/up/patches-1.0.0.mpp -f com.google.android.youtube -o $o/options.json" "$SHIM_LOG" &&
	grep -q -- "list-patches --with-packages --with-versions --with-options -f com.google.android.youtube --patches $T/up/patches-1.0.0.mpp" "$SHIM_LOG"; then
	pass inspect_ok
else fail inspect_ok "rc=$rc $(cat "$T/out.txt")"; fi

new_env; export SHIM_EMPTY=1
(bash "$REPO/src/build/inspect.sh" "$T/up/morphe-desktop-1.0.0-all.jar" com.google.android.youtube "$T/inspect" "$T/up/patches-1.0.0.mpp" > "$T/out.txt" 2>&1)
rc=$?; unset SHIM_EMPTY
if [ "$rc" = 1 ] && [ "$(grep -c '^\[-\]' "$T/out.txt")" = 1 ]; then pass inspect_empty; else fail inspect_empty "rc=$rc $(cat "$T/out.txt")"; fi

(bash "$REPO/src/build/inspect.sh" only-two args > "$T/out.txt" 2>&1)
rc=$?
if [ "$rc" = 2 ] && grep -q '^\[-\] usage' "$T/out.txt"; then pass inspect_usage; else fail inspect_usage "rc=$rc"; fi

[ "$fails" -eq 0 ] || { echo "$fails case(s) failed"; exit 1; }
echo "ALL PASS"
