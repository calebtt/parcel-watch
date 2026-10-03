#!/bin/sh
# Build dist/parcel-watch.pyz: a single file that runs with python3 (3.9+).
set -eu
cd "$(dirname "$0")/.."
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp -r parcel_watch "$stage/"
find "$stage" -name __pycache__ -prune -exec rm -rf {} +
printf 'import sys\nfrom parcel_watch.cli import main\nsys.exit(main())\n' > "$stage/__main__.py"
mkdir -p dist
python3 -m zipapp "$stage" -p "/usr/bin/env python3" -o dist/parcel-watch.pyz
echo "built dist/parcel-watch.pyz"
