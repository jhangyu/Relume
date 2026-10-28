#!/bin/bash
# Build library sourced by src/build/build-item.sh (spec build §2, §7).
# Sourcing has no side effects. Callers cd into the job work dir; ./download and ./release live there.
# shellcheck disable=SC2034,SC2154  # globals are shared with the runner

pup=pup
APKEditor=${RELUME_APKEDITOR:-/opt/APKEditor.jar}
user_agent='Mozilla/5.0 (Android 16; Mobile; rv:146.0) Gecko/146.0 Firefox/146.0'
LAST_ERR=""
FAIL_CODE=""
APKM_EMITTED=0

#################################################

# Colored output logs
green_log() {
	echo -e "\e[32m$1\e[0m"
}
red_log() {
	LAST_ERR=$1
	echo -e "\e[31m$1\e[0m"
}
yellow_log() {
	echo -e "\e[33m$1\e[0m"
}

# Latest Firefox Android user agent; offline -> keep the fixed one.
init_ua() {
	local ua
	ua=$(wget -qO- --timeout=10 --tries=1 https://www.whatismybrowser.com/guides/the-latest-user-agent/firefox | tr '\n' ' ' | sed 's#</tr>#\n#g' | grep 'Firefox (Standard)' | sed -n 's/.*<span class="code">\([^<]*Android[^<]*\)<\/span>.*/\1/p')
	if [[ -n "$ua" ]]; then user_agent=$ua; else yellow_log "[!] Using fixed user agent"; fi
}

#################################################

req() {
	local cookie_args=()
	[[ -n "$FS_COOKIES" ]] && cookie_args=(--header "Cookie: $FS_COOKIES")
	local out="./download/$2"
	[[ "$2" = "-" ]] && out="-"
	wget -nv -O "$out" --header="User-Agent: $user_agent" --header="Content-Type: application/octet-stream" --header="Accept-Language: en-US,en;q=0.9" --header="Connection: keep-alive" --header="Upgrade-Insecure-Requests: 1" --header="Cache-Control: max-age=0" --header="Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8" "${cookie_args[@]}" --keep-session-cookies --timeout=30 "$1" || { [[ "$out" != "-" ]] && rm -f "$out"; return 1; }
}

_fs_get() {
	local url=$1
	local max_retries=3
	local attempt
	for attempt in $(seq 1 $max_retries); do
		local response
		response=$(curl -s -X POST "${FLARESOLVERR_URL:-http://127.0.0.1:47191}/v1" \
			-H 'Content-Type: application/json' \
			-d "{\"cmd\":\"request.get\",\"url\":\"$url\",\"maxTimeout\":15000}")
		local status
		status=$(echo "$response" | jq -r '.status // empty' 2>/dev/null)
		if [[ "$status" == "ok" ]]; then
			html=$(echo "$response" | jq -r '.solution.response // empty')
			export FS_COOKIES
			FS_COOKIES=$(echo "$response" | jq -r '[.solution.cookies[] | .name + "=" + .value] | join("; ")')
			user_agent=$(echo "$response" | jq -r '.solution.userAgent // empty')
			return 0
		fi
		yellow_log "[!] FlareSolverr attempt $attempt/$max_retries failed: $url"
		sleep 5
	done
	red_log "[-] FlareSolverr failed after $max_retries attempts: $url"
	return 1
}

_cfb_get() {
	local url=$1
	local base=${CFBYPASS_URL:-http://127.0.0.1:47800}
	local max_retries=3
	local attempt
	for attempt in $(seq 1 $max_retries); do
		local response_file http_code
		response_file=$(mktemp)
		http_code=$(curl -s -o "$response_file" -w '%{http_code}' -G --data-urlencode "url=$url" --max-time 120 "$base/html")
		if [[ "$http_code" == "200" ]]; then
			html=$(cat "$response_file")
			rm -f "$response_file"
			if [[ -n "$html" ]]; then
				export FS_COOKIES=""
				local ck cfb_ck cfb_ua
				# /cookies JSON: {"cookies": {k: v}, "user_agent": "..."}
				ck=$(curl -sf --max-time 120 -G --data-urlencode "url=$url" "$base/cookies")
				cfb_ck=$(jq -r '.cookies // {} | to_entries | map(.key + "=" + (.value|tostring)) | join("; ")' <<<"$ck" 2>/dev/null)
				cfb_ua=$(jq -r '.user_agent // ""' <<<"$ck" 2>/dev/null)
				# empty UA -> 403, so only use cookies + UA together
				if [[ -n "$cfb_ck" && -n "$cfb_ua" ]]; then
					FS_COOKIES="$cfb_ck"
					user_agent="$cfb_ua"
				fi
				return 0
			fi
		else
			rm -f "$response_file"
			yellow_log "[!] CFB attempt $attempt/$max_retries: HTTP $http_code: $url"
		fi
	done
	red_log "[-] CFBypass failed after $max_retries attempts: $url"
	return 1
}

_FFS_FAILED=0

_cf_get() {
	if [[ "$_FFS_FAILED" -eq 0 ]]; then
		_fs_get "$@" && return 0
		yellow_log "[!] FlareSolverr failed, falling back to CFB"
		_FFS_FAILED=1
	fi
	_cfb_get "$@"
}

# Print ::apkm_versions:: once per run from the list page in $html (spec build §4 step 5d).
emit_apkm_versions() {
	[[ "$APKM_EMITTED" -eq 1 || -z "$html" ]] && return 0
	APKM_EMITTED=1
	local v
	v=$(echo "$html" | "$pup" 'h5.appRowTitle a.fontBlack json{}' | \
		jq -r '.[] | select(.text | test("(?i)beta|alpha") | not) | .text' 2>/dev/null | \
		awk '{print $NF}' | grep -E '^[0-9]' | awk '!seen[$0]++' | paste -sd, -)
	[[ -n "$v" ]] && echo "::apkm_versions::$v"
	return 0
}

# get_apk <pkg> <name> <type> <arch> <dpi> <minver> <list_url> <example_url> <example_url2> <want_abis>
# want_abis (space-separated) steers the variant pick when <arch> is empty; check_abis verifies the result.
get_apk() {
	local pkg_name=$1 apk_name=$2
	local pkg_type=${3:-apk} arch=${4:-} dpi=${5:-} minver=${6:-} want_abis=${10:-}
	local list_url=${7:-}
	local allow_near_version=${near_version:-0}
	unset near_version
	local base_url="https://www.apkmirror.com"
	html=""

	local example_urls=()
	[[ -n "${8:-}" ]] && example_urls+=("$8")
	[[ -n "${9:-}" ]] && example_urls+=("$9")

	if [[ -z "$list_url" ]]; then
		red_log "[-] Package $pkg_name has no APKMirror list URL"
		return 1
	fi

	local base_apk
	if [[ "$pkg_type" == "bundle" ]] || [[ "$pkg_type" == "bundle_extract" ]]; then
		base_apk="$apk_name.apkm"
	else
		base_apk="$apk_name.apk"
	fi

	export version

	green_log "[+] Detected version: ${version:-latest} [$pkg_name]"
	green_log "[+] Downloading $apk_name (type=$pkg_type arch=${arch:-any} dpi=$dpi)"

	local version_href=""

	if [[ ${#example_urls[@]} -gt 0 && -n "$version" ]]; then
		local example_url slug_ver target_ver
		for example_url in "${example_urls[@]}"; do
			version_href="${example_url#"$base_url"}"
			slug_ver=$(echo "$version_href" | grep -oP '\d+(-\d+)+' | tail -1)
			target_ver=$(echo "$version" | tr '.' '-' | grep -oP '\d+(-\d+)+')
			if [[ -n "$slug_ver" ]]; then
				version_href="${version_href/$slug_ver/$target_ver}"
			fi

			echo "$base_url$version_href"
			if _cf_get "$base_url$version_href" && [[ "$html" != *"Page Not Found"* && "$html" != *"404 Whoops"* ]]; then
				break
			fi
			version_href=""
		done
	fi

	if [[ -z "$version_href" ]]; then
		_cf_get "$list_url" || return 1
		emit_apkm_versions

		version_href=$(echo "$html" | "$pup" 'h5.appRowTitle a.fontBlack json{}' | \
			jq -r '.[] | select(.text | test("(?i)beta|alpha") | not) | .href' | head -1)
		[[ -z "$version_href" ]] && \
			version_href=$(echo "$html" | "$pup" 'h5.appRowTitle a.fontBlack attr{href}' | head -1)

		if [[ -z "$version_href" ]]; then
			red_log "[-] Could not find release on APKMirror"
			return 1
		fi

		if [[ -n "$version" ]]; then
			local slug_ver target_ver
			slug_ver=$(echo "$version_href" | grep -oP '\d+(-\d+)+' | tail -1)
			target_ver=$(echo "$version" | tr '.' '-' | grep -oP '\d+(-\d+)+')
			if [[ -n "$slug_ver" ]]; then
				version_href="${version_href/$slug_ver/$target_ver}"
			fi
		fi

		echo "$base_url$version_href"

		_cf_get "$base_url$version_href" || return 1
	fi

	if [[ "$html" == *"Page Not Found"* ]] || [[ "$html" == *"404 Whoops"* ]]; then
		yellow_log "[!] Version page not found, searching uploads pages..."
		local target_ver_dot="$version"
		local near_version_href="" near_version_value=""
		version_href=""
		local page_num
		for page_num in $(seq 1 10); do
			local page_url="$list_url"
			if [[ $page_num -gt 1 ]]; then
				page_url="${list_url%%\?*}/page/$page_num/?${list_url#*\?}"
			fi
			_cf_get "$page_url" || return 1
			[[ $page_num -eq 1 ]] && emit_apkm_versions
			if [[ -n "$target_ver_dot" ]]; then
				version_href=$(echo "$html" | "$pup" 'h5.appRowTitle a.fontBlack json{}' | \
					jq -r --arg v "$target_ver_dot" '.[] | select(.text | contains($v)) | .href' | head -1)
				if [[ -z "$version_href" && "$allow_near_version" == "1" ]]; then
					local release_text release_href release_version
					while IFS=$'\t' read -r release_text release_href; do
						release_version=$(echo "$release_text" | grep -oP '\d+(\.\d+)+' | tail -1)
						[[ -z "$release_version" ]] && continue
						if [[ "$(printf '%s\n' "$release_version" "$target_ver_dot" | sort -V | tail -n1)" == "$target_ver_dot" && "$release_version" != "$target_ver_dot" ]]; then
							if [[ -z "$near_version_value" || "$(printf '%s\n' "$near_version_value" "$release_version" | sort -V | tail -n1)" == "$release_version" ]]; then
								near_version_value="$release_version"
								near_version_href="$release_href"
							fi
						fi
					done < <(echo "$html" | "$pup" 'h5.appRowTitle a.fontBlack json{}' | \
						jq -r '.[] | select(.text | test("(?i)beta|alpha") | not) | [.text, .href] | @tsv')
				fi
			else
				version_href=$(echo "$html" | "$pup" 'h5.appRowTitle a.fontBlack json{}' | \
					jq -r '.[] | select(.text | test("(?i)beta|alpha") | not) | .href' | head -1)
			fi
			[[ -n "$version_href" ]] && break
		done
		if [[ -z "$version_href" && -n "$near_version_href" ]]; then
			version_href="$near_version_href"
			version="$near_version_value"
			export version
			yellow_log "[!] Exact version $target_ver_dot unavailable; using nearest lower version $version"
		fi
		if [[ -z "$version_href" ]]; then
			red_log "[-] Could not find version on APKMirror"
			return 1
		fi
		echo "$base_url$version_href"
		_cf_get "$base_url$version_href" || return 1
	fi

	local type_badge="APK"
	[[ "$pkg_type" == "bundle" || "$pkg_type" == "bundle_extract" ]] && type_badge="BUNDLE"

	local vtable_html rows variant_href=""
	vtable_html=$(echo "$html" | "$pup" 'div.variants-table')
	rows=$(echo "$vtable_html" | tr '\n' ' ' | sed 's/<div class="table-row/\n<div class="table-row/g')

	local dpi_fallback=("120-640dpi" "120-480dpi" "480-640dpi" "480dpi")
	local type_attempts=("$type_badge")
	if [[ "$type_badge" == "BUNDLE" ]]; then
		type_attempts+=("APK")
	else
		type_attempts+=("BUNDLE")
	fi

	local matched_type=""
	local variant_page_loaded=false
	local try_type
	for try_type in "${type_attempts[@]}"; do
		local filtered_rows
		filtered_rows=$(echo "$rows" | grep -iP "apkm-badge[^>]*>\s*$try_type\s*<")
		[[ -n "$arch" ]] && filtered_rows=$(echo "$filtered_rows" | grep -iF -- "$arch")
		[[ -n "$minver" ]] && filtered_rows=$(echo "$filtered_rows" | grep -iF -- "$minver")
		if [[ -z "$arch" && -n "$want_abis" && -n "$filtered_rows" ]]; then
			local arch_rows w
			arch_rows=$filtered_rows
			for w in $want_abis; do arch_rows=$(echo "$arch_rows" | grep -iF -- "$w"); done
			[[ -z "$arch_rows" ]] && arch_rows=$(echo "$filtered_rows" | grep -iwE -- 'universal|noarch')
			if [[ -n "$arch_rows" ]]; then filtered_rows=$arch_rows; else yellow_log "[!] No $try_type variant lists ABIs $want_abis, universal or noarch"; fi
		fi

		if [[ -n "$dpi" ]]; then
			local dpi_filtered fb_dpi
			dpi_filtered=$(echo "$filtered_rows" | grep -iF -- "$dpi")
			if [[ -z "$dpi_filtered" ]]; then
				for fb_dpi in "${dpi_fallback[@]}"; do
					dpi_filtered=$(echo "$filtered_rows" | grep -iF -- "$fb_dpi")
					[[ -n "$dpi_filtered" ]] && { yellow_log "[!] DPI fallback: $dpi -> $fb_dpi"; break; }
				done
			fi
			filtered_rows="$dpi_filtered"
		fi

		variant_href=$(echo "$filtered_rows" | grep -oP 'accent_color[^>]*href="\K[^"]+' | head -1)
		if [[ -n "$variant_href" ]]; then
			matched_type="$try_type"
			[[ "$try_type" != "$type_badge" ]] && yellow_log "[!] Type fallback: $type_badge -> $try_type"
			break
		fi
	done

	if [[ -z "$variant_href" ]]; then
		local direct_apk_href
		direct_apk_href=$(echo "$html" | "$pup" 'a.downloadButton attr{href}' | grep 'forcebaseapk' | head -1)
		if [[ -n "$direct_apk_href" && -z "$arch" && -z "$dpi" && -z "$minver" ]]; then
			matched_type="APK"
			variant_page_loaded=true
			[[ "$type_badge" != "APK" ]] && yellow_log "[!] Type fallback: $type_badge -> APK"
			yellow_log "[!] APKMirror redirected directly to the APK download page"
		else
			red_log "[-] Could not find variant (type=$type_badge arch=${arch:-any} dpi=$dpi)"
			return 1
		fi
	fi
	if [[ "$variant_page_loaded" == false ]]; then
		variant_href=$(echo "$variant_href" | sed 's/&amp;/\&/g')
		echo "$base_url$variant_href"
	fi

	if [[ "$matched_type" == "BUNDLE" ]]; then
		base_apk="$apk_name.apkm"
	else
		base_apk="$apk_name.apk"
	fi

	if [[ "$variant_page_loaded" == false ]]; then
		_cf_get "$base_url$variant_href" || return 1
	fi

	local dl_btn_href all_dl_btns
	all_dl_btns=$(echo "$html" | "$pup" 'a.downloadButton attr{href}')
	if [[ "$matched_type" == "BUNDLE" ]]; then
		dl_btn_href=$(echo "$all_dl_btns" | grep -v 'forcebaseapk' | head -1)
		[[ -z "$dl_btn_href" ]] && dl_btn_href=$(echo "$all_dl_btns" | head -1)
	else
		dl_btn_href=$(echo "$all_dl_btns" | grep 'forcebaseapk' | head -1)
		[[ -z "$dl_btn_href" ]] && dl_btn_href=$(echo "$all_dl_btns" | head -1)
	fi

	if [[ -z "$dl_btn_href" ]]; then
		red_log "[-] Could not find download button"
		return 1
	fi
	dl_btn_href=$(echo "$dl_btn_href" | sed 's/&amp;/\&/g')

	_cf_get "$base_url$dl_btn_href" || return 1

	local final_href
	final_href=$(echo "$html" | "$pup" 'a#download-link attr{href}' | head -1)
	[[ -z "$final_href" ]] && \
		final_href=$(echo "$html" | grep -oP 'id="download-link"[^>]*href="\K[^"]+' | head -1)

	if [[ -z "$final_href" ]]; then
		red_log "[-] Could not find final download link"
		return 1
	fi
	final_href=$(echo "$final_href" | sed 's/&amp;/\&/g')

	echo "$base_url$final_href"
	local cookie_args=()
	[[ -n "$FS_COOKIES" ]] && cookie_args=(--header "Cookie: $FS_COOKIES")
	wget -nv -O "./download/$base_apk" \
		--header="User-Agent: $user_agent" \
		--referer="$base_url$dl_btn_href" \
		"${cookie_args[@]}" \
		--timeout=120 \
		"$base_url$final_href"
	local wget_rc=$?

	if [[ $wget_rc -ne 0 && -n "${APKM_SOLVER_URL:-}" ]]; then
		yellow_log "[!] Download blocked, asking Turnstile solver"
		local sol
		sol=$(curl -sf --max-time 150 -G --data-urlencode "url=$base_url$final_href" \
			--data-urlencode "referer=$base_url$dl_btn_href" "$APKM_SOLVER_URL/clearance")
		if [[ -n "$sol" ]]; then
			wget -nv -O "./download/$base_apk" --header="User-Agent: $(jq -r .ua <<<"$sol")" \
				--header="Cookie: $(jq -r .cookies <<<"$sol")" --referer="$base_url$dl_btn_href" \
				--timeout=120 "$base_url$final_href"
			wget_rc=$?
		fi
	fi

	if [[ $wget_rc -eq 0 && -s "./download/$base_apk" ]]; then
		green_log "[+] Successfully downloaded $apk_name"
	else
		rm -f "./download/$base_apk"
		red_log "[-] Failed to download $apk_name"
		return 1
	fi

	if [[ "$matched_type" == "BUNDLE" ]]; then
		if [[ "$pkg_type" == "bundle_extract" && "$type_badge" == "BUNDLE" ]]; then
			unzip "./download/$base_apk" -d "./download/$(basename "$base_apk" .apkm)" > /dev/null 2>&1
		else
			green_log "[+] Merge splits apk to standalone apk"
			java -jar "$APKEditor" m -i "./download/$apk_name.apkm" -o "./download/$apk_name.apk" > /dev/null 2>&1
		fi
	fi
}

# get_apkpure <pkg> <name> <type> <download_url>
get_apkpure() {
	local pkg_name=$1 apk_name=$2 pkg_type=${3:-apk} base_download_url=${4:-}
	html=""

	if [[ -z "$base_download_url" ]]; then
		red_log "[-] Package $pkg_name has no APKPure URL"
		return 1
	fi

	export version

	local base_apk
	if [[ "$pkg_type" == "bundle" ]] || [[ "$pkg_type" == "bundle_extract" ]]; then
		base_apk="$apk_name.xapk"
	else
		base_apk="$apk_name.apk"
	fi

	local dl_page_url
	if [[ -n "$version" ]]; then
		dl_page_url="${base_download_url%/}/$version"
	else
		dl_page_url="$base_download_url"
	fi

	green_log "[+] Downloading $apk_name from APKPure (type=$pkg_type)"
	echo "$dl_page_url"

	_cf_get "$dl_page_url" || return 1

	if [[ -z "$version" ]]; then
		version=$(echo "$html" | "$pup" 'h2 text{}' | grep -oP '\d+(\.\d+)+' | head -1)
	fi
	green_log "[+] Version: $version"

	local download_url
	download_url=$(echo "$html" | "$pup" 'a#download_link attr{href}' | head -1)
	[[ -z "$download_url" ]] && \
		download_url=$(echo "$html" | grep -oP '<a[^>]+id="download_link"[^>]+href="\Khttps://[^"]+' | head -1)

	if [[ -z "$download_url" ]]; then
		red_log "[-] Could not find download link on APKPure"
		return 1
	fi
	echo "$download_url"

	local cookie_args=()
	[[ -n "$FS_COOKIES" ]] && cookie_args=(--header "Cookie: $FS_COOKIES")
	wget -nv -O "./download/$base_apk" \
		--header="User-Agent: $user_agent" \
		--referer="$dl_page_url" \
		"${cookie_args[@]}" \
		--timeout=120 \
		"$download_url"
	local wget_rc=$?

	if [[ $wget_rc -eq 0 && -s "./download/$base_apk" ]]; then
		green_log "[+] Successfully downloaded $apk_name"
	else
		rm -f "./download/$base_apk"
		red_log "[-] Failed to download $apk_name"
		return 1
	fi
	if [[ "$pkg_type" == "bundle" ]]; then
		if unzip -l "./download/$base_apk" 2>/dev/null | grep -q '\.apk$'; then
			green_log "[+] Merge splits apk to standalone apk"
			if ! java -jar "$APKEditor" m -i "./download/$apk_name.xapk" -o "./download/$apk_name.apk" > /dev/null 2>&1; then
				red_log "[-] Failed to merge $apk_name.xapk to standalone apk"
				return 1
			fi
		elif unzip -l "./download/$base_apk" 2>/dev/null | grep -q 'AndroidManifest.xml'; then
			green_log "[+] File is already a standalone APK, renaming"
			mv "./download/$base_apk" "./download/$apk_name.apk"
		else
			red_log "[-] Unknown file format for $base_apk"
			return 1
		fi
	elif [[ "$pkg_type" == "bundle_extract" ]]; then
		unzip "./download/$base_apk" -d "./download/$(basename "$base_apk" .xapk)" > /dev/null 2>&1
	fi
}

#################################################

# apk_abis <apk | extracted bundle dir>: unique ABIs under lib/<abi>/ (a bundle dir: across all its split APKs)
apk_abis() {
	local f files=()
	if [[ -d "$1" ]]; then mapfile -t files < <(find "$1" -name '*.apk'); else files=("$1"); fi
	[[ ${#files[@]} -gt 0 ]] || { echo "no APK in $1" >&2; return 1; }
	for f in "${files[@]}"; do unzip -Z1 "$f" || return 1; done | sed -n 's#^lib/\([^/]*\)/.*#\1#p' | sort -u
}

# check_abis <apk | bundle dir> <label> <abi>...: return 1 (LAST_ERR set) when the input has native libs
# but lacks one of the given ABIs. No lib/ at all = pure Java = OK.
check_abis() {
	local src=$1 label=$2 found a missing=()
	shift 2
	found=$(apk_abis "$src") || { red_log "[-] $label: cannot list native libs of $src"; return 1; }
	[[ -z "$found" ]] && return 0
	for a in "$@"; do grep -qxF -- "$a" <<<"$found" || missing+=("$a"); done
	[[ ${#missing[@]} -eq 0 ]] && return 0
	red_log "[-] $label: expected ABIs [$*], found [$(paste -sd, - <<<"$found")], missing [${missing[*]}]"
	return 1
}

#################################################

# patch_variant <input_apk> <out_apk> <strip_abi or "">; globals: engine cli_jar patches_files keystore RELUME_REPO
patch_variant() {
	local in=$1 out=$2 strip=${3:-}
	case "$engine" in
	morphe)
		local args=(-jar "$cli_jar" patch) f
		for f in "${patches_files[@]}"; do args+=(-p "$f"); done
		args+=(--options-file options.json "--keystore=$RELUME_REPO/src/keystore/$keystore" --force --continue-on-error)
		[[ -n "$strip" ]] && args+=(--striplibs "$strip")
		args+=("--out=$out" "$in")
		java "${args[@]}" || { red_log "[-] Patch failed: $out"; return 1; }
		;;
	lspatch | npatch)
		# spec xposed 5.3: module(s) embedded by the engine; output = the only APK in an empty dir
		local creds odir a rc
		local -a ks cmd apks
		: "${engine_sigbypass:=}" "${engine_injectdex:=false}" # set by build-item.sh from job.json engine_options
		if ! creds=$(keystore_creds "$keystore"); then
			FAIL_CODE=internal
			LAST_ERR="unknown keystore"
			return 1
		fi
		if [[ ! "$engine_sigbypass" =~ ^[0-4]$ ]]; then
			FAIL_CODE=internal
			LAST_ERR="bad engine_options"
			return 1
		fi
		read -r -a ks <<<"$creds"
		odir="${out%.apk}.d"
		rm -rf "$odir"
		mkdir -p "$odir"
		cmd=(java -jar "$cli_jar" "$in" -k "$RELUME_REPO/src/keystore/$keystore" "${ks[0]}" "${ks[1]}" "${ks[2]}"
			-l "$engine_sigbypass")
		[ "$engine_injectdex" = "true" ] && cmd+=(--injectdex)
		for a in "${patches_files[@]}"; do
			cmd+=(-m "$a")
		done
		cmd+=(-o "$odir" -f)
		"${cmd[@]}"
		rc=$?
		if [ "$rc" -ne 0 ]; then
			LAST_ERR="engine exit $rc"
			return 1
		fi
		shopt -s nullglob
		apks=("$odir"/*.apk)
		shopt -u nullglob
		if [ "${#apks[@]}" -ne 1 ]; then
			LAST_ERR="engine produced ${#apks[@]} apks"
			return 1
		fi
		mv "${apks[0]}" "$out"
		rm -rf "$odir"
		;;
	*)
		FAIL_CODE=internal
		LAST_ERR="unknown engine $engine"
		return 1
		;;
	esac
	[[ -s "$out" ]] || { red_log "[-] Patch produced no file: $out"; return 1; }
}

# apk_info <apk> -> package=, version_code=, version_name= lines (APKEditor info, layout from the captured fixture)
apk_info() {
	local raw pkg code name
	raw=$(java -jar "$APKEditor" info -i "$1" -package -version-code -version-name 2>/dev/null | tr -d '\r"')
	pkg=$(grep -iE '^[[:space:]]*package' <<<"$raw" | head -1 | sed -E 's/^[^=:]*[=:][[:space:]]*//')
	code=$(grep -iE 'version[-_ ]?code' <<<"$raw" | head -1 | sed -E 's/^[^=:]*[=:][[:space:]]*//')
	name=$(grep -iE 'version[-_ ]?name' <<<"$raw" | head -1 | sed -E 's/^[^=:]*[=:][[:space:]]*//')
	[[ -n "$pkg" ]] || return 1
	printf 'package=%s\nversion_code=%s\nversion_name=%s\n' "$pkg" "$code" "$name"
}

# apk_signer <apk> -> lowercase SHA-256 of signer #1
apk_signer() {
	apksigner verify --print-certs "$1" 2>/dev/null | \
		sed -nE 's/^Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)$/\1/p' | head -1 | tr 'A-F' 'a-f' | grep .
}

split_editor() {
	if [[ -z "$3" || -z "$4" ]]; then
		green_log "[+] Merge splits apk to standalone apk"
		java -jar "$APKEditor" m -i "./download/$1" -o "./download/$1.apk" > /dev/null 2>&1
		return 0
	fi
	local include_files file filename basename_no_ext
	IFS=' ' read -r -a include_files <<< "$4"
	mkdir -p "./download/$2"
	for file in "./download/$1"/*.apk; do
		filename=$(basename "$file")
		basename_no_ext="${filename%.apk}"
		if [[ "$filename" == "base.apk" ]]; then
			cp -f "$file" "./download/$2/" > /dev/null 2>&1
			continue
		fi
		local listed=0
		case " ${include_files[*]} " in *" $basename_no_ext "*) listed=1 ;; esac
		if [[ "$3" == "include" && $listed -eq 1 ]] || [[ "$3" == "exclude" && $listed -eq 0 ]]; then
			cp -f "$file" "./download/$2/" > /dev/null 2>&1
		fi
	done

	green_log "[+] Merge splits apk to standalone apk"
	java -jar "$APKEditor" m -i "./download/$2" -o "./download/$2.apk" > /dev/null 2>&1
}
