#!/usr/bin/env bash
# Version check (spec per-app-update-build §3.2): one APKMirror list-page request, prints ::apkm_versions::<csv>.
# Usage: bash src/build/check-versions.sh <list_url>
set -o pipefail
RELUME_REPO=$(cd "$(dirname "$0")/../.." && pwd)
# shellcheck source=src/build/utils.sh
source "$RELUME_REPO/src/build/utils.sh"

list_url=${1:-}
[[ -n "$list_url" ]] || { echo "[-] usage: check-versions.sh <list_url>"; exit 2; }
html=""
_cf_get "$list_url" || { echo "[-] APKMirror list page fetch failed: $list_url"; exit 1; }
versions=$(emit_apkm_versions)
[[ -n "$versions" ]] || { echo "[-] no versions found on $list_url"; exit 1; }
echo "$versions"
