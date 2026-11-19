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
  output=$(mktemp "${TMPDIR:-/tmp}/simloom-browser.XXXXXX")
  set +e
  node "$script" 2>&1 | tee "$output"
  pipeline_status=(${PIPESTATUS[@]})
  set -e
  last_status=${pipeline_status[0]}
  if (( last_status == 0 )); then
    rm -f "$output"
    exit 0
  fi
  # Hosted-runner logs are not always available through the public Actions API.
  # Keep the bounded tail in an annotation so the failing assertion remains
  # visible from the check run itself without uploading arbitrary output.
  detail=$(tail -c 6000 "$output" | tr '\r\n' '  ' | sed 's/%/%25/g; s/:/%3A/g; s/,/%2C/g')
  echo "::error title=browser test ${script} attempt ${attempt}::${detail}"
  rm -f "$output"
  if (( attempt < attempts )); then
    sleep 2
  fi
done
exit "$last_status"
