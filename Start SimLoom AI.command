#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  printf '%s\n' 'Please run ./install.sh first.'
  exit 1
fi
export PYTHONPATH="$PWD/src"
exec .venv/bin/python -m engineering_agent.desktop "$@"
