#!/usr/bin/env bash
# inspect.sh stand-in: copies the captured fixtures into <out_dir> ($3)
d=$(dirname "$0")
cp "$d/list-patches.txt" "$d/options.json" "$3/"
echo "[+] inspected $2"
