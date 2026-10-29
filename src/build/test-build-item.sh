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
	export PATH="$FIX/bin:$PATH" RELUME_APKEDITOR=$T/APKEditor.jar RELUME_APK_CACHE=$T/cache
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

# ---- utils.sh cases ----
new_env
d=$(mktemp -d)
(cd "$d" && source "$REPO/src/build/utils.sh")
if [ -z "$(ls -A "$d")" ]; then pass utils_source_no_side_effects; else fail utils_source_no_side_effects "$(ls -A "$d")"; fi

out=$(cd "$T/work" && source "$REPO/src/build/utils.sh" && init_ua && echo "UA=$user_agent")
if grep -q '\[!\] Using fixed user agent' <<<"$out" && grep -q 'UA=Mozilla/5.0' <<<"$out"; then pass init_ua_offline; else fail init_ua_offline "$out"; fi

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
	grep -q -- "-p $T/up/patches-1.0.0.mpp" <<<"$log" && grep -q -- "--continue-on-error" <<<"$log"; then
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
	[ "$(grep -c -- '--striplibs arm64-v8a' "$SHIM_LOG")" = 1 ]; then
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
gp_job() { make_job "$GP | .version.value=$1 | .googleplay=[{download:.recipe.variants[0].download, path:\"$2\"}]"; }
new_env; mkdir -p "$T/gp"; cp "$SHIM_SERVE/input.apk" "$T/gp/$YT.apk"; gp_job null "$T/gp/$YT.apk"
rc=$(run_runner)
if [ "$rc" = 0 ] && [ -s "$T/work/download/in-0.apk" ] && [ "$(jq -r .app_version "$T/work/result.json")" = 21.20.405 ]; then
	pass googleplay_single_apk
else fail googleplay_single_apk "rc=$rc $(tail -3 "$T/out.txt")"; fi

new_env; mkdir -p "$T/gp/$YT"; cp "$SHIM_SERVE/input.apk" "$T/gp/$YT/$YT.apk"; cp "$SHIM_SERVE/input.apk" "$T/gp/$YT/$YT.config.arm64_v8a.apk"
gp_job '"21.20.405"' "$T/gp/$YT"
rc=$(run_runner)
if [ "$rc" = 0 ] && [ -s "$T/work/download/in-0.apk" ] && grep -q "java -jar $T/APKEditor.jar m -i ./download/in-0.xapk" "$SHIM_LOG" &&
	unzip -Z1 "$T/work/download/in-0.apk" | grep -qxF "$YT.config.arm64_v8a.apk"; then
	pass googleplay_split_dir
else fail googleplay_split_dir "rc=$rc $(tail -3 "$T/out.txt")"; fi

new_env; mkdir -p "$T/gp"; cp "$SHIM_SERVE/input.apk" "$T/gp/$YT.apk"; gp_job '"1.0.0"' "$T/gp/$YT.apk"
rc=$(run_runner)
if [ "$rc" != 0 ] && [ "$(fail_lines)" = 1 ] &&
	grep -qxF '::fail::download_failed::Google Play serves only the latest version: expected 1.0.0, downloaded 21.20.405' "$T/out.txt"; then
	pass googleplay_version_mismatch
else fail googleplay_version_mismatch "rc=$rc $(grep '^::fail::' "$T/out.txt")"; fi

# ---- Xposed engine cases (P8, spec xposed 5.3 / 11) ----
XP_LS='.engine="lspatch" | .engine_options={"sigbypass":3,"injectdex":true} | .keystore="lspatch-signing.p12" | .recipe.variants=[.recipe.variants[0] | .name="arm64-v8a" | .abi="arm64-v8a" | .strip_libs=null]'
XP_NP='.engine="npatch" | .engine_options={"sigbypass":3,"injectdex":false} | .keystore="fiorenmas.ks" | .recipe.variants=[.recipe.variants[0] | .strip_libs=null]'

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

new_env; make_job "$XP_LS | .keystore=\"other.ks\""
rc=$(run_runner)
if [ "$rc" != 0 ] && [ "$(fail_lines)" = 1 ] && grep -q '^::fail::internal::unknown keystore$' "$T/out.txt"; then
	pass xposed_unknown_keystore
else fail xposed_unknown_keystore "rc=$rc"; fi

new_env; make_job "$XP_LS | .engine_options.sigbypass=9"
rc=$(run_runner)
if [ "$rc" != 0 ] && grep -q '^::fail::internal::bad engine_options$' "$T/out.txt"; then
	pass xposed_bad_sigbypass
else fail xposed_bad_sigbypass "rc=$rc"; fi

new_env; make_job '.'
rc1=$(run_runner)
rm -rf "${T:?}/work" && mkdir -p "$T/work" && make_job '.' && rm "$SHIM_SERVE/input.apk"
rc2=$(run_runner)
if [ "$rc1" = 0 ] && [ "$rc2" = 0 ] && grep -q 'Input from cache' "$T/out.txt"; then pass runner_cache_hit; else fail runner_cache_hit "rc1=$rc1 rc2=$rc2"; fi
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
