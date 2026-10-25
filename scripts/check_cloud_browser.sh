#!/usr/bin/env bash
# Actual browser test on synthetic pages only. No network, ports, sessions or orders.
set -euo pipefail
profile="${FINDER_BROWSER_PROFILE:-standard}"
case "$profile" in
  standard) memory=2g; cpus=2; deadline=120 ;;
  micro) memory=1g; cpus=0.125; deadline=600 ;;
  *) echo 'Unsupported browser resource profile' >&2; exit 64 ;;
esac
image="finder-browser-smoke:${GITHUB_RUN_ID:-local}-${profile}-$$"
container="finder-browser-smoke-${profile}-$$"
trap 'docker rm -f "$container" >/dev/null 2>&1 || true; docker image rm "$image" >/dev/null 2>&1 || true' EXIT
docker build --file deployment/BrowserSmoke.Dockerfile --tag "$image" .
[[ "$(docker image inspect --format '{{.Config.User}}' "$image")" == '10001:10001' ]]
# Build resources are not the benchmark. Only the isolated runtime has these limits.
run_started=$SECONDS
set +e
timeout --signal=TERM --kill-after=10 "$deadline" docker run --name "$container" --init --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory "$memory" --memory-swap "$memory" --cpus "$cpus" --pids-limit 256 --shm-size 256m \
  --env "FINDER_BROWSER_PROFILE=$profile" \
  --tmpfs /tmp:rw,nosuid,size=512m,mode=1777 "$image"
run_status=$?
set -e
# Keep the stopped container just long enough to establish OOM and exit state.
state="$(docker inspect --format '{{json .State}}' "$container")"
STATE_JSON="$state" PROFILE="$profile" RUNTIME_SECONDS="$((SECONDS-run_started))" node --input-type=module -e '
const s=JSON.parse(process.env.STATE_JSON);
console.log(JSON.stringify({profile:process.env.PROFILE,runtime_wall_seconds:Number(process.env.RUNTIME_SECONDS),container_running:s.Running,container_oom_killed:s.OOMKilled,container_exit_code:s.ExitCode}));
if(s.Running!==false || s.OOMKilled!==false || s.ExitCode!==0) process.exit(1);
'
[[ "$run_status" == 0 ]]
