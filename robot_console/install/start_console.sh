#!/bin/bash
# Start the web console and open its UI. Works on WSL2 (opens a Windows browser window) and native Ubuntu.
# Usage: bash <workspace>/src/robot_console/install/start_console.sh   (workspace: $ROS2_WS, default ~/ros2_ws)
#   - console already running: just opens the UI
#   - otherwise starts it in this terminal and opens the UI once it answers
# Quit with Ctrl+C in this terminal (processes started from the UI are stopped first).
set +u
source /opt/ros/humble/setup.bash
source "${ROS2_WS:-$HOME/ros2_ws}/install/setup.bash"
# Port of the effective configuration (shipped console.yaml + ~/.config/robot_console/console.yaml)
PORT=$(python3 -c "from robot_console import config; print(config.load()['server']['port'])" 2>/dev/null)
URL="http://localhost:${PORT:-8000}"

open_ui() {
    if grep -qi microsoft /proc/version; then
        # Edge in --app mode looks like a desktop window; fall back to the default Windows browser.
        local edge="/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
        if [ -x "$edge" ]; then "$edge" --app="$URL" > /dev/null 2>&1 &
        else explorer.exe "$URL" > /dev/null 2>&1; fi
    elif command -v xdg-open > /dev/null; then
        xdg-open "$URL" > /dev/null 2>&1 &
    else
        echo "Open $URL in a browser"
    fi
}

if curl -s -m 1 "$URL/api/stacks" > /dev/null; then
    echo "Console already running: $URL"
    open_ui
    exit 0
fi

# Wait in the background (up to 30 s) until the console answers, then open the UI.
(
    for _ in $(seq 60); do
        if curl -s -m 1 "$URL/api/stacks" > /dev/null; then open_ui; exit 0; fi
        sleep 0.5
    done
    echo "!! console not ready after 30 s, see the messages above"
) < /dev/null &

echo "Starting the console... the UI opens when it is ready ($URL). Quit with Ctrl+C."
exec ros2 run robot_console console
