#!/usr/bin/env bash
# Fake runner: prints ::fail::<job.json package>::... (the test puts the fail code in "package")
code=$(jq -r .package "$1")
echo "[-] something broke"
echo "::fail::$code::detail of $code"
exit 1
