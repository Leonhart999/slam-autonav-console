#!/bin/bash
# UI check in running states that tools/ui_smoke.sh never reaches (it starts no simulation):
#   A  simulation (warehouse) started from the Mapping page, Navigation page on a map of another world
#      (scene mismatch warning + restart button), plus Mapping and Monitor
#   B  FAST-LIO running (Mapping page with live SLAM controls)
#   C  Navigation page restarts the simulation with the map's world, then Nav2 runs
# Every state is checked in zh/en at 1707x985 and 1100x700 with the layout rules of tools/ui_smoke.mjs.
# Separate console (PORT 8013, DOMAIN 93); maps are read from the normal console's map library, runs and
# logs go to /tmp. Needs WSL (Windows Edge + node.exe, see ui_smoke.sh) and about 3 minutes.
#   bash tools/ui_states.sh <map name>      # a saved map whose world is not the warehouse, e.g. an office map
set -u
MAP=${1:?usage: ui_states.sh <map name of a non-warehouse world>}
HERE=$(cd "$(dirname "$0")/.." && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
[ -f ~/.config/robot_console/dev.env ] && . ~/.config/robot_console/dev.env
PORT=${PORT:-8013}
DOMAIN=${DOMAIN:-93}
WIN_NODE=${WIN_NODE:-$(command -v node.exe || true)}
[ -n "$WIN_NODE" ] && [ -x "$WIN_NODE" ] || { echo "Windows node.exe not found (set WIN_NODE)"; exit 2; }
if curl -s -m 1 -o /dev/null "http://127.0.0.1:$PORT/"; then echo "port $PORT is busy (set PORT)"; exit 2; fi
if pgrep -f "ign gazebo" > /dev/null; then echo "a Gazebo simulation is already running; stop it first"; exit 2; fi

set +u; source /opt/ros/humble/setup.bash; source "${ROS2_WS:-$HOME/ros2_ws}/install/setup.bash"; set -u
export ROS_DOMAIN_ID=$DOMAIN IGN_PARTITION=ui_states_$DOMAIN PATH=~/opt/node/bin:$PATH
D=/tmp/ui_states_$PORT; rm -rf $D; mkdir -p $D
read -r MAPS DATA < <(~/venvs/console/bin/python -c "from robot_console import config
c = config.load(); print(c['mapping']['maps_dir'], c['paths']['data_root'])")
OUT_WIN=${OUT_WIN:-$(mkdir -p "$DATA/ui_screens" && wslpath -w "$DATA/ui_screens")\\smoke_$STAMP}
OUT_WIN="${OUT_WIN/smoke_/states_}"
U=http://127.0.0.1:$PORT
(cd "$HERE/web" && npx vite build --outDir $D/dist --emptyOutDir > $D/build.log 2>&1) || { tail -20 $D/build.log; exit 2; }
sed -e "s/^  port: .*/  port: $PORT/" -e "s#^  web_dist: .*#  web_dist: $D/dist#" -e "s#^  log_dir: .*#  log_dir: $D/logs#" \
    -e "s#^  results_dir: .*#  results_dir: $D/exports#" -e "s#^  maps_dir: .*#  maps_dir: $MAPS#" \
    -e "s#^  runs_dir: .*#  runs_dir: $D/runs#" "$HERE/config/console.yaml" > $D/console.yaml
cp "$HERE/config/stacks.yaml" "$HERE/config/mapping_presets.yaml" $D/
setsid ros2 run robot_console console --config $D/console.yaml < /dev/null > $D/console.log 2>&1 &
trap 'pkill -INT -f "console --config $D/console.yaml"; sleep 8; pkill -KILL -f "console --config $D/console.yaml" 2>/dev/null' EXIT
for _ in $(seq 60); do curl -s -m 1 -o /dev/null $U/api/status && break; sleep 0.5; done
fail=0
smoke() {   # smoke <tag> <pages>
    for vp in 1707x985 1100x700; do
        out=$("$WIN_NODE" "$(wslpath -w "$HERE/tools/ui_smoke.mjs")" --base "http://localhost:$PORT" --out "$OUT_WIN" \
            --src "$(wslpath -w "$HERE/web/src")" --viewport $vp --pages "$2" --tag "${1}_${vp}_" --port 9343)
        code=$?
        echo "$out" | grep -E "FAIL|passed|^- "
        [ "$code" -eq 0 ] || fail=1
    done
}
wait_odom() { for _ in $(seq 40); do timeout 3 ros2 topic echo --once "$1" nav_msgs/msg/Odometry > /dev/null 2>&1 && return 0; done; return 1; }

echo "== A: warehouse simulation started from the Mapping page; Navigation page on $MAP"
curl -s -X POST "$U/api/stacks/sim/start?preset=warehouse&origin=mapping" -o /dev/null -w "sim start %{http_code}\n"
sleep 12
smoke A "navigation?map=$MAP,mapping,monitor"
echo "== B: FAST-LIO running"
curl -s -X POST "$U/api/stacks/mapping_fastlio/start" -o /dev/null -w "slam start %{http_code}\n"
wait_odom /Odometry || echo "no FAST-LIO odometry"
smoke B "mapping"
curl -s -X POST "$U/api/stacks/mapping_fastlio/stop" -o /dev/null -w "slam stop %{http_code}\n"
sleep 6
echo "== C: Navigation page restarts the simulation with the map's world, then Nav2 runs"
curl -s -X POST "$U/api/navigation/sim/restart?name=$MAP" -o /dev/null -w "sim restart %{http_code}\n"
sleep 25
curl -s -X POST "$U/api/navigation/start?name=$MAP" -o /dev/null -w "nav start %{http_code}\n"
sleep 25
smoke C "navigation?map=$MAP,monitor"
curl -s -X POST "$U/api/navigation/stop" -o /dev/null -w "nav stop %{http_code}\n"
sleep 5
curl -s -X POST "$U/api/navigation/sim/stop?name=$MAP" -o /dev/null -w "sim stop %{http_code}\n"
sleep 5
echo "screenshots and summary: $OUT_WIN"
exit $fail
