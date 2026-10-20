#!/usr/bin/env bash
# Compose shape checks (spec core §1, §11). Runs `docker compose config` only; starts nothing.
set -o pipefail
cd "$(dirname "$0")/.." || exit 1
f=local/docker-compose.yml
fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1: $2"; fails=$((fails + 1)); }

for m in host bridge; do
	if RV_NETWORK_MODE=$m docker compose --env-file /dev/null -f "$f" config > /dev/null 2>&1; then pass "compose_$m"; else fail "compose_$m" "config failed"; fi
done
if env -u RV_NETWORK_MODE docker compose --env-file /dev/null -f "$f" config > /dev/null 2>&1; then
	fail compose_unset "config passed without RV_NETWORK_MODE"
else pass compose_unset; fi

j=$(RV_NETWORK_MODE=bridge docker compose --env-file /dev/null -f "$f" config --format json 2>/dev/null) || j='{}'
before=$fails
check() { jq -e "$1" <<<"$j" > /dev/null || fail compose_shape "$1"; }
check '.services | keys == ["apkmsolver","app","cfbypass","flaresolverr","netns"]'
check '[.services[] | select(.network_mode == "service:netns")] | length == 4'
check '.services.netns.network_mode == "bridge"'
check '.services.netns.ports[0].target == 47000'
check '.services.flaresolverr.environment.HOST == "127.0.0.1"'
check '.services.cfbypass.command as $c | ($c | index("--host")) as $i | $i != null and $c[$i + 1] == "127.0.0.1"'
check '[.services.app.volumes[] | select(.target == "/private/data") | .bind.create_host_path] == [false]'
check '[.services.app.volumes[] | select(.target == "/repo") | .read_only] == [true]'
check '.services.app.healthcheck.test | join(" ") | test("curl -sf http://127.0.0.1:47000/api/health")'
check '.services.app.environment.HOME == "/private/data/home"'
check '.services.app.environment.FLARESOLVERR_URL == "http://127.0.0.1:47191"'
check '.services.app.environment.CFBYPASS_URL == "http://127.0.0.1:47800"'
check '.services.app.environment.APKM_SOLVER_URL == "http://127.0.0.1:47900"'
check '[.services[] | .restart] | all(. == "unless-stopped")'
for name in RV_GITHUB_TOKEN_FILE RV_UID RV_GID RV_DATA_DIR RV_NETWORK_MODE RV_WEB_PORT; do
	grep -q "$name" "$f" || fail compose_shape "missing env name $name"
done
[ "$fails" -eq "$before" ] && pass compose_shape
[ "$fails" -eq 0 ] || exit 1
