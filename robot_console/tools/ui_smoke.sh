#!/bin/bash
# UI smoke test (manual, not part of the pre-commit hook): starts a separate console instance without any
# simulation and lets headless Edge open every page in Chinese and English (tools/ui_smoke.mjs).
# Needs WSL: the browser is the Windows Edge, driven by the Windows node.exe.
#   bash tools/ui_smoke.sh            # screenshots + summary in <data_root>/ui_screens/smoke_<time>/
# Env: PORT (8010), DOMAIN (90), OUT_WIN (Windows output dir), WIN_NODE (Windows node.exe, see below),
#      VIEWPORT (1440x1000; the laptop at 150 % scaling gives a maximised --app window of about 1707x985).
#      Machine-specific defaults for these can go in ~/.config/robot_console/dev.env (sourced, may use $STAMP).
# The instance reads (never writes) the maps and runs of the normal console configuration.
# Uses the working tree as it is (uncommitted changes included). Exit code 1 when any page fails.
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
[ -f ~/.config/robot_console/dev.env ] && . ~/.config/robot_console/dev.env
PORT=${PORT:-8010}
DOMAIN=${DOMAIN:-90}
VIEWPORT=${VIEWPORT:-1440x1000}
# Windows Node: WSL's default NAT network cannot reach the Windows-side DevTools port, Windows can reach WSL
WIN_NODE=${WIN_NODE:-$(command -v node.exe || true)}
D=/tmp/ui_smoke_$PORT
CFG=$D/console.yaml

[ -n "$WIN_NODE" ] && [ -x "$WIN_NODE" ] || { echo "Windows node.exe not found (set WIN_NODE)"; exit 2; }
if curl -s -m 1 -o /dev/null "http://127.0.0.1:$PORT/"; then echo "port $PORT is busy (set PORT)"; exit 2; fi

set +u; source /opt/ros/humble/setup.bash
source "${ROS2_WS:-$HOME/ros2_ws}/install/setup.bash"; set -u
export ROS_DOMAIN_ID=$DOMAIN IGN_PARTITION=ui_smoke_$DOMAIN
export PATH=~/opt/node/bin:$PATH
mkdir -p $D
# Effective folders of the normal console (shipped config + ~/.config/robot_console/console.yaml)
read -r MAPS RUNS DATA < <(~/venvs/console/bin/python -c "from robot_console import config
c = config.load(); print(c['mapping']['maps_dir'], c['mapping']['runs_dir'], c['paths']['data_root'])")
OUT_WIN=${OUT_WIN:-$(mkdir -p "$DATA/ui_screens" && wslpath -w "$DATA/ui_screens")\\smoke_$STAMP}
echo "building web/ into $D/dist ..."
(cd "$HERE/web" && npx vite build --outDir $D/dist --emptyOutDir > $D/build.log 2>&1) || { tail -20 $D/build.log; exit 2; }
sed -e "s/^  port: .*/  port: $PORT/" -e "s#^  web_dist: .*#  web_dist: $D/dist#" -e "s#^  log_dir: .*#  log_dir: $D/logs#" \
    -e "s#^  results_dir: .*#  results_dir: $D/exports#" -e "s#^  maps_dir: .*#  maps_dir: $MAPS#" \
    -e "s#^  runs_dir: .*#  runs_dir: $RUNS#" "$HERE/config/console.yaml" > $CFG
cp "$HERE/config/stacks.yaml" "$HERE/config/mapping_presets.yaml" $D/
setsid ros2 run robot_console console --config $CFG < /dev/null > $D/console.log 2>&1 &
stop() { pkill -INT -f "console --config $CFG"; sleep 2; pkill -KILL -f "console --config $CFG" 2>/dev/null; }
trap stop EXIT
for i in $(seq 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:$PORT/api/status" && break; sleep 0.5; done
curl -s -m 2 -o /dev/null "http://127.0.0.1:$PORT/api/status" || { tail -20 $D/console.log; exit 2; }

"$WIN_NODE" "$(wslpath -w "$HERE/tools/ui_smoke.mjs")" --base "http://localhost:$PORT" --out "$OUT_WIN" \
    --src "$(wslpath -w "$HERE/web/src")" --viewport "$VIEWPORT"
code=$?
grep -E "Traceback|ERROR|Exception" $D/console.log | head -5 | sed 's/^/console.log: /'
exit $code
