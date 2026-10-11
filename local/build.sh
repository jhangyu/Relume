#!/usr/bin/env bash
# Run src/build/*.sh locally in Docker. Usage: ./local/build.sh <org|all> [run]
set -u
cd "$(dirname "$0")/.."
compose=(docker compose -f local/docker-compose.yml)

# GitHub token: host file, bind-mounted read-only at /run/secrets/github_token (docker-compose.yml).
# Never read or printed on the host. Empty file = unauthenticated (60 req/h per IP).
export RV_GITHUB_TOKEN_FILE=${RV_GITHUB_TOKEN_FILE:-$PWD/src/keystore/github_token}
[ -e "$RV_GITHUB_TOKEN_FILE" ] || { mkdir -p "$(dirname "$RV_GITHUB_TOKEN_FILE")" && (umask 077 && : >"$RV_GITHUB_TOKEN_FILE"); }
# Expanded inside the builder only (single quotes): the docker command line never carries the value.
tok='[ -s /run/secrets/github_token ] && export GITHUB_TOKEN=$(tr -d "[:space:]" </run/secrets/github_token);'

# org | workflow option name (.github/workflows/manual-patch.yml) | script | matrix runs ("-" = no matrix)
ORGS="
morphe|Morphe|morphe.sh|1 2 3
morphe-beta|Morphe Beta|morphe-beta.sh|1 2 3
rve-anddea-stable|RVE-anddea-stable|Anddea-Revanced-Extended.sh|1 2
rve-anddea-beta|RVE-anddea-beta|Anddea-Revanced-Extended-Beta.sh|1 2
piko-stable|Twitter Piko Stable|Piko.sh|1 2
piko-beta|Twitter Piko Beta|Piko-Beta.sh|1 2
piko-newx|Piko NewX|Piko-Newx.sh|-
derevanced|DeRevanced|DeRevanced.sh|1 2 3 4 5 6 7 8 9 10
hushfacebook|HushFacebook|HushFacebook.sh|-
hoo-dles|hoo-dles|hoo-dles.sh|1 2 3 4 5
bufferk|bufferk|bufferk.sh|1 2
arandomhooman|arandomhooman|arandomhooman.sh|1 2
icysymmetra|icysymmetra|icysymmetra.sh|-
docbt|docbt|docbt.sh|-
rushiranpise|rushiranpise|rushiranpise.sh|1 2 3 4
biliroamingm|BiliRoamingM|BiliRoamingM.sh|-
scrazzz-patches|Scrazzz Patches|scrazzz-revanced.sh|-
dropped-patches|Dropped Patches - indrastorms|Dropped-Patches-Revanced.sh|-
"

if [ $# -eq 0 ]; then
	echo "Usage: $0 <org|all> [run]"
	echo "Orgs:"
	grep '|' <<<"$ORGS" | awk -F'|' '{ printf "  %-20s %-32s runs: %s\n", $1, $2, $4 }'
	echo "GitHub token file: $RV_GITHUB_TOKEN_FILE (fine-grained token, public repositories read-only)"
	echo "After changing the token: printf %s TOKEN > file, or docker compose -f local/docker-compose.yml up -d --force-recreate builder"
	exit 0
fi

want=$1 only_run=${2:-}
rows=$(grep '|' <<<"$ORGS" | awk -F'|' -v w="$want" 'w == "all" || $1 == w')
[ -n "$rows" ] || { echo "Unknown org: $want (run without args for the list)"; exit 2; }

"${compose[@]}" up -d --build >/dev/null || exit 1
"${compose[@]}" exec -T builder sh -c \
	'curl -sf --retry 30 --retry-delay 2 --retry-all-errors -o /dev/null localhost:$FLARESOLVERR_PORT' \
	|| { echo "FlareSolverr not ready"; exit 1; }
"${compose[@]}" exec -T builder bash -c "$tok"'
	code=$(curl -s -o /tmp/rl.json -w "%{http_code}" ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} https://api.github.com/rate_limit)
	if [ "$code" = 401 ]; then echo "GitHub API: token rejected (HTTP 401)"
	else echo "GitHub API: $([ -n "${GITHUB_TOKEN:-}" ] && echo authenticated || echo unauthenticated) remaining=$(jq -r .rate.remaining /tmp/rl.json 2>/dev/null)"; fi' </dev/null

# One org's failure never stops the next; a run passes only if it yields at least one APK.
summary="" failed=0
while IFS='|' read -r org _ script runs; do
	org_ok=1
	for run in $runs; do
		[ -n "$only_run" ] && [ "$run" != "$only_run" ] && [ "$run" != "-" ] && continue
		arg=$([ "$run" = "-" ] || echo "$run")
		work="local/work/$org-$run"
		rm -rf "$work" && mkdir -p "$work" "local/out/$org"
		ln -s ../../../src "$work/src"
		echo "=== $org run $run ($script $arg) ==="
		"${compose[@]}" exec -T -w "/repo/$work" -e GITHUB_OUTPUT="/repo/$work/github_output" builder bash -c \
			"$tok bash src/etc/connection.sh && grep -q 'internet_error=0' github_output && bash src/build/$script $arg" </dev/null
		rc=$?
		n=$(find "$work/release" -maxdepth 1 -name '*.apk' 2>/dev/null | wc -l | tr -d ' ')
		[ "$n" -gt 0 ] && cp "$work"/release/*.apk "local/out/$org/"
		if [ "$rc" -eq 0 ] && [ "$n" -gt 0 ]; then res=PASS; else res=FAIL; org_ok=0; fi
		summary+="$res  $org run $run  (exit $rc, $n apk)"$'\n'
	done
	[ "$org_ok" -eq 1 ] || failed=1
done <<<"$rows"

echo "===== Summary ====="
printf '%s' "$summary"
echo "APKs: local/out/"
exit "$failed"
