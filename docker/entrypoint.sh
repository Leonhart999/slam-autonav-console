#!/bin/bash
# Container entrypoint: source ROS 2 and the workspace, then run the web console in the foreground.
set -e
set +u
source /opt/ros/humble/setup.bash
source "$HOME/ros2_ws/install/setup.bash"
set -u

# A volume that starts empty (or a bind-mounted host folder) lacks the console's data folders.
mkdir -p "$HOME/results" "$HOME/robot_console_data"/{logs,maps,runs,scenes,results,bags}

# Run the executable directly instead of `ros2 run`, so the SIGTERM from `docker stop` reaches the console itself
# and it can save a running map and stop its child processes (same reason as in install/check.sh).
exec "$(ros2 pkg prefix robot_console)/lib/robot_console/console" "$@"
