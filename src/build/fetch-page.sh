#!/usr/bin/env bash
# APKMirror lookup page (spec marketplace §5.2): one page through the solver chain, its HTML written to <out>.
# Run isolated by local/webui/apklocate.py; exit 1 when FlareSolverr and CFBypass both fail.
# Usage: bash src/build/fetch-page.sh <url> <out>
set -o pipefail
RELUME_REPO=$(cd "$(dirname "$0")/../.." && pwd)
# shellcheck source=src/build/utils.sh
source "$RELUME_REPO/src/build/utils.sh"

url=${1:-}
out=${2:-}
[[ -n "$url" && -n "$out" ]] || { echo "[-] usage: fetch-page.sh <url> <out>"; exit 2; }
html=""
_cf_get "$url" || { echo "[-] page fetch failed: $url"; exit 1; }
printf '%s' "$html" >"$out"
