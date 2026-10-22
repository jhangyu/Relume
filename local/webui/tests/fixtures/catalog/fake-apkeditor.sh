#!/usr/bin/env bash
# APKEditor `info` stand-in (layout of src/build/test-fixtures/apkeditor-info.txt): com.example.other when the -i path
# contains "other"
pkg=app.revanced.android.gms
while [[ $# -gt 0 ]]; do
	if [[ "$1" == "-i" && "$2" == *other* ]]; then pkg=com.example.other; fi
	shift
done
echo "package=\"$pkg\""
echo 'VersionCode="255070218"'
echo 'VersionName="7.2.1"'
