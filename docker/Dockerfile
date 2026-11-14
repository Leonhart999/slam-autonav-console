# Container image for the SLAM and AutoNav Console: Ubuntu 22.04, ROS 2 Humble, Gazebo Fortress, FAST-LIO2, Nav2.
#
# The image is built by the same install.sh that sets up a normal machine, so the two cannot drift apart.
# The whole stack (web console, Gazebo, SLAM, Nav2) runs inside this one container. There is no Gazebo window;
# use the web UI.
#
# Build from the repository root:
#   docker compose build
#   docker compose build --build-arg CN=1      # mirrors for mainland China (apt, ROS, pip, npm, Node)
#
# The image contains the Gazebo Fuel warehouse models that install.sh downloads (CC BY-NC / CC BY-NC-ND).
# Keep the image local: do not push it to a public registry.

# Offline archives for install.sh: an empty stage by default. Pass --build-context prefetch=DIR (compose:
# PREFETCH_DIR) to supply the files install.sh would download from GitHub; see docker/README.md.
FROM scratch AS prefetch

FROM ubuntu:22.04

ARG CN=0
ARG JOBS=4
# Ubuntu mirror used when CN=1, reached over https. If one mirror is out of sync for a while, try another one,
# e.g. --build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn or mirrors.aliyun.com
ARG APT_MIRROR=mirrors.ustc.edu.cn
ARG DEBIAN_FRONTEND=noninteractive

# sudo: install.sh refuses to run as root and calls sudo.
# libgl1-mesa-dri, libegl*: software rendering of the simulated lidar (LIBGL_ALWAYS_SOFTWARE=1, headless ogre2).
# procps: install.sh prints the RAM size with free.
# apt goes over https: on some networks plain-http package downloads arrive corrupted inside the build (Hash Sum mismatch).
# ca-certificates is not in the base image, so the first update and its install skip the TLS certificate check; apt
# still verifies every package through the signed Release files. Acquire::Retries repeats a failed download.
RUN printf 'Acquire::Retries "5";\n' > /etc/apt/apt.conf.d/80retries \
 && boot="" \
 && if [ "$CN" = 1 ]; then \
        sed -i -E "s#https?://(archive|security)\.ubuntu\.com/ubuntu/?#https://${APT_MIRROR}/ubuntu/#g" /etc/apt/sources.list; \
        boot="-o Acquire::https::Verify-Peer=false -o Acquire::https::Verify-Host=false"; \
    fi \
 && for i in 1 2 3 4 5; do apt-get $boot update && break; echo "apt-get update failed (mirror out of sync?), retry $i" >&2; rm -rf /var/lib/apt/lists/*; sleep 15; done \
 && apt-get $boot install -y --no-install-recommends ca-certificates \
 && apt-get install -y --no-install-recommends sudo curl procps \
        libgl1-mesa-dri libegl1 libegl-mesa0 libglx-mesa0 \
 && rm -rf /var/lib/apt/lists/*

# install.sh must run as a normal user with sudo rights.
RUN useradd -m -s /bin/bash -u 1000 robot \
 && echo 'robot ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-robot \
 && chmod 0440 /etc/sudoers.d/90-robot

USER robot
# install.sh runs with set -u and reads $USER when it writes the WSL launcher (the container shares the WSL2 kernel,
# so /proc/version says microsoft). Docker does not set USER, so the build would stop at the very end without this.
ENV USER=robot
WORKDIR /home/robot
RUN mkdir -p /home/robot/.cache /home/robot/ros2_ws/src

COPY --chown=robot:robot . /home/robot/ros2_ws/src/slam-autonav-console

# One step on purpose: the build and the clean-up must share a layer for the clean-up to shrink the image.
# The cache mount keeps the downloaded archives (FAST_LIO, Livox, Node) between builds.
# Line endings of the scripts are normalised in case the repository was checked out on Windows with CRLF.
# The clean-up removes build-only files (colcon build/log, Node, Livox-SDK2 sources, node_modules) and the sudo
# rule, which the console does not need at run time. FAST_LIO stays in src/ because it writes its Log/ there.
# Archives supplied through the prefetch build context are copied into the download cache first, so install.sh
# does not need GitHub access.
RUN --mount=type=cache,target=/home/robot/.cache/robot_console_install,uid=1000,gid=1000 \
    --mount=type=bind,from=prefetch,target=/prefetch \
    set -eu; \
    find /prefetch -maxdepth 1 -type f \( -name '*.tar.gz' -o -name '*.deb' \) -exec cp -n -t /home/robot/.cache/robot_console_install/ {} +; \
    cd /home/robot/ros2_ws/src/slam-autonav-console; \
    find . -type f \( -name '*.sh' -o -name 'versions.env' \) -exec sed -i 's/\r$//' {} +; \
    opts="--jobs ${JOBS}"; \
    if [ "$CN" = 1 ]; then opts="$opts --cn"; fi; \
    if [ -d /prefetch/fuel ]; then export FUEL_BASE=file:///prefetch/fuel; fi; \
    ok=0; \
    for try in 1 2 3 4; do \
        if bash robot_console/install/install.sh $opts; then ok=1; break; fi; \
        echo "install.sh failed (attempt $try of 4); it is idempotent, running it again" >&2; sleep 10; \
    done; \
    [ "$ok" = 1 ]; \
    rm -rf /home/robot/ros2_ws/build /home/robot/ros2_ws/log /home/robot/thirdparty /home/robot/opt /home/robot/.npm \
           robot_console/web/node_modules; \
    sudo rm -rf /var/lib/apt/lists/*; \
    sudo rm -f /etc/sudoers.d/90-robot

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    ROBOT_CONSOLE_USER_CONFIG=/home/robot/ros2_ws/src/slam-autonav-console/docker/console.docker.yaml

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fs -m 3 http://127.0.0.1:8000/api/stacks > /dev/null || exit 1

ENTRYPOINT ["bash", "/home/robot/ros2_ws/src/slam-autonav-console/docker/entrypoint.sh"]
