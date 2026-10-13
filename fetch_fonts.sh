#!/usr/bin/env bash
# Fetch the kit's OFL fonts from github.com/google/fonts into fonts/.
# Run after creating the venv (fontTools from requirements.txt is needed to make the
# static Inter/Oswald weights). Usage: bash fetch_fonts.sh [--force]
set -euo pipefail
cd "$(dirname "$0")"
exec "${PYTHON:-.venv/bin/python}" fetch_fonts.py "$@"
