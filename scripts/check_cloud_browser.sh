#!/usr/bin/env bash
# Actual browser test on synthetic pages only. No network, ports, sessions or orders.
set -euo pipefail
image="finder-browser-smoke:${GITHUB_RUN_ID:-local}-$$"
container="finder-browser-smoke-$$"
trap 'docker rm -f "$container" >/dev/null 2>&1 || true; docker image rm "$image" >/dev/null 2>&1 || true' EXIT
docker build --file deployment/BrowserSmoke.Dockerfile --tag "$image" .
[[ "$(docker image inspect --format '{{.Config.User}}' "$image")" == '10001:10001' ]]
timeout 120 docker run --rm --name "$container" --init --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 2g --cpus 2 --pids-limit 256 --shm-size 256m \
  --tmpfs /tmp:rw,nosuid,size=512m,mode=1777 "$image"
