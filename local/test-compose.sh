#!/usr/bin/env bash
# Compose shape checks (spec core §1, §11). Runs `docker compose config` only; starts nothing.
set -o pipefail
cd "$(dirname "$0")/.." || exit 1
f=local/docker-compose.yml
fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1: $2"; fails=$((fails + 1)); }

for m in host bridge; do
	if RELUME_NETWORK_MODE=$m docker compose --env-file /dev/null -f "$f" config > /dev/null 2>&1; then pass "compose_$m"; else fail "compose_$m" "config failed"; fi
done
if env -u RELUME_NETWORK_MODE docker compose --env-file /dev/null -f "$f" config > /dev/null 2>&1; then
	fail compose_unset "config passed without RELUME_NETWORK_MODE"
else pass compose_unset; fi

j=$(RELUME_NETWORK_MODE=bridge docker compose --env-file /dev/null -f "$f" config --format json 2>/dev/null) || j='{}'
before=$fails
# the build-only upstream-browser service (replicas 0, never started) is the browser target's base: assert it, then leave it out of the runtime-shape checks
jq -e '.services["upstream-browser"].deploy.replicas == 0' <<<"$j" > /dev/null || fail compose_shape "upstream-browser is not replicas 0"
j=$(jq '.services |= with_entries(select(.value.deploy.replicas != 0))' <<<"$j")
check() { jq -e "$1" <<<"$j" > /dev/null || fail compose_shape "$1"; }
check '.services | keys == ["apkmsolver","app","cfbypass","flaresolverr","netns"]'
check '[.services[] | select(.network_mode == "service:netns")] | length == 4'
check '.services.netns.network_mode == "bridge"'
check '.services.netns.ports[0].target == 47000'
check '.services.flaresolverr.environment.HOST == "127.0.0.1"'
check '.services.cfbypass.command as $c | ($c | index("--host")) as $i | $i != null and $c[$i + 1] == "127.0.0.1"'
check '[.services.app.volumes[] | select(.target == "/private/data") | .bind.create_host_path] == [false]'
check '[.services.app.volumes[] | select(.target | startswith("/repo/src/keystore/")) | .read_only] == [true, true, true]'
check '[.services.app.volumes[] | select(.target == "/repo")] | length == 0'
check '.services.app.healthcheck.test | join(" ") | test("curl -sf http://127.0.0.1:47000/api/health")'
check '.services.app.environment.HOME == "/private/data/home"'
check '.services.app.environment.FLARESOLVERR_URL == "http://127.0.0.1:47191"'
check '.services.app.environment.CFBYPASS_URL == "http://127.0.0.1:47800"'
check '.services.app.environment.APKM_SOLVER_URL == "http://127.0.0.1:47900"'
check '[.services[] | .restart] | all(. == "unless-stopped")'
for name in RELUME_GITHUB_TOKEN_FILE RELUME_UID RELUME_GID RELUME_DATA_DIR RELUME_NETWORK_MODE RELUME_WEB_PORT; do
	grep -q "$name" "$f" || fail compose_shape "missing env name $name"
done
[ "$fails" -eq "$before" ] && pass compose_shape

# Portainer stack must not drift from the main compose file (spec core §11); only its fixed container names differ.
pj=$(RELUME_NETWORK_MODE=bridge RELUME_KEYSTORE_DIR=/x RELUME_DATA_DIR=/x docker compose --env-file /dev/null -f local/docker-compose-portainer.yml config --format json 2>/dev/null) || pj='{}'
pbefore=$fails
same() { [ "$(jq -cS "$1" <<<"$j")" = "$(jq -cS "$1" <<<"$pj")" ] || fail portainer_drift "$1"; }
same '.services | keys'
same '.services.app.environment'
same '.services.app.cap_add'
same '.services.app.cap_drop'
same '.services.app.command'
same '.services.app.healthcheck'
same '.services.app.user'
same '[.services[] | .network_mode]'
same '.services.flaresolverr | del(.container_name)'
same '.services.cfbypass | del(.build, .container_name)'
same '.services.apkmsolver | del(.build, .container_name)'
[ "$fails" -eq "$pbefore" ] && pass portainer_same
[ "$fails" -eq 0 ] || exit 1
