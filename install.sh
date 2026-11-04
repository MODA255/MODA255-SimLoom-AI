#!/usr/bin/env sh
set -eu
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
printf '%s\n' 'Engineering Agent installed. Activate with: . .venv/bin/activate'
