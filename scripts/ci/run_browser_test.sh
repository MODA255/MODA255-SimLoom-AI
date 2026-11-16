#!/usr/bin/env bash
set -u

script=${1:?browser test script is required}
attempts=${SIMLOOM_BROWSER_ATTEMPTS:-2}
if ! [[ "$attempts" =~ ^[1-9][0-9]*$ ]]; then
  echo "SIMLOOM_BROWSER_ATTEMPTS must be a positive integer" >&2
  exit 2
fi

last_status=1
for ((attempt=1; attempt<=attempts; attempt++)); do
  echo "browser test ${script}: attempt ${attempt}/${attempts}"
  node "$script" && exit 0
  last_status=$?
  if (( attempt < attempts )); then
    sleep 2
  fi
done
exit "$last_status"
