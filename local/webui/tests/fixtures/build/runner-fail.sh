#!/usr/bin/env bash
# Fake runner: prints ::fail::<job.json key>::... (the test puts the fail code in "key")
code=$(jq -r .key "$1")
echo "[-] something broke"
echo "::fail::$code::detail of $code"
exit 1
