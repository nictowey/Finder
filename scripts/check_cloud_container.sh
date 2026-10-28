#!/usr/bin/env bash
# Disposable CI-only image test: no credentials, published ports, registry push or egress.
set -euo pipefail
image="finder-cloud-check:${GITHUB_RUN_ID:-local}-$$"
container="finder-cloud-check-$$"
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build --file deployment/Dockerfile --tag "$image" .
[[ "$(docker image inspect --format '{{.Config.User}}' "$image")" == '10001:10001' ]]
# The production entrypoint must reject missing deployment configuration.
if timeout 20 docker run --name "$container" --network none --read-only "$image"; then
  echo 'ERROR: unconfigured container started successfully' >&2
  exit 1
else
  status=$?
  [[ "$status" != 124 && "$status" != 137 ]]
  [[ "$(docker inspect --format '{{.State.Status}}' "$container")" == exited ]]
  # Require an actual application configuration failure, not an unrelated launch error.
  docker logs "$container" 2>&1 | grep -q 'FINDER_CLOUD_ORIGIN'
fi
docker rm "$container" >/dev/null

# Initialize only a new synthetic rules file, then exec the image's unchanged CMD.
mapfile -t image_command < <(docker image inspect --format '{{json .Config.Cmd}}' "$image" | python -c 'import json,sys; print("\n".join(json.load(sys.stdin)))')
docker run --detach --name "$container" --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /data:rw,noexec,nosuid,size=16m,uid=10001,gid=10001 \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --env FINDER_CLOUD_ORIGIN=https://finder.example \
  --env FINDER_CLOUD_OWNER_EMAIL=owner@example.test \
  --env FINDER_CLOUD_AUTH_URL=https://auth.invalid \
  --env FINDER_CLOUD_DATABASE=/data/rules.db \
  --env FINDER_CLOUD_MONITOR=0 --env FINDER_CLOUD_LISTING_READ=0 \
  --entrypoint python "$image" -c 'import os,sys; from pathlib import Path; from finder.discogs_rules import RulesStore; RulesStore(Path("/data/rules.db"), create=True).close(); os.execvp(sys.argv[1], sys.argv[1:])' "${image_command[@]}" >/dev/null
# HTTP is exercised inside the container's isolated network namespace only.
docker exec -i "$container" python - <<'PY'
import json
import os
import time
import urllib.error
import urllib.request

assert os.getuid() == 10001

def request(path, *, host="finder.example", body=None, origin=None):
    headers = {"Host": host, "Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request("http://127.0.0.1:8080" + path, body, headers)
    try:
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())

for attempt in range(30):
    try:
        health = request("/health")
        break
    except urllib.error.URLError:
        time.sleep(0.2)
else:
    raise AssertionError("Container HTTP startup timed out")
assert health == (200, {"status": "ok", "checkout_available": False})
assert request("/health", host="foreign.example")[0] == 421
assert request("/")[0] == 401
assert request("/api/snapshot")[0] == 401
assert request("/api/target", body=b"{}", origin="https://foreign.example")[0] == 403
assert request("/api/target", body=b"{}", origin="https://finder.example")[0] == 401
assert request("/api/checkout", body=b"{}", origin="https://finder.example")[0] == 401
print("Container passed: non-root, production entrypoint, isolated HTTP health, host/origin/auth rejection; no provider or checkout access.")
PY
