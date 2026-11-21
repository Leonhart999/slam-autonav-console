# robot_console

Web console for the thesis system *LiDAR-Based SLAM Mapping and Autonomous Navigation for Mobile Robots*.
From one browser page you can start a Gazebo simulation, build a map with FAST-LIO2 or GLIM, manage maps,
navigate with Nav2 and evaluate the runs with evo. The console is a ROS 2 package: a Python backend (rclpy + FastAPI) plus a Vue 3
frontend. The backend starts simulation, SLAM and navigation as separate processes from a fixed whitelist
(`config/stacks.yaml`).

The system runs in simulation. Recorded rosbag2 data (from the simulation or a real robot) can be played
into a SLAM on the Playback page (see Bag playback); driving a real robot would need different topics, stacks
and configuration.

## Requirements

| | |
|---|---|
| OS | Ubuntu 22.04 x86_64, native or under WSL2 on Windows 10/11 |
| ROS | ROS 2 Humble (installed by the script if missing) |
| Simulator | Gazebo Fortress through `ros-humble-ros-gz` (installed by the script) |
| Hardware | 4+ cores and 16 GB RAM recommended; ~10 GB free disk |
| Rights | a normal user with `sudo` |

## Workspace layout

The installer uses the workspace `$ROS2_WS`, `~/ros2_ws` by default. The console finds the other packages
through the ROS package index (`${pkg:NAME}` in the configuration), so the workspace can live anywhere.

| Package in `<workspace>/src` | Role | Source |
|---|---|---|
| `robot_console` | this web console | thesis code |
| `sim_robot` | Gazebo worlds, diff-drive robot with 16-line lidar and IMU, cloud adapter, waypoint driver | thesis code |
| `lidar_nav` | Nav2 launch and parameters, 3D→2D map projection, navigation test tools | thesis code |
| `FAST_LIO` | FAST-LIO2 SLAM, ROS2 branch with the thesis patch | `hku-mars/FAST_LIO@cc3c9e8` + `install/patches/fast_lio-thesis.patch` |
| `livox_ros_driver2` | message types that FAST_LIO builds against | `Livox-SDK/livox_ros_driver2@2144554` |

The first three packages come from this repository; clone it into `<workspace>/src` (the installer finds the
packages anywhere below `src/`, so one cloned repository or three separate folders both work).
The installer downloads FAST_LIO and livox_ros_driver2 at the pinned commits in
[`install/versions.env`](install/versions.env) if they are not there.

The FAST-LIO patch makes one code change. The point cloud subscription uses `QoS(20).reliable()` instead of
`SensorDataQoS`. With best-effort delivery, about 30 % of the simulated 16-line clouds (0.7 MB each) were
dropped. The patch also adds the config `m3dgr_mid360.yaml` for the M3DGR dataset.

## Install

```bash
# 1. clone the repository into the workspace
mkdir -p ~/ros2_ws/src && cd ~/ros2_ws/src
git clone https://github.com/Leonhart999/slam-autonav-console.git

# 2. run the installer (in mainland China add --cn to use domestic mirrors)
bash ~/ros2_ws/src/slam-autonav-console/robot_console/install/install.sh --cn
```

For another workspace folder, set it first: `export ROS2_WS=/path/to/ws`.

The script installs and configures these items. It skips any step that is already done, so after a
failure you can fix the cause and run it again.

1. apt packages: ROS 2 Humble (`ros-base`), Gazebo Fortress bridge, Nav2, pointcloud_to_laserscan, PCL and build tools.
2. Livox-SDK2, built and installed to `/usr/local`.
3. FAST_LIO and livox_ros_driver2 in `<workspace>/src`, if they are missing, and the Gazebo Fuel models of the
   warehouse world (`sim_robot/tools/fetch_fuel_models.sh`, pinned versions with SHA-256 check). Their
   licences do not allow shipping them in the repository. If the download fails the installer goes on;
   only the warehouse world is affected.
   The HKUST 4F west world is optional: its building model is derived from the SLABIM dataset, which states
   no licence for its BIM files, so it is not shipped either. Only `BIM.zip` of SLABIM is needed:
   download https://huggingface.co/datasets/BobH62/SLABIM/resolve/main/BIM.zip (the SharePoint links in the SLABIM GitHub README no longer work; mainland China mirror:
   https://hf-mirror.com/datasets/BobH62/SLABIM/resolve/main/BIM.zip), extract it with `python3 -m zipfile -e BIM.zip SLABIM` and run
   `sim_robot/tools/make_hkust_models.sh SLABIM`, or run the installer with `SLABIM_DIR` set. Until then the
   Mapping page does not list that scene.
4. FAST-LIO cannot expand `~`, so the configs need an absolute path. The script rewrites `map_file_path` in
   `sim_robot/config/fastlio_sim_vlp16.yaml` to your home directory.
5. A `colcon build` of the workspace.
6. Two Python virtual environments:
   - `~/venvs/console` has FastAPI, uvicorn and the bundled `pcd2grid` wheel.
   - `~/venvs/evo` has evo for trajectory evaluation. It is separate because it needs NumPy 2.
7. The web frontend. Node.js is downloaded to `~/opt/node` and only used for this build.
8. The data folders: `~/results` (FAST-LIO writes its map there) and the console's folders under
   `paths.data_root`, `~/robot_console_data` by default (see Configuration).
9. On WSL, a Windows launcher `~/start_console.bat`.

Options:

| Option | Effect |
|---|---|
| `--cn` | TUNA mirror for Ubuntu and pip, USTC mirror for ROS, npmmirror for Node and npm |
| `--jobs N` | parallel compile jobs (default `min(nproc, 4)`); use 2 with 8 GB RAM |
| `--skip-apt` | do not touch apt (all system packages already installed) |
| `--cache DIR` | archive cache, default `~/.cache/robot_console_install` |
| `--rebuild-web` | rebuild the frontend even if `web/dist` exists |

**Without GitHub access.** The installer downloads five files from GitHub and looks for each one in the
cache first. Download them on another machine and put them into `~/.cache/robot_console_install` under
these names:

```
hku-mars_FAST_LIO-<FASTLIO_SHA>.tar.gz                  https://github.com/hku-mars/FAST_LIO/archive/<FASTLIO_SHA>.tar.gz
hku-mars_ikd-Tree-<IKDTREE_SHA>.tar.gz                  https://github.com/hku-mars/ikd-Tree/archive/<IKDTREE_SHA>.tar.gz
Livox-SDK_Livox-SDK2-<LIVOX_SDK2_SHA>.tar.gz            https://github.com/Livox-SDK/Livox-SDK2/archive/<LIVOX_SDK2_SHA>.tar.gz
Livox-SDK_livox_ros_driver2-<LIVOX_DRIVER_SHA>.tar.gz   https://github.com/Livox-SDK/livox_ros_driver2/archive/<LIVOX_DRIVER_SHA>.tar.gz
ros2-apt-source_1.3.0.jammy_all.deb                     https://github.com/ros-infrastructure/ros-apt-source/releases/download/1.3.0/ros2-apt-source_1.3.0.jammy_all.deb
```

The SHAs are in [`install/versions.env`](install/versions.env). The installer also honours the
`https_proxy` variable.

## Check and start

```bash
bash ~/ros2_ws/src/robot_console/install/check.sh          # packages, venvs, and a short console start on port 8099
bash ~/ros2_ws/src/robot_console/install/start_console.sh  # start the console and open http://localhost:8000
```

On WSL the UI opens in Edge app mode, or in the default Windows browser if Edge is missing. On native Ubuntu
it opens through `xdg-open`. To stop the console, press Ctrl+C in its terminal. It stops the simulation,
SLAM and navigation processes it started; if FAST-LIO is still running, it saves the map first. A fresh
install has no maps. Build one on the Mapping page and save it before you use the Navigation page.

## Configuration

The shipped settings are in `config/console.yaml`, `config/stacks.yaml` and `config/mapping_presets.yaml`.
They contain no machine-specific paths. Put the settings of your machine in
`~/.config/robot_console/console.yaml` instead: it holds only the keys you change and is merged onto
`config/console.yaml` (dicts key by key, lists replaced). `config/user_console.example.yaml` is a starting
point. The variable `ROBOT_CONSOLE_USER_CONFIG` names another override file. A console started with
`--config FILE` (test instances) ignores `~/.config/robot_console/console.yaml`.

Strings in the configuration may use `~`, `${data_root}` (the value of `paths.data_root`) and
`${pkg:NAME}` (the `share/NAME` folder of an installed ROS package).

| Key | Default | Meaning |
|---|---|---|
| `paths.data_root` | `~/robot_console_data` | parent of the data folders below |
| `server.port` | 8000 | HTTP port. The console listens on 127.0.0.1 only |
| `server.log_dir` | `${data_root}/logs` | output of the processes started from the UI |
| `mapping.maps_dir` | `${data_root}/maps` | map library |
| `mapping.runs_dir` | `${data_root}/runs` | run records used by the evaluation page |
| `mapping.scenes_dir` | `${data_root}/scenes` | Gazebo scenes uploaded on the Mapping page |
| `evaluation.results_dir` | `${data_root}/results` | CSV and PNG exports of the evaluation page |
| `mapping.slam_profiles.*.save.pcd_path` | `~/results/fastlio_map.pcd` | where FAST-LIO writes its map; the console puts it into the generated parameter file |
| `mapping.slam_profiles.*.config` | `slam/fastlio.yaml` | layered SLAM configuration the console builds the stack's parameter file from (see below) |
| `mapping.slam_config_dir` | `${data_root}/slam/effective` | the generated parameter files, one per SLAM stack |
| `mapping.slam_overrides_file` | `${data_root}/slam/overrides.yaml` | SLAM parameters changed on the pages |
| `mapping.slam_facts_file` | `${data_root}/slam/sensor_facts.yaml` | measured sensor facts per sensor profile |
| `mapping.gt_topic` | `/ground_truth` | simulation ground truth; leave empty without one |
| `playback.bag_dirs` | `[${data_root}/bags]` | folders searched for rosbag2 bags (Playback page) |
| `playback.upload_dir` | `<first bag_dirs>/uploads` | where bags uploaded from the page go |
| `playback.space_check` | `[]` | extra paths whose free space an upload must fit in. Under WSL the Linux disk reports the free space of its virtual disk, not of the Windows drive holding the disk image: list that drive's mount point |
| `playback.user_sensor_profiles_file` | `${data_root}/sensor_profiles.yaml` | browser-created sensor profiles (checked before built-in ones) |
| `playback.scan_recording.dir` | `${data_root}/scans` | raw body-frame scans recorded when **Save raw scans** is enabled during playback |
| `navigation.default_map` | `~/maps/office_fastlio.yaml` | map `lidar_nav/nav.launch.py` loads without `map:=` |
| `teleop.max_v`, `teleop.max_w` | 5.0, 1.2 | hard speed limits for manual driving |

Changes to `config/` need a rebuild (`colcon build --packages-select robot_console`) unless the workspace was
built with `--symlink-install`. The override file needs only a console restart.

Use one console per machine and ROS domain. Starting a second console on the same port is refused, because
on startup the console stops processes left over from its previous run on that port. For tests, run a
second console on another port and another `ROS_DOMAIN_ID` (see `install/check.sh`).

## SLAM configuration

The SLAM stacks do not run hand-written parameter files. At every start the console builds them from a
specification in `config/slam/` (`fastlio.yaml`, `glim.yaml`, `glim_lidar.yaml`) in layers, later layers winning:

| Layer | Holds | Source |
|---|---|---|
| base | the validated parameters, the same for every robot | `base` in the specification |
| input | what depends on the data source: `sim` (the simulated robot, also bags recorded from it) or `adapter` (a real-robot bag played through the sensor adapter) | `inputs` |
| derived | values computed from what the console measured of the sensor: number of rings, scan rate, points per scan | `derive` + `mapping.slam_facts_file` |
| options | the switches of the page: LiDAR only, ground-vehicle constraint | `options` |
| console | input topics and the map path | `mapping.slam_profiles` in `console.yaml` |
| user | values changed on the page ("Parameters" in the SLAM step) | `mapping.slam_overrides_file` |

The result is written to `mapping.slam_config_dir/<stack>.yaml` (GLIM: a folder of JSON files
`mapping.slam_config_dir/<stack>/`, whose base is the folder named by `base_dir`) and stored in the run record (`run.json`,
key `slam_config`: every value, the layer each one came from, and the validation record of that combination),
so two runs can be compared by their parameters. Every key of a layer must exist in `base` with the same type.

**Derived values.** For a bag played with a sensor profile the console measures, once per profile, the number
of ring values of the cloud, the scan rate, the valid points per scan and the IMU rate (the first scans of the
bag and its message counts; stored in `mapping.slam_facts_file`, delete an entry to measure again). Rules in
the specification (`derive`) turn these into parameter values (for FAST-LIO: `scan_line`, `scan_rate` and
`point_filter_num`; for GLIM: `k_correspondences` from the ring count). A rule sets the value only when it is marked
`enabled`, which is done after it was checked on recorded data; otherwise the Parameters dialog shows its value
as a suggestion that can be taken over with a click.

The **Parameters** button of the SLAM step (Mapping and Playback pages) opens the list of parameters named
under `tunable` in the specification, each with its base value, the automatic value, your value and the range
accepted. Your values are stored per SLAM variant and data source (`sim`, or the sensor profile of the bag), so
a value tuned for one robot never reaches another; they apply at the next start. A run started with changed
values is marked on the Evaluation page, and the validation record of the specification is shown as no longer
describing it.
GLIM with IMU runs on real-robot bags too: through the sensor adapter it gets cloud and IMU in one frame,
so its input layer `adapter` only sets an identity `T_lidar_imu`.
The files `sim_robot/config/fastlio_*.yaml` are no longer used by the console; they remain for running FAST-LIO
from the command line, and a unit test checks that the layers reproduce them exactly.

## Bag playback

The Playback page (menu item 02, between Mapping and Maps) builds maps from recorded bags. It lists every
rosbag2 folder (a folder with `metadata.yaml`, up to four levels deep) under `playback.bag_dirs`. Then:

1. choose a bag; the card shows its length, size and whether it has a point cloud, IMU and ground truth.
   **Upload a bag** picks a rosbag2 folder on this computer (metadata.yaml and the .db3 / .mcap files it
   lists) and sends it file by file into `playback.upload_dir`; the console first checks the free space of
   that disk and of every path in `playback.space_check`. The bin icon on a card moves the bag to the
   `.trash` folder of its bag folder; it keeps its disk space until **Empty the trash** (above the list)
   deletes it for good;
2. start a SLAM. Only algorithms whose inputs the bag has are offered: FAST-LIO2 and GLIM need an IMU,
   **GLIM (LiDAR only)** (`config/glim_lidar_only`, CT-GICP odometry) needs only a point cloud;
3. choose the part of the bag on the timeline (default: all of it), then play it at 0.5× to 8×, pause,
   change the rate, stop. The start is `--start-offset`; the console stops the player at the end of the part.
   There is no jumping during a playback, because a jump inside one SLAM run breaks the SLAM. Starting in the
   middle of a drive gives IMU-based SLAMs a worse initialization (FAST-LIO2 on `office_loop_04`, part
   20–80 s: ATE 0.044 m against about 0.035 m for the whole bag);
4. save the map as usual. `run.json` and `meta.json` record `source: bag`, the bag path, rate and part.

The simulation and a playback cannot run at the same time (both publish `/clock` and the SLAM inputs); each
page says so and links to the other.

What gets played: the SLAM's input topics (`mapping.slam_profiles.<name>.inputs`). A bag topic with the
input's name is used as is; otherwise the bag's busiest topic of that type is remapped onto it. A bag with
`/clock` (recorded from the simulation) plays its clock; other bags get `--clock 100`. If the bag has the
ground truth topic, it is played too and the Evaluation page can compute ATE for the run.

Each SLAM run takes one playback. To play again, restart the SLAM: otherwise the bag time jumps back and
the SLAM mixes both passes. The page shows when the SLAM falls behind the playback and drops scans; save only after it has caught up,
or the end of the map is missing.

The simulation bags in `~/bags/sim` (recorded by the thesis scripts) play straight into FAST-LIO2 and GLIM.
Measured on this machine (12 cores):

| SLAM, bag | 1× | 4× | 8× |
|---|---|---|---|
| FAST-LIO2, `office_loop_04` (110 s, 16-line sim lidar) | ATE 0.034 m, 1068 poses | 0.035 m, 1060 | 0.031 m, 1066; player reaches 7.4×, no lag |
| GLIM (LiDAR only), HEU61 120 s (two 96-line lidars) | 1125 frames, 55.6 m | 1052 frames, 55.1 m | 854 frames (24 % dropped), 54.7 m; up to 37 s behind, caught up 7 s after the bag |

8× suits sparse clouds such as the simulation bags; for dense real-robot clouds use 1× or 2× when every
scan matters. For a bag from another robot, the SLAM configs
(extrinsics, lidar type, blind distance) are still the simulated robot's; only GLIM (LiDAR only) has no
robot-specific extrinsic.

`tools/pcd_frames_to_bag.py` converts a dataset exported as per-frame PCD files (`frames.csv` +
`calibration_results.yaml`, two lidars) into such a bag: NaN points dropped, both lidars merged into
`base_link`, per-point time kept. It writes about 26 MB per second of recording; convert a time window first
(`--start`, `--duration`).

## Plugins

Plugins add backend routes, pages, header items, isolated Python environments, background jobs and settings
without changing the console (developer guide: [docs/plugins.md](docs/plugins.md), rules & UI guidelines:
[docs/plugin-guidelines.md](docs/plugin-guidelines.md), example:
[examples/plugins/hello_console](examples/plugins/hello_console)).
A plugin is a folder with a `plugin.yaml` inside a plugin folder (`plugins.dirs`, default
`${data_root}/plugins`). Additional plugins can be installed directly from the **Plugin market**
([`Leonhart999/slam-console-plugins`](https://github.com/Leonhart999/slam-console-plugins) index) or from a
local `.zip` archive on the **Plugins** page. The console only reads manifests when scanning; new plugins
start disabled, and enabling one takes effect at the next console start. Plugin code runs with the console's
rights, so only enable plugins you trust. `ROBOT_CONSOLE_NO_PLUGINS=1` starts the console without any plugin.

The console ships four plugins (all disabled until you enable them):

- **AI assistant** (`ai_assistant`): a resizable header chat drawer with a dedicated model-service profiles
  page (OpenAI-compatible Chat Completions or Anthropic Messages API), inline model / reasoning-effort /
  access pickers, read-only tools for maps, runs, ATE/RPE, batch experiments, SLAM parameters, logs and
  sensor profiles, and optional approval-gated action tools for starting/cancelling batch experiments and
  running evaluations. See [docs/plugins.md](docs/plugins.md#the-ai-assistant-plugin).
- **Bag converter** (`bag_convert`): converts ROS 1 `.bag` files to ROS 2 bags (sqlite3 or MCAP) via
  `rosbags`, including optional Livox `CustomMsg` to `PointCloud2` conversion.
- **Dynamic object removal** (`dynamic_removal`): removes traces of moving objects from maps saved with raw
  scans using DUFOMap, and saves a cleaned map version.
- **Ground segmentation** (`ground_seg`): splits map point clouds into ground / non-ground with Patchwork++
  and rebuilds the 2D occupancy grid from height above local ground.

## Notes on the simulation

- In `sim_robot/launch/sim.launch.py` the sensors render with `ogre2` plus `LIBGL_ALWAYS_SOFTWARE=1`.
  Under WSL2, GPU `ogre2` crashes. GPU `ogre1` runs, but its lidar range error (σ ≈ 0.1 m on the ground)
  bends the FAST-LIO map. Software rendering is slower but measures to about 0.03 m. On native Linux
  with a working GPU driver this choice still works; it is just not the fastest.
- Gazebo prints ~1500 fake points per frame at exactly 0.30 m. The cloud adapter drops points closer than
  0.35 m and FAST-LIO uses a blind distance of 0.4 m.

## Third-party components and licences

| Component | Licence |
|---|---|
| FAST_LIO, ikd-Tree | GPL-2.0 (patched copy in this workspace) |
| Livox-SDK2, livox_ros_driver2 | MIT |
| Warehouse and shelf/pallet/cart models in `sim_robot/models/fuel` | CC BY-NC 4.0 / CC BY-NC-ND 4.0, Gazebo Fuel (OpenRobotics, MovAi). Not in the repository; downloaded by `sim_robot/tools/fetch_fuel_models.sh`. Non-commercial use only and do not modify; see `sim_robot/models/fuel/README.md` |
| HKUST 4F building models in `sim_robot/models/bim_hkust_*` | derived from the SLABIM dataset; check its licence before redistributing |
| pcd2grid (`install/wheels`) | Apache-2.0; unreleased snapshot of 2026-11-05 on top of v0.1.0 |
| robot_console, sim_robot, lidar_nav | MIT (`LICENSE`) |

## Development checks

`install/dev_check.sh` checks what is about to be committed. It exports the staged tree (`git checkout-index`)
to a temp directory, so uncommitted work elsewhere in the folder does not affect the result. It then runs:

| Stage | What | When |
|---|---|---|
| python | `py_compile` of `robot_console/*.py` and `tests/*.py` | always |
| tests | `pytest tests/` in `~/venvs/console`, with ROS sourced | a `.py`, `config/` or `tests/` file is staged |
| web | `vue-tsc -b` + `vite build` of `web/`, reusing `web/node_modules` | a `web/` file is staged |
| i18n | `tools/check_i18n.py`: literal `t('…')` keys exist in zh-CN and en, backend error codes have `error.<code>` | a `web/` or `robot_console/*.py` file is staged |

```bash
bash install/dev_check.sh --install   # once per clone: install it as .git/hooks/pre-commit
bash install/dev_check.sh --all       # run every stage by hand
```

The hook blocks a commit when a stage fails. `git commit --no-verify` skips it; use that only in an
emergency. The tests in `tests/` need no running ROS graph. Run them directly with
`~/venvs/console/bin/python -m pytest -p no:anyio tests -q` after sourcing `/opt/ros/humble/setup.bash`.
`-p no:anyio` is needed because the anyio pytest plugin in the venv requires a newer pytest than the one
Ubuntu ships (6.2.5).
Frontend unit tests: `(cd web && npm test)` runs the Node test suite (`web/tests/*.test.mjs`).
UI smoke test (manual, not in the hook): `THEME=light|dark VIEWPORT=1440x1000 bash tools/ui_smoke.sh` opens all 6 pages in zh/en with headless Edge on a separate console instance (port 8010, domain 90) and fails on JS errors, failed requests, raw i18n keys, layout overflow or low contrast; screenshots and summary go to `<data_root>/ui_screens/smoke_<time>/` (WSL only; machine-specific `WIN_NODE` / `OUT_WIN` defaults can go in `~/.config/robot_console/dev.env`).
Running-state UI check (manual, ~3 min): `bash tools/ui_states.sh <map>` starts a separate console with a simulation, FAST-LIO and Nav2 and runs the same checks on the Navigation, Mapping and Monitor pages in those states (scene mismatch warning, live SLAM controls, active navigation) at 1707x985 and 1100x700; `<map>` is a saved map of a non-warehouse world. Buttons, tags and chips whose text runs past their own border fail both checks.

## Uninstall

The console and its environments live in your home directory:

```bash
rm -rf ~/venvs/console ~/venvs/evo ~/opt/node ~/opt/node-v*-linux-x64 ~/.cache/robot_console_install
rm -rf ~/ros2_ws/build ~/ros2_ws/install ~/ros2_ws/log
```

The maps and records under `paths.data_root` (default `~/robot_console_data`) and `~/results` are your data,
so delete them only on purpose. `~/.config/robot_console` holds your settings.
Livox-SDK2 is installed in `/usr/local` (`liblivox_lidar_sdk_*`, `livox_lidar_*.h`). The ROS apt packages
can be removed with `sudo apt remove 'ros-humble-*'`.
