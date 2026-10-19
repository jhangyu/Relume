#!/usr/bin/env bash
echo "ENVKEYS $(env | cut -d= -f1 | sort | tr '\n' ' ')"
echo "::version::9.9.9"
exit 1
