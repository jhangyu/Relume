#!/usr/bin/env bash
# Runner self-check (spec build §8). Offline: wget/curl/java/apksigner are shims.
# Usage: bash src/build/test-build-item.sh   (exit 0 = all PASS)
# shellcheck disable=SC2034,SC2154  # globals are read by the sourced scripts
set -o pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
FIX=$REPO/src/build/test-fixtures
fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1: $2"; fails=$((fails + 1)); }

new_env() {
	T=$(mktemp -d)
	export SHIM_LOG=$T/shim.log SHIM_SERVE=$T/serve SHIM_FIXTURES=$FIX
	export PATH="$FIX/bin:$PATH" RV_APKEDITOR=$T/APKEditor.jar RV_APK_CACHE=$T/cache
	unset SHIM_PATCH_FAIL SHIM_EMPTY SHIM_LIST_PATCHES
	mkdir -p "$SHIM_SERVE" "$T/work" "$T/up" "$T/cache" "$T/m"
	printf '<manifest/>' > "$T/m/AndroidManifest.xml"
	printf 'x' > "$T/m/classes.dex"
	(cd "$T/m" && zip -q "$SHIM_SERVE/input.apk" AndroidManifest.xml classes.dex)
	: > "$T/up/patches-1.0.0.mpp"
	: > "$T/up/morphe-desktop-1.0.0-all.jar"
	: > "$RV_APKEDITOR"
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
	engine=morphe cli_jar=$T/up/morphe-desktop-1.0.0-all.jar keystore=morphe.keystore RV_REPO=$REPO &&
	patches_files=("$T/up/patches-1.0.0.mpp") &&
	patch_variant "$SHIM_SERVE/input.apk" release/x.apk arm64-v8a && echo OK)
log=$(cat "$SHIM_LOG")
if [ "$out" = OK ] && [ -s "$T/work/release/x.apk" ] && grep -q -- "--striplibs arm64-v8a" <<<"$log" &&
	grep -q -- "--options-file options.json" <<<"$log" && grep -q -- "--keystore=$REPO/src/keystore/morphe.keystore" <<<"$log" &&
	grep -q -- "-p $T/up/patches-1.0.0.mpp" <<<"$log" && grep -q -- "--continue-on-error" <<<"$log"; then
	pass patch_variant_argv
else fail patch_variant_argv "$out / $log"; fi

out=$(cd "$T/work" && source "$REPO/src/build/utils.sh" && engine=lspatch &&
	{ patch_variant a b ""; echo "rc=$? code=$FAIL_CODE err=$LAST_ERR"; })
if grep -q 'rc=1 code=internal err=engine not implemented' <<<"$out"; then pass engine_stub; else fail engine_stub "$out"; fi

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

new_env; make_job '.version.mode="auto" | .version.value=null'; export SHIM_LIST_PATCHES=/dev/null
rc=$(run_runner); unset SHIM_LIST_PATCHES
if [ "$rc" != 0 ] && grep -q '^::fail::version_unresolved::' "$T/out.txt"; then pass runner_version_unresolved; else fail runner_version_unresolved "rc=$rc"; fi

new_env; make_job '.recipe.variants[].download.kind="apkmirror"'
rc=$(run_runner)
if [ "$rc" != 0 ] && grep -q '^::fail::apk_not_listed::' "$T/out.txt"; then pass runner_apk_not_listed; else fail runner_apk_not_listed "rc=$rc"; fi

new_env; make_job '.engine="lspatch"'
rc=$(run_runner)
if [ "$rc" != 0 ] && [ "$(fail_lines)" = 1 ] && grep -q '^::fail::internal::engine not implemented$' "$T/out.txt"; then pass runner_engine_stub; else fail runner_engine_stub "rc=$rc"; fi

new_env; make_job '.'
rc1=$(run_runner)
rm -rf "${T:?}/work" && mkdir -p "$T/work" && make_job '.' && rm "$SHIM_SERVE/input.apk"
rc2=$(run_runner)
if [ "$rc1" = 0 ] && [ "$rc2" = 0 ] && grep -q 'Input from cache' "$T/out.txt"; then pass runner_cache_hit; else fail runner_cache_hit "rc1=$rc1 rc2=$rc2"; fi
# ---- inspect cases (Task P3.4) ----

[ "$fails" -eq 0 ] || { echo "$fails case(s) failed"; exit 1; }
echo "ALL PASS"
