#!/bin/bash
# Quick post-install check: ROS packages found, Python environments import, and the console answers HTTP.
# The console is started on a spare port (8099) and ROS domain (97) so a running console or simulation is untouched.
# Usage: bash <workspace>/src/robot_console/install/check.sh   (workspace: $ROS2_WS, default ~/ros2_ws)
set -uo pipefail
PORT=8099
fail=0
ok()  { printf '  \033[32mok\033[0m   %s\n' "$*"; }
bad() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=1; }

WS="${ROS2_WS:-$HOME/ros2_ws}"
CONSOLE_DIR=$(cd "$(dirname "$0")/.." && pwd)
SIM_DIR=$(dirname "$(grep -lsE "<name>sim_robot</name>" "$WS"/src/*/package.xml "$WS"/src/*/*/package.xml | head -1)")
set +u; source /opt/ros/humble/setup.bash; source "$WS/install/setup.bash"; set -u
for pkg in robot_console sim_robot lidar_nav fast_lio livox_ros_driver2 nav2_bringup ros_gz_sim pointcloud_to_laserscan; do
    if ros2 pkg prefix "$pkg" > /dev/null 2>&1; then ok "ROS package $pkg"; else bad "ROS package $pkg not found"; fi
done
command -v ign > /dev/null && ok "Gazebo (ign) $(ign gazebo --versions 2>/dev/null | head -1)" || bad "Gazebo 'ign' command not found"
"$HOME/venvs/console/bin/python" -c "import fastapi, uvicorn, multipart, websockets, pcd2grid" 2>/dev/null \
    && ok "console venv (fastapi, uvicorn, pcd2grid)" || bad "console venv imports failed (~/venvs/console)"
"$HOME/venvs/evo/bin/python" -c "import evo, numpy" 2>/dev/null \
    && ok "evo venv" || bad "evo venv imports failed (~/venvs/evo)"
[ -f "$CONSOLE_DIR/web/dist/index.html" ] && ok "web frontend built" || bad "web/dist/index.html missing"
[ -f "$SIM_DIR/models/fuel/Warehouse/model.sdf" ] && ok "Gazebo Fuel models (warehouse world)"     || bad "Gazebo Fuel models missing: bash $SIM_DIR/tools/fetch_fuel_models.sh"
[ -d "$WS/src/FAST_LIO/Log" ] && ok "FAST_LIO/Log exists" || bad "FAST_LIO/Log missing (FAST-LIO crashes on shutdown without it)"
grep -q "map_file_path: \"$HOME/results/" "$SIM_DIR/config/fastlio_sim_vlp16.yaml" \
    && ok "FAST-LIO map_file_path under $HOME/results" || bad "map_file_path in sim_robot/config/fastlio_sim_vlp16.yaml does not point to $HOME/results"

# Start the console on a spare port with a copy of the installed config.
cfg_dir=$(mktemp -d)
cp "$(ros2 pkg prefix robot_console)/share/robot_console/config/"*.yaml "$cfg_dir/"
sed -i -E "s/^(  port:) *[0-9]+/\1 $PORT/" "$cfg_dir/console.yaml"
# Run the executable directly (not through `ros2 run`) so the signal below reaches the console itself.
console_bin="$(ros2 pkg prefix robot_console)/lib/robot_console/console"
ROS_DOMAIN_ID=97 "$console_bin" --config "$cfg_dir/console.yaml" > "$cfg_dir/console.log" 2>&1 < /dev/null &
pid=$!
up=0
for _ in $(seq 40); do
    if curl -s -m 1 "http://127.0.0.1:$PORT/api/stacks" > "$cfg_dir/stacks.json" 2>/dev/null && [ -s "$cfg_dir/stacks.json" ]; then up=1; break; fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
done
if [ "$up" -eq 1 ] && curl -s -m 2 "http://127.0.0.1:$PORT/" | grep -qi "<html"; then
    ok "console answers on port $PORT (API and web page)"
else
    bad "console did not start; last log lines:"; tail -15 "$cfg_dir/console.log" | sed 's/^/       /'
fi
# SIGTERM, not SIGINT: background jobs of a non-interactive shell ignore SIGINT. uvicorn handles SIGTERM and the
# console then runs its normal cleanup.
kill -TERM "$pid" 2>/dev/null
for _ in $(seq 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
kill -KILL "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
rm -rf "$cfg_dir"

if [ "$fail" -eq 0 ]; then echo "All checks passed."; else echo "Some checks failed."; fi
exit "$fail"
