#!/usr/bin/env bash
# Build runner (spec build §2-§4): executes one job.json. No DB, queue or HTTP knowledge.
# Usage: bash src/build/build-item.sh <job.json>
# stdout markers: ::step::<name>[::i/n]  ::version::<v>  ::apkm_versions::<csv>  ::fail::<code>::<detail>
# shellcheck disable=SC2034  # globals are read by utils.sh
set -o pipefail
RELUME_REPO=$(cd "$(dirname "$0")/../.." && pwd)

fail() {
	echo "::fail::$1::$2"
	exit 1
}

job=$(realpath -e "${1:-/nonexistent}" 2>/dev/null) || fail internal "job.json missing: ${1:-}"
# shellcheck source=src/build/utils.sh
source "$RELUME_REPO/src/build/utils.sh"

j() { jq -r "$1 // empty" "$job"; }

# Keystore credentials for Xposed engines (spec xposed 5.3; public credentials of the committed src/keystore files).
keystore_creds() {
	case "$1" in
	lspatch-signing.p12) echo "morphe fiorenmas morphe" ;;
	fiorenmas.ks) echo "fiorenmas morphe fiorenmas" ;;
	*) return 1 ;;
	esac
}

work=$(j .work_dir)
package=$(j .package)
engine=$(j .engine)
engine_sigbypass=$(j .engine_options.sigbypass)
engine_injectdex=$(j .engine_options.injectdex)
cli_jar=$(j .cli_jar)
keystore=$(j .keystore)
upstream_tag=$(j .upstream_tag)
cli_tag=$(j .cli_tag)
[[ -n "$work" && -d "$work" && -n "$package" && -n "$engine" && -n "$cli_jar" && -n "$keystore" ]] ||
	fail internal "job.json lacks work_dir, package, engine, cli_jar or keystore"
mapfile -t patches_files < <(jq -r '.patches_files[]' "$job")
mapfile -t variants < <(jq -c '.recipe.variants[]' "$job")
mapfile -t specs < <(jq -cS '[.recipe.variants[].download] | unique | .[]' "$job")
n_var=${#variants[@]}
[[ $n_var -ge 1 ]] || fail internal "recipe has no variants"

cd "$work" || fail internal "cannot enter $work"
mkdir -p download release
init_ua

ln -sf "$cli_jar" . || fail internal "cannot link $cli_jar"
for f in "${patches_files[@]}"; do ln -sf "$f" . || fail internal "cannot link $f"; done

# ---- version (resolved by the server, apps part §4.2): value null = newest on APKMirror ----
echo "::step::version"
version=$(j .version.value)
allow_near=0
[[ "$(jq -r '.version.near' "$job")" == true ]] && allow_near=1
printed_version=""
if [[ -n "$version" ]]; then
	echo "::version::$version"
	printed_version=$version
fi

# ---- download (one fetch per distinct download spec) ----
echo "::step::download"
list_url=$(j .apkmirror.list_url)
ex1=$(j .apkmirror.example_url)
ex2=$(j .apkmirror.example_url2)
apkpure_url=$(j .apkpure_url)

fetch_input() {
	local n=$1 d=$2 name="in-$1" kind type arch dpi minver url ext=apk
	kind=$(jq -r .kind <<<"$d")
	type=$(jq -r '.type // "apk"' <<<"$d")
	arch=$(jq -r '.arch // empty' <<<"$d")
	dpi=$(jq -r '.dpi // empty' <<<"$d")
	minver=$(jq -r '.minver // empty' <<<"$d")
	url=$(jq -r '.url // empty' <<<"$d")
	[[ $type == bundle_extract ]] && ext=apkm
	local -a want
	mapfile -t want < <(jq -r --argjson d "$d" '[.recipe.variants[] | select(.download == $d and .abi != "universal") | .abi] | unique[]' "$job")
	local label="$package $version (download $kind type=$type arch=${arch:-any})" got
	local spec_key cache_dir="${RELUME_APK_CACHE:-}/$package" use_cache=0
	spec_key=$(printf -- '-%s-%s-%s' "$type" "${arch:-any}" "${dpi:-any}" | tr -c 'A-Za-z0-9._-' '_')
	local vkey cache_file
	vkey=$(printf '%s' "$version" | tr -c 'A-Za-z0-9._-' '_')
	cache_file="$cache_dir/$vkey$spec_key.$ext"
	# googleplay: the server already downloaded the file, nothing to cache
	[[ -n "${RELUME_APK_CACHE:-}" && -n "$version" && $allow_near -eq 0 && $kind != googleplay ]] && use_cache=1
	if [[ $use_cache -eq 1 && -s "$cache_file" ]]; then
		green_log "[+] Input from cache: $(basename "$cache_file")"
		cp "$cache_file" "download/$name.$ext" || fail internal "cannot copy from cache"
		[[ $ext == apkm ]] && unzip -q "download/$name.apkm" -d "download/$name"
		got="download/$name.$ext"
		[[ -d "download/$name" ]] && got="download/$name"
		check_abis "$got" "$label" "${want[@]}" || { rm -f "$cache_file"; fail download_failed "$LAST_ERR (cached file removed)"; }
		return 0
	fi
	case "$kind" in
	apkmirror)
		[[ -n "$list_url" ]] || fail apk_not_listed "no APKMirror list URL for $package"
		[[ $allow_near -eq 1 ]] && near_version=1
		get_apk "$package" "$name" "$type" "$arch" "$dpi" "$minver" "$list_url" "$ex1" "$ex2" "${want[*]}" ||
			fail download_failed "${LAST_ERR:-download failed}"
		;;
	apkpure)
		get_apkpure "$package" "$name" "$type" "$apkpure_url" || fail download_failed "${LAST_ERR:-download failed}"
		;;
	googleplay)
		local gp base gpver
		gp=$(jq -r --argjson d "$d" 'first(.googleplay[] | select(.download == $d) | .path) // empty' "$job")
		[[ -n "$gp" && -e "$gp" ]] || fail internal "no Google Play download for $d"
		base=$gp
		[[ -d "$gp" ]] && base=$gp/$package.apk
		gpver=$(apk_info "$base" | sed -n 's/^version_name=//p')
		[[ -n "$gpver" ]] || fail download_failed "cannot read versionName of $base"
		# Google Play serves only the latest version (apkeep cannot pin one)
		[[ -z "$version" || "$version" == "$gpver" ]] ||
			fail download_failed "Google Play serves only the latest version: expected $version, downloaded $gpver"
		version=$gpver
		get_googleplay "$gp" "$name" "$type" || fail download_failed "${LAST_ERR:-Google Play input failed}"
		;;
	url)
		req "$url" "$name.apk" || fail download_failed "download failed: $url"
		;;
	*) fail internal "unknown download kind $kind" ;;
	esac
	got="download/$name.$ext"
	[[ -d "download/$name" ]] && got="download/$name"
	check_abis "$got" "$label" "${want[@]}" || fail download_failed "$LAST_ERR"
	if [[ $use_cache -eq 1 && -s "download/$name.$ext" ]]; then
		mkdir -p "$cache_dir" && rm -f "$cache_dir"/*"$spec_key".* && cp "download/$name.$ext" "$cache_file"
	fi
	return 0
}

for k in "${!specs[@]}"; do fetch_input "$k" "${specs[$k]}"; done
if [[ -n "$version" && "$version" != "$printed_version" ]]; then
	echo "::version::$version"
	printed_version=$version
fi

# APKMirror versions for the pin list (spec build §4 step 5d): at most one extra list-page request
if [[ "$(j .apkmirror.versions_due)" == true && $APKM_EMITTED -eq 0 && -n "$list_url" ]] &&
	jq -e '[.recipe.variants[].download.kind] | index("apkmirror")' "$job" >/dev/null; then
	if _cf_get "$list_url"; then emit_apkm_versions; else yellow_log "[!] APKMirror version list unavailable"; fi
fi

spec_index() {
	local k
	for k in "${!specs[@]}"; do
		[[ "${specs[$k]}" == "$1" ]] && { echo "$k"; return 0; }
	done
	echo -1
}

# ---- patch each variant ----
i=0
for v in "${variants[@]}"; do
	i=$((i + 1))
	echo "::step::patch::$i/$n_var"
	name=$(jq -r .name <<<"$v")
	strip=$(jq -r '.strip_libs // empty' <<<"$v")
	excl=$(jq -c '.exclude_splits' <<<"$v")
	base="in-$(spec_index "$(jq -cS .download <<<"$v")")"
	input="download/$base.apk"
	if [[ "$excl" != null && "$excl" != '[]' ]]; then
		split_editor "$base" "v-$name" exclude "$(jq -r 'join(" ")' <<<"$excl")"
		input="download/v-$name.apk"
	elif [[ -d "download/$base" && ! -s "download/$base.apk" ]]; then
		split_editor "$base"
	fi
	[[ -s "$input" ]] || fail merge_failed "no input APK for variant $name"
	FAIL_CODE=""
	patch_variant "$input" "release/$name.apk" "$strip" || fail "${FAIL_CODE:-patch_failed}" "${LAST_ERR:-patch failed}"
	vabi=$(jq -r .abi <<<"$v")
	if [[ $vabi != universal ]]; then
		check_abis "release/$name.apk" "$package variant $name" "$vabi" || fail patch_failed "$LAST_ERR"
	fi
done

# ---- verify and write result.json ----
echo "::step::verify"
: > variants.jsonl
first_vname=""
for v in "${variants[@]}"; do
	name=$(jq -r .name <<<"$v")
	abi=$(jq -r .abi <<<"$v")
	f="release/$name.apk"
	info=$(apk_info "$f") || fail patch_failed "cannot read the package of $f"
	vpkg=$(sed -n 's/^package=//p' <<<"$info")
	vcode=$(sed -n 's/^version_code=//p' <<<"$info")
	vname=$(sed -n 's/^version_name=//p' <<<"$info")
	[[ -z "$first_vname" ]] && first_vname=$vname
	signer=$(apk_signer "$f") || signer=""
	sha=$(sha256sum "$f" | cut -d' ' -f1)
	size=$(stat -c %s "$f")
	jq -nc --arg name "$name" --arg abi "$abi" --arg file "$f" --argjson size "$size" --arg sha "$sha" \
		--arg pkg "$vpkg" --arg vname "$vname" --arg vcode "$vcode" --arg signer "$signer" \
		'{name:$name, abi:$abi, file:$file, size:$size, sha256:$sha, package:$pkg, version_name:$vname,
		  version_code:($vcode|tonumber? // null), signer_sha256:(if $signer == "" then null else $signer end)}' \
		>> variants.jsonl || fail internal "cannot record variant $name"
done
app_version=${version:-$first_vname}
[[ "$app_version" != "$printed_version" ]] && echo "::version::$app_version"
jq -s --arg av "$app_version" --arg pv "$upstream_tag" --arg cv "$cli_tag" \
	'{app_version:$av, patches_version:$pv, cli_version:$cv, variants:.}' variants.jsonl > result.json ||
	fail internal "cannot write result.json"
green_log "[+] Build finished: $n_var variant(s)"
