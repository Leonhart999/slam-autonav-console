# Docker

Runs the whole system (web console, Gazebo simulation, FAST-LIO2 / GLIM, Nav2) in one container. The image is built
by the normal `robot_console/install/install.sh`, so it matches a native install.

## Requirements

- Docker Engine, or Docker Desktop with the WSL2 backend on Windows, with BuildKit and `docker compose` v2.
- x86_64 (the Livox SDK and Node binaries are x86_64 only).
- 4+ cores, 16 GB RAM and about 20 GB free disk for the build. With 8 GB RAM build with `JOBS=2`.

## Build and run

```bash
docker compose build        # the first build compiles FAST_LIO and downloads several GB
docker compose up -d
# open http://localhost:8000
docker compose logs -f      # console output
docker compose down         # stop; the data volumes stay
```

A build takes about 20 to 60 minutes depending on the network; the image is about 7.4 GB.

## Options

Set them as environment variables (`CN=1 docker compose build`) or, for the build ones, as `--build-arg`.

| Variable | Default | Effect |
|---|---|---|
| `CN` | `0` | `1` uses mirrors in mainland China (Ubuntu over https, ROS, pip, npm, Node) |
| `JOBS` | `4` | parallel compile jobs; use `2` with 8 GB RAM |
| `APT_MIRROR` | `mirrors.ustc.edu.cn` | Ubuntu mirror used when `CN=1`; try `mirrors.tuna.tsinghua.edu.cn` or `mirrors.aliyun.com` if one is out of sync |
| `PREFETCH_DIR` | `./docker/prefetch` | folder with files the build would otherwise download (see Offline build) |
| `CONSOLE_PORT` | `8000` | host port of the web UI (run time). Use another one if a native console already listens on 8000 |

## Data

| Volume | Mounted at | Holds |
|---|---|---|
| `console_data` | `~/robot_console_data` | maps, runs, logs, scenes, bags, evaluation exports |
| `console_results` | `~/results` | the raw map FAST-LIO writes |

`docker compose down` keeps them, `docker compose down -v` deletes them. To play bags that live on the host, uncomment
the bind mount in `docker-compose.yml`.

## Offline build (no direct access to GitHub or Gazebo Fuel)

The installer downloads five files from GitHub and seven zip files from Gazebo Fuel. Where those hosts are blocked or
slow, download the files on another machine (or through a proxy) and put them into a folder. The build reads that
folder through the `prefetch` build context: `docker/prefetch/` by default, or the folder named by `PREFETCH_DIR`.

```
<PREFETCH_DIR>/
  hku-mars_FAST_LIO-<FASTLIO_SHA>.tar.gz
  hku-mars_ikd-Tree-<IKDTREE_SHA>.tar.gz
  Livox-SDK_Livox-SDK2-<LIVOX_SDK2_SHA>.tar.gz
  Livox-SDK_livox_ros_driver2-<LIVOX_DRIVER_SHA>.tar.gz
  ros2-apt-source_<ROS_APT_SOURCE_VERSION>.jammy_all.deb
  fuel/<owner>/<kind>/<name>/<version>/<name>.zip      (same layout as the Fuel server)
```

The SHAs and the version are in `robot_console/install/versions.env`; the URLs of the five GitHub files are in the
main README, section "Without GitHub access". The Fuel zip files are the ones listed in
`sim_robot/tools/fetch_fuel_models.sh`, for example
`fuel/OpenRobotics/models/Warehouse/1/Warehouse.zip` and `fuel/OpenRobotics/worlds/Tugbot in Warehouse/1/Tugbot in Warehouse.zip`
(the folder name keeps the space). Each Fuel zip is checked against the SHA-256 in that script.

If the `fuel` folder exists, the build sets `FUEL_BASE=file:///prefetch/fuel` for the installer, so nothing is
downloaded from Fuel. Without a prefetch folder the build downloads everything itself.

A proxy can be passed to the build instead. It must be reachable from inside the builder, so `localhost` does not
work; use the host's address:

```bash
docker compose build --build-arg HTTPS_PROXY=http://host.docker.internal:<port>
```

The proxy variant has not been tested. The prefetch folder has (see `docker/prefetch/README.md`).

## Docker on WSL2 without Docker Desktop

Docker Engine can be installed directly in a WSL2 Ubuntu. Two things differ from a normal Linux host:

- A WSL distribution without systemd does not start the Docker daemon by itself. After every WSL restart run
  `sudo service docker start`.
- Docker Hub may be unreachable. Registry mirrors can be set in `/etc/docker/daemon.json`
  (`registry-mirrors`), then restart the daemon.

## Troubleshooting

- **Random download failures during the build** (`Hash Sum mismatch` from apt, `SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC`
  from pip). On one tested network, downloads inside the build were corrupted from time to time, while the same
  downloads on the host were fine. The cause was not found. The Dockerfile works around it: with `CN=1` apt uses
  https, apt retries failed downloads, and `install.sh` is run up to four times (it skips finished steps). If a
  build still fails with such an error, run `docker compose build` again; if apt keeps failing on one mirror, change
  `APT_MIRROR`.
- **The build fails at the end with `Gazebo Fuel models missing`.** The installer continues without the models, but
  its final check fails. The Fuel download was too slow or blocked; use the Offline build above.
- **Port 8000 is already in use.** A native console may be running in the same WSL. Stop it or set `CONSOLE_PORT`.

## Notes

- The console listens on all interfaces inside the container (`docker/console.docker.yaml`), and compose publishes
  the port on `127.0.0.1` of the host only. The console has no login, so do not publish it on other interfaces.
- The simulation runs headless; there is no Gazebo window. The web UI shows everything.
- The simulated lidar is rendered in software (ogre2 + llvmpipe), as in the native install, so no GPU is needed.
- The container shares the WSL2 kernel under Docker Desktop, so the installer treats it as WSL and also writes an
  unused `start_console.bat`. The folder-open buttons of the UI were not checked inside the container and probably do nothing there.
- The image contains the Gazebo Fuel warehouse models (CC BY-NC / CC BY-NC-ND). Do not push it to a public registry.
- The HKUST 4F west scene is not included (it needs your own SLABIM download, see the main README).
- Settings can still be overridden: mount your own file and set `ROBOT_CONSOLE_USER_CONFIG` to its path.

## Tested

On 2026-11-14 (WSL2 Ubuntu 22.04, Docker Engine 29, `CN=1`): the image builds, the installer check passes, the web UI
answers on the published port, the headless simulation publishes `/lidar/points` and `/imu`, and the console API
starts the simulation, FAST-LIO2 and the waypoint driver and saves a map into the data volume.

Not tested in the container: the Navigation page (Nav2), GLIM, bag playback, batch experiments, and the UI in a
browser (only the HTTP interface was checked).
