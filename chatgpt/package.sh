#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Rebuild from scratch so removed files do not linger in an older ZIP.
rm -f ../mataroa-plugin.zip
zip -r ../mataroa-plugin.zip plugin.json mcp.json assets skills \
    -x '*/.DS_Store' '*/__pycache__/*' '*.pyc'
