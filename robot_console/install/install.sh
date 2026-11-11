#!/bin/bash
# One-shot installer for the LiDAR SLAM + navigation thesis system (robot_console web console and the
# ROS 2 packages it drives). Target: Ubuntu 22.04 x86_64 (native or WSL2) with ROS 2 Humble.
#
# Usage (as a normal user with sudo rights, from anywhere):
#   bash <workspace>/src/robot_console/install/install.sh [options]
#
# Options:
#   --cn            use mirrors in mainland China (TUNA for Ubuntu/pip, USTC for ROS, npmmirror for Node/npm)
#   --skip-apt      do not touch apt (system packages must already be installed)
#   --jobs N        parallel compile jobs (default: min(nproc, 4); lower it on machines with < 16 GB RAM)
#   --cache DIR     where downloaded archives are kept and looked up first (default ~/.cache/robot_console_install).
#                   Put archives there in advance to install without GitHub access; file names are printed on failure.
#   --rebuild-web   rebuild the web frontend even if web/dist already exists
#   -h, --help      show this help
#
# The script is idempotent: steps that are already done are skipped, so it can simply be run again after a failure.
# The workspace is $ROS2_WS (default ~/ros2_ws) with robot_console, sim_robot and lidar_nav in its src/
# (copied or cloned). FAST_LIO and livox_ros_driver2 are downloaded at pinned versions if missing, and so are
# the Gazebo Fuel models of the warehouse world (their licenses do not allow shipping them).
# Data (maps, run records, logs, exports) goes to paths.data_root of the console config, ~/robot_console_data
# by default; put machine-specific settings in ~/.config/robot_console/console.yaml.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=versions.env
source "$HERE/versions.env"
WS="${ROS2_WS:-$HOME/ros2_ws}"
SRC="$WS/src"
CN=0; SKIP_APT=0; REBUILD_WEB=0
JOBS=$(( $(nproc) < 4 ? $(nproc) : 4 ))
CACHE="$HOME/.cache/robot_console_install"

while [ $# -gt 0 ]; do
    case "$1" in
        --cn) CN=1 ;;
        --skip-apt) SKIP_APT=1 ;;
        --jobs) JOBS="$2"; shift ;;
        --cache) CACHE="$2"; shift ;;
        --rebuild-web) REBUILD_WEB=1 ;;
        -h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)"; exit 2 ;;
    esac
    shift
done
mkdir -p "$CACHE"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then
    die "run this as the normal user who will use the console, not as root (it calls sudo when needed)"
fi
SUDO=sudo

# fetch URL NAME: download URL to $CACHE/NAME unless it is already there; prints the local path.
fetch() {
    local url="$1" name="$2" out="$CACHE/$2"
    if [ ! -s "$out" ]; then
        info "downloading $name" >&2
        curl -fsSL --retry 3 --connect-timeout 20 -o "$out.part" "$url" >&2 \
            || die "download failed: $url
       Put the file into $CACHE as '$name' (e.g. download it on another machine) and run again.
       Behind a proxy, export https_proxy=http://host:port first."
        mv "$out.part" "$out"
    fi
    echo "$out"
}

# github_src REPO SHA DEST: unpack a pinned GitHub commit into DEST (no git needed, works with the cache).
github_src() {
    local repo="$1" sha="$2" dest="$3" name archive
    name="$(echo "$repo" | tr '/' '_')-$sha.tar.gz"
    archive=$(fetch "https://github.com/$repo/archive/$sha.tar.gz" "$name")
    mkdir -p "$dest"
    tar -xzf "$archive" -C "$dest" --strip-components=1
}

# ---------------------------------------------------------------------------------------------------------------
step "Checking the system"
. /etc/os-release
[ "${VERSION_CODENAME:-}" = jammy ] || die "Ubuntu 22.04 (jammy) is required, found ${PRETTY_NAME:-unknown}"
[ "$(uname -m)" = x86_64 ] || die "x86_64 is required (Livox SDK and Node binaries), found $(uname -m)"
IN_WSL=0; grep -qi microsoft /proc/version && IN_WSL=1
info "$PRETTY_NAME, $(nproc) cores, $(free -g | awk '/Mem:/ {print $2}') GB RAM, WSL=$IN_WSL, jobs=$JOBS"
# Package folders: anywhere below $SRC (three folders, or the folders of one cloned repository)
pkg_dir() {
    local f
    f=$(grep -lsE "<name>$1</name>" "$SRC"/*/package.xml "$SRC"/*/*/package.xml 2>/dev/null | head -1)
    [ -n "$f" ] && dirname "$f"
}
CONSOLE_DIR=$(pkg_dir robot_console) || true
SIM_DIR=$(pkg_dir sim_robot) || true
NAV_DIR=$(pkg_dir lidar_nav) || true
for pkg in robot_console:$CONSOLE_DIR sim_robot:$SIM_DIR lidar_nav:$NAV_DIR; do
    [ -n "${pkg#*:}" ] || die "package ${pkg%%:*} not found below $SRC. Clone the repository into $SRC first."
done
info "packages: $CONSOLE_DIR, $SIM_DIR, $NAV_DIR"

# ---------------------------------------------------------------------------------------------------------------
if [ "$SKIP_APT" -eq 0 ]; then
    step "System packages (apt)"
    if [ "$CN" -eq 1 ] && ! grep -rqs "mirrors.tuna.tsinghua.edu.cn" /etc/apt/sources.list; then
        info "switching Ubuntu apt mirror to TUNA (backup: /etc/apt/sources.list.bak)"
        $SUDO cp -n /etc/apt/sources.list /etc/apt/sources.list.bak
        $SUDO sed -i -E 's#https?://(archive|security)\.ubuntu\.com/ubuntu/?#http://mirrors.tuna.tsinghua.edu.cn/ubuntu/#g' /etc/apt/sources.list
    fi
    $SUDO apt-get update
    $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y \
        curl ca-certificates gnupg locales software-properties-common git build-essential cmake \
        python3-pip python3-venv python3-dev
    # ROS 2 requires a UTF-8 locale
    if ! locale -a 2>/dev/null | grep -qi '^en_US\.utf8$'; then
        $SUDO locale-gen en_US.UTF-8
    fi
    $SUDO add-apt-repository -y universe > /dev/null

    if ! grep -rqs "ros2/ubuntu" /etc/apt/sources.list.d/; then
        info "adding the ROS 2 apt repository"
        deb=$(fetch "https://github.com/ros-infrastructure/ros-apt-source/releases/download/$ROS_APT_SOURCE_VERSION/ros2-apt-source_$ROS_APT_SOURCE_VERSION.jammy_all.deb" \
                    "ros2-apt-source_$ROS_APT_SOURCE_VERSION.jammy_all.deb")
        if [ "$CN" -eq 1 ]; then
            # Same signing key as packages.ros.org; only the download host changes.
            tmp=$(mktemp -d); dpkg-deb -x "$deb" "$tmp"
            $SUDO install -m 644 "$tmp/usr/share/keyrings/ros2-archive-keyring.gpg" /usr/share/keyrings/ros2-archive-keyring.gpg
            rm -rf "$tmp"
            echo "deb [arch=amd64 signed-by=/usr/share/keyrings/ros2-archive-keyring.gpg] https://mirrors.ustc.edu.cn/ros2/ubuntu jammy main" \
                | $SUDO tee /etc/apt/sources.list.d/ros2.list > /dev/null
        else
            $SUDO apt-get install -y "$deb"
        fi
        $SUDO apt-get update
    fi
    # Runtime and build dependencies (package.xml of robot_console, sim_robot, lidar_nav, FAST_LIO, livox_ros_driver2)
    $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y \
        ros-humble-ros-base ros-dev-tools python3-colcon-common-extensions \
        ros-humble-ros-gz ros-humble-navigation2 ros-humble-nav2-bringup ros-humble-nav2-simple-commander \
        ros-humble-pointcloud-to-laserscan ros-humble-pcl-ros ros-humble-pcl-conversions ros-humble-rosidl-default-generators \
        libpcl-dev libeigen3-dev libapr1-dev \
        python3-numpy python3-scipy python3-yaml python3-pil python3-matplotlib python3-psutil
else
    step "Skipping apt (--skip-apt)"
fi
[ -f /opt/ros/humble/setup.bash ] || die "ROS 2 Humble is not installed (/opt/ros/humble missing)"

# ---------------------------------------------------------------------------------------------------------------
step "Livox-SDK2 (needed to build livox_ros_driver2, which FAST_LIO depends on)"
if [ -f /usr/local/lib/liblivox_lidar_sdk_shared.so ]; then
    info "already installed in /usr/local"
else
    sdk="$HOME/thirdparty/Livox-SDK2"
    [ -f "$sdk/CMakeLists.txt" ] || github_src "$LIVOX_SDK2_REPO" "$LIVOX_SDK2_SHA" "$sdk"
    cmake -S "$sdk" -B "$sdk/build" -DCMAKE_BUILD_TYPE=Release > /dev/null
    cmake --build "$sdk/build" -j "$JOBS"
    $SUDO cmake --install "$sdk/build"
    $SUDO ldconfig
fi

# ---------------------------------------------------------------------------------------------------------------
step "Third-party ROS packages in $SRC"
if [ -f "$SRC/FAST_LIO/package.xml" ]; then
    info "FAST_LIO present (keeping it)"
else
    info "FAST_LIO $FASTLIO_SHA + thesis patch"
    tmp="$SRC/FAST_LIO.tmp"; rm -rf "$tmp"
    github_src "$FASTLIO_REPO" "$FASTLIO_SHA" "$tmp"
    rm -rf "$tmp/doc" "$tmp/include/ikd-Tree"
    github_src "$IKDTREE_REPO" "$IKDTREE_SHA" "$tmp/include/ikd-Tree"
    (cd "$tmp" && git apply --whitespace=nowarn "$HERE/patches/fast_lio-thesis.patch")
    mkdir -p "$tmp/Log" "$tmp/PCD"    # FAST-LIO crashes on shutdown if Log/ is missing
    mv "$tmp" "$SRC/FAST_LIO"
fi
mkdir -p "$SRC/FAST_LIO/Log" "$SRC/FAST_LIO/PCD"
if [ -f "$SRC/livox_ros_driver2/package_ROS2.xml" ]; then
    info "livox_ros_driver2 present (keeping it)"
else
    info "livox_ros_driver2 $LIVOX_DRIVER_SHA"
    github_src "$LIVOX_DRIVER_REPO" "$LIVOX_DRIVER_SHA" "$SRC/livox_ros_driver2"
fi
# The driver ships ROS1 and ROS2 variants; its own build.sh selects ROS2 the same way.
(cd "$SRC/livox_ros_driver2" && cp -f package_ROS2.xml package.xml && rm -rf launch && cp -r launch_ROS2 launch)

# Gazebo Fuel models of the warehouse world (CC BY-NC / BY-NC-ND, downloaded instead of shipped).
# The office and BIM worlds work without them. Behind a proxy, export https_proxy=http://host:port first.
if [ -f "$SIM_DIR/models/fuel/Warehouse/model.sdf" ]; then
    info "Gazebo Fuel models present"
elif bash "$SIM_DIR/tools/fetch_fuel_models.sh"; then
    info "Gazebo Fuel models downloaded"
else
    info "WARNING: Gazebo Fuel models could not be downloaded; the warehouse world will not load."
    info "         Run later: bash $SIM_DIR/tools/fetch_fuel_models.sh [--proxy http://host:port]"
fi

# FAST-LIO does not expand '~' in map_file_path, so the configs carry an absolute home path.
# Optional HKUST 4F west world: its building model is built from the user's own SLABIM download
if [ -f "$SIM_DIR/models/bim_hkust_4f_west/model.sdf" ]; then
    info "HKUST 4F west building model present"
elif [ -n "${SLABIM_DIR:-}" ]; then
    bash "$SIM_DIR/tools/make_hkust_models.sh" "$SLABIM_DIR" || info "WARNING: HKUST model could not be built"
else
    info "HKUST 4F west scene skipped (optional). To add it: download SLABIM, then"
    info "  bash $SIM_DIR/tools/make_hkust_models.sh /path/to/SLABIM   (or run this installer with SLABIM_DIR set)"
fi

for f in "$SIM_DIR/config/fastlio_sim_vlp16.yaml" "$SIM_DIR/config/fastlio_lidar_only.yaml" \
         "$SIM_DIR/config/fastlio_bag.yaml" "$SIM_DIR/config/fastlio_bag_lidar_only.yaml" \
         "$SRC/FAST_LIO/config/m3dgr_mid360.yaml"; do
    if [ -f "$f" ] && grep -q 'map_file_path: "/home/[^/"]*/results/' "$f" && ! grep -q "map_file_path: \"$HOME/results/" "$f"; then
        sed -i -E "s#map_file_path: \"/home/[^/\"]*/results/#map_file_path: \"$HOME/results/#" "$f"
        info "map_file_path in $(basename "$f") now points to $HOME/results/"
    fi
done

# ---------------------------------------------------------------------------------------------------------------
step "Building the workspace (colcon, $JOBS jobs)"
set +u; source /opt/ros/humble/setup.bash; set -u
cd "$WS"
export MAKEFLAGS="-j$JOBS"
if [ ! -f install/livox_ros_driver2/share/livox_ros_driver2/package.xml ]; then
    colcon build --packages-select livox_ros_driver2 --cmake-args -DROS_EDITION=ROS2 -DDISTRO_ROS=humble
fi
colcon build --parallel-workers "$JOBS" --packages-up-to robot_console sim_robot lidar_nav fast_lio \
    --packages-skip livox_ros_driver2

# ---------------------------------------------------------------------------------------------------------------
step "Python environments"
PIP_INDEX=()
[ "$CN" -eq 1 ] && PIP_INDEX=(-i https://pypi.tuna.tsinghua.edu.cn/simple)
if [ ! -x "$HOME/venvs/console/bin/python" ]; then
    python3 -m venv --system-site-packages "$HOME/venvs/console"
fi
"$HOME/venvs/console/bin/pip" install -q "${PIP_INDEX[@]}" -r "$HERE/requirements-console.txt"
# pcd2grid snapshot used by the map management page (2D regeneration). --no-deps: numpy/scipy/Pillow/PyYAML come
# from Ubuntu, exactly as on the thesis machine.
"$HOME/venvs/console/bin/pip" install -q --no-deps --force-reinstall "$HERE"/wheels/pcd2grid-*.whl
if [ ! -x "$HOME/venvs/evo/bin/python" ]; then
    python3 -m venv "$HOME/venvs/evo"
fi
"$HOME/venvs/evo/bin/pip" install -q "${PIP_INDEX[@]}" -r "$HERE/requirements-evo.txt"

# ---------------------------------------------------------------------------------------------------------------
WEB="$CONSOLE_DIR/web"
if [ "$REBUILD_WEB" -eq 1 ] || [ ! -f "$WEB/dist/index.html" ]; then
    step "Web frontend (Node $NODE_VERSION)"
    node_dir="$HOME/opt/node-$NODE_VERSION-linux-x64"
    if [ ! -x "$node_dir/bin/node" ]; then
        if [ "$CN" -eq 1 ]; then base="https://cdn.npmmirror.com/binaries/node/$NODE_VERSION"; else base="https://nodejs.org/dist/$NODE_VERSION"; fi
        tarball="node-$NODE_VERSION-linux-x64.tar.xz"
        archive=$(fetch "$base/$tarball" "$tarball")
        sums=$(fetch "$base/SHASUMS256.txt" "node-$NODE_VERSION-SHASUMS256.txt")
        (cd "$CACHE" && grep " $tarball\$" "$sums" | sha256sum -c -) || die "Node checksum mismatch; delete $archive and retry"
        mkdir -p "$HOME/opt" && tar -xJf "$archive" -C "$HOME/opt"
    fi
    ln -sfn "$node_dir" "$HOME/opt/node"
    export PATH="$HOME/opt/node/bin:$PATH"
    NPM_REG=()
    [ "$CN" -eq 1 ] && NPM_REG=(--registry=https://registry.npmmirror.com)
    (cd "$WEB" && npm ci --no-fund --no-audit "${NPM_REG[@]}" && npm run build)
else
    step "Web frontend: web/dist exists (use --rebuild-web to rebuild)"
fi

# ---------------------------------------------------------------------------------------------------------------
step "Data folders and launcher"
# ~/results: FAST-LIO writes its map there (map_file_path above). The console's own folders come from its config.
mkdir -p "$HOME/results"
set +u; source "$WS/install/setup.bash"; set -u
"$HOME/venvs/console/bin/python" - <<'PY_DIRS'
import os
from robot_console import config
c = config.load()
dirs = [c['paths']['data_root'], c['server']['log_dir'], c['evaluation']['results_dir']] +        [c['mapping'][k] for k in ('maps_dir', 'runs_dir', 'scenes_dir')]
for d in dirs:
    os.makedirs(d, exist_ok=True)
print('    data folders: ' + ', '.join(dirs))
PY_DIRS
if [ "$IN_WSL" -eq 1 ]; then
    bat="$HOME/start_console.bat"
    # Windows batch file: double-click to start the console in this WSL distro and open the UI.
    printf '@echo off\r\ntitle robot_console\r\nwsl -d %s -u %s -- bash -lc "bash %s/install/start_console.sh"\r\npause\r\n' \
        "${WSL_DISTRO_NAME:-Ubuntu-22.04}" "$USER" "$CONSOLE_DIR" > "$bat"
    info "Windows launcher: \\\\wsl.localhost\\${WSL_DISTRO_NAME:-Ubuntu-22.04}${bat//\//\\} (copy it to the desktop)"
fi

step "Checking the installation"
ROS2_WS="$WS" bash "$HERE/check.sh"

cat <<EOF

Installation finished.
  Start:  bash $CONSOLE_DIR/install/start_console.sh   (then open http://localhost:8000)
  Config: ~/.config/robot_console/console.yaml overrides config/console.yaml (example: config/user_console.example.yaml)
  Maps:   none yet. Use the Mapping page to build one; the Navigation page needs a saved map.
EOF
