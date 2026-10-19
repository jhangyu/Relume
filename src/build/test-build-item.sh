#!/usr/bin/env bash
# Runner self-check (spec build §8). Offline: wget/curl/java/apksigner are shims.
# Usage: bash src/build/test-build-item.sh   (exit 0 = all PASS)
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
# ---- inspect cases (Task P3.4) ----

[ "$fails" -eq 0 ] || { echo "$fails case(s) failed"; exit 1; }
echo "ALL PASS"
