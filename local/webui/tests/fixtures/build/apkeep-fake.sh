#!/usr/bin/env bash
# Fake apkeep: -a <pkg>[@code] ... <outdir> -> <outdir>/<pkg>.apk; SHIM_APKEEP_FAIL=1 fails, SHIM_APKEEP_EMPTY=1 writes nothing.
[ "${SHIM_APKEEP_FAIL:-0}" = 1 ] && { echo "login failed"; exit 1; }
[ "${SHIM_APKEEP_EMPTY:-0}" = 1 ] && exit 0
pkg="" prev=""
for a in "$@"; do [ "$prev" = -a ] && pkg=${a%%@*}; prev=$a; done
printf 'apk' > "${!#}/$pkg.apk"
