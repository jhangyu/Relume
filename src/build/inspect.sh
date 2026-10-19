#!/usr/bin/env bash
# Patch inspection for the catalog (spec build §3.1.1). No network, no APKMirror.
# Usage: bash src/build/inspect.sh <cli_jar> <package> <out_dir> <patches_files...>
# Writes <out_dir>/options.json and <out_dir>/list-patches.txt; exit 0 only when both are non-empty.
set -o pipefail
if [[ $# -lt 4 ]]; then
	echo "[-] usage: inspect.sh <cli_jar> <package> <out_dir> <patches_files...>"
	exit 2
fi
cli=$1 pkg=$2 out=$3
shift 3
mkdir -p "$out" || { echo "[-] cannot create $out"; exit 1; }
p_args=() lp_args=()
for f in "$@"; do p_args+=(-p "$f"); done
lp_args=(--patches "$@")
java -jar "$cli" options-create "${p_args[@]}" -f "$pkg" -o "$out/options.json"
oc_rc=$?
java -jar "$cli" list-patches --with-packages --with-versions --with-options -f "$pkg" "${lp_args[@]}" |
	sed -E 's/\x1b\[[0-9;]*m//g' > "$out/list-patches.txt"
lp_rc=$?
if [[ $oc_rc -ne 0 || $lp_rc -ne 0 || ! -s "$out/options.json" || ! -s "$out/list-patches.txt" ]]; then
	echo "[-] inspect failed for $pkg (options-create rc=$oc_rc, list-patches rc=$lp_rc)"
	exit 1
fi
echo "[+] inspected $pkg"
