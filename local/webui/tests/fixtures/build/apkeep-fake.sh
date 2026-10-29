#!/usr/bin/env bash
# Fake apkeep: -a <pkg> ... <outdir> -> <outdir>/<pkg>.apk; fails with SHIM_APKEEP_FAIL=1.
[ "${SHIM_APKEEP_FAIL:-0}" = 1 ] && { echo "login failed"; exit 1; }
pkg="" prev=""
for a in "$@"; do [ "$prev" = -a ] && pkg=$a; prev=$a; done
printf 'apk' > "${!#}/$pkg.apk"
