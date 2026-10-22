#!/usr/bin/env bash
# apksigner stand-in for the project release tests: CDCD... when the APK path contains "resigned"
echo "Signer #1 certificate DN: CN=test"
if [[ "${!#}" == *resigned* ]]; then
	echo "Signer #1 certificate SHA-256 digest: CDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCDCD"
else
	echo "Signer #1 certificate SHA-256 digest: ABABABABABABABABABABABABABABABABABABABABABABABABABABABABABABABAB"
fi
