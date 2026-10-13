#!/usr/bin/env bash
# Render every style layout in specs/styles/ to out/styles/<name>.mp4 (+ final PNG).
# Usage: bash render_styles.sh [vertical|landscape|all]   (default: vertical)
set -euo pipefail
cd "$(dirname "$0")"
which="${1:-vertical}"
mkdir -p out/styles
for spec in specs/styles/*_vertical.json specs/styles/*_landscape.json; do
  [ -e "$spec" ] || continue
  name=$(basename "$spec" .json)
  case "$which" in all) ;; *) [[ "$name" == *"_$which" ]] || continue ;; esac
  "${PYTHON:-.venv/bin/python}" statanim.py "$spec" -o "out/styles/$name.mp4" --final-png "out/styles/${name}_final.png"
done
