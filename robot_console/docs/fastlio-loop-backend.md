# Offline planar loop back-end

This is an **offline** back-end, not an online loop-closure switch. FAST-LIO2 continues to estimate odometry unchanged. A separate recorder stores body-frame scans with matching odometry timestamps. After playback, the back-end selects keyframes, finds spatial revisit candidates, verifies submap alignment, optimizes a planar pose graph, and rebuilds the map from local scans.

## Scope

- For a ground vehicle on a single approximately horizontal floor, after the front-end height/tilt have already been stabilized.
- Optimizes x, y and world-z yaw only. Every input scan's height and gravity tilt are retained.
- Candidate search uses odometry proximity (10 m default) and path separation (100 m default). This is **not** a place-recognition system for arbitrary large drift or repeated identical corridors.
- Three or more nearby accepted constraints may still represent one physical revisit; they are not independent closed loops.
- Registration gates, robust losses and submap spacing reduce false matches but cannot guarantee rejection of all corridor aliases. Inspect the junction and full-map figures before using the result.
- Current implementation processes the whole recording after it ends; no corrections are fed into the live FAST-LIO filter or its local map.

## In the console (Maps page)

Since 2026-11-25 the whole workflow runs from the web page; the command-line steps below stay available.

1. **Record**: on the Playback page pick a FAST-LIO2 variant (preferably with the ground vehicle constraint), switch **Save raw scans** on, play the bag, and **save the map**. A recording that no saved map names is deleted when the SLAM stops.
2. **Optimize**: on the Maps page select that map and press **Loop optimization** (next to Regenerate 2D). The button is disabled with a reason when the map has no raw scans or is itself a loop result. The dialog warns when the map was built without the ground constraint, and shows the install command when the loop venv is missing. Parameters: loop search radius (10 m), minimum path between visits (100 m), map point range (20 m), map voxel (0.05 m), scan stride (2); under *Other parameters* the registration gates (overlap 0.6, RMSE 0.15 m, shift 5 m, heading 10°). The run is a background task (`kind: loop`, Background tasks list); stages: reading, checking loop candidates, optimizing, rebuilding.
3. **Preview**: summary, end correction, wall-point consistency per revisit, a table of every checked candidate with its rejection reason, and the figures (trajectory correction, walls at the revisit, map top view, original and new grid). With no accepted loop nothing can be saved.
4. **Save as new map** (default name `<map>_loop`, at most 48 characters): a new map with `source: loop`, `parent_map`, `loop_params`, and a new run `play_loop_<name>` whose `est.tum` is the optimized and `est_online.tum` the original FAST-LIO2 trajectory (`gt.tum` / `ref.tum` copied from the source run), so the Evaluation page compares them directly. The scans were recorded during the playback that produced the source map, so its run is the exact baseline. Navigation goals are not copied. The new map is added to the recording's `used_by_maps.txt`.

Only the scans listed in the map's `data/scan_poses.tum` are used, i.e. the scans up to the moment the map was saved. Unsaved results stay in `<maps_dir>/.loop/<token>/` (at most 3, deleted after 24 h; **Discard result** deletes one at once). The back-end runs in `loop_backend.python` (`console.yaml`, default `~/venvs/loop/bin/python`, timeout 1800 s), never inside the console process. Implementation: `robot_console/map_loop.py`, `web/src/components/MapLoopDialog.vue`.

## Environment

Use a separate numerical environment so the Python GTSAM 4.2 wheel does not replace the system GLIM/GTSAM installation:

```bash
python3 -m venv ~/venvs/loop
~/venvs/loop/bin/pip install -r install/requirements-loop.txt
```

Tested: Ubuntu 22.04, Python 3.10, NumPy 1.26.4, SciPy 1.15.3, GTSAM 4.2 Python wheel, small_gicp 1.0.1. The ROS recorder uses the ROS Python interpreter, not this venv.

## 1. Record scans and odometry

Build/install `robot_console` to register the three CLI commands. Source ROS and the workspace, use the **same ROS_DOMAIN_ID as the intended FAST-LIO process**, and start the recorder before playback:

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_ws/install/setup.bash
ros2 run robot_console fastlio_loop_record ~/loop_recordings/run_001
```

The recorder subscribes to `/cloud_registered_body` and `/Odometry`; `--cloud-topic` and `--odom-topic` override them. FAST-LIO must publish body-frame scans (`publish.scan_bodyframe_pub_en: true`). Stop the recorder with Ctrl+C after playback and the front-end finish.

Output:

- `scans.bin`: consecutive little-endian float32 x/y/z/intensity points, 16 bytes per point.
- `index.csv`: whitespace-separated rows, despite the legacy extension: `stamp_s point_offset point_count x y z qx qy qz qw`.
- Quaternion is x/y/z/w. Pose maps body coordinates into FAST-LIO's odometry frame. Each scan is paired with odometry by exact ROS timestamp before writing.

Refuses to overwrite an existing recording. The recorded HEU61 run used approximately 4.4 GB.

## 2. Optimize and rebuild

Run the numerical module with the isolated interpreter. From a source checkout this works without installing numerical dependencies into ROS:

```bash
~/venvs/loop/bin/python -m robot_console.fastlio_loop_backend \
  ~/loop_recordings/run_001 ~/loop_results/run_001 \
  --map-res 0.05 --scan-stride 2
```

The source package must be on `PYTHONPATH` (workspace `setup.bash` after building, or `export PYTHONPATH="$PWD:${PYTHONPATH:-}"` from the repository root). Running `ros2 run robot_console fastlio_loop` with the ROS interpreter will not find the optional numerical libraries unless that interpreter has them; prefer the explicit venv command above.

Output directory must be absent or empty. Input recordings are never modified. Both before/after maps use the same scans, maximum range and sampling, making their comparison meaningful.

`--poses <tum>` uses only the scans listed in a TUM file (by stamp), e.g. `data/scan_poses.tum` of a map saved while the recorder was still running. `--params <json>` overrides `loop_radius`, `loop_min_gap`, `accept_overlap`, `accept_rmse`, `max_loop_shift` and `max_loop_yaw_deg`. Lines `PHASE <name>` on stdout mark the stages for the console. `loop_report.json` is written even when no loop is accepted (then without the optimized trajectory). The wall slices of the figures and metrics are taken relative to the floor height estimated from the scans.

Outputs include `traj_odom.tum`, `traj_loop.tum`, `map_odom_voxel.pcd`, `map_loop_voxel.pcd`, `grid_odom.*`, `grid_loop.*`, `loop_report.json`, corrections CSV and comparison figures. `--no-map` skips full-map and grid rebuilding. `--map-range` (default 20 m) limits the points used for the map and grid; loop registration still uses 50 m.

### Occupancy grid from scan evidence

The rebuilt map is a union of raw scans, so it contains everything any scan ever saw: people walking with the robot, stray far returns, points seen once. Projecting that union with `min-points=1` put many obstacle cells into the corridor. `fastlio_loop_grid.py` therefore builds the grid from the scans directly and counts evidence per 5 cm cell:

- height is measured from the floor under the sensor of each scan (sensor height estimated from the scans), not from one global plane;
- only points within `map_range` of the sensor are used;
- a cell is occupied only if obstacle-height points were seen in at least `min_hits` scans (default 4), from at least `min_span` metres of path (1.0), and hits are at least `occ_ratio` (0.10) of hits plus beams of other scans that passed through the cell;
- cells within `robot_radius` (0.3 m) of the driven path are free.

The same decision removes obstacle-height points (0.10-0.70 m above the floor) of rejected cells from `map_*_voxel.pcd`, so a later re-projection of the cloud does not bring them back. Points of moving objects above 0.70 m stay in the cloud. The module also runs alone on an existing result: `python -m robot_console.fastlio_loop_grid <recording> <result_dir> [--traj traj_loop.tum] [--min-hits N ...]`.

## 3. Publish a new console map

```bash
~/venvs/loop/bin/python -m robot_console.fastlio_loop_export \
  ~/loop_results/run_001 \
  --maps-dir ~/maps --name my_fastlio_loop \
  --source-run ~/runs/ORIGINAL_RUN --runs-dir ~/runs \
  --parent-map ORIGINAL_MAP
```

`--grid-python` and `--grid-script` are needed only for older results that have no `grid_loop.pgm`.

Creates a **new** library map, a new optimized run and its raw trajectory backup. Does not overwrite the parent map/run. Preview, grid and trajectory all come from the corrected geometry; old grids or goals must not be copied over. The source-run option supplies bag/reference metadata only: if scans came from a new replay, it is not the exact baseline. The exact baseline is the new run's `est_online.tum` and the result directory's `traj_odom.tum`.

The grid is the back-end's `grid_loop` (5 cm cells, scan evidence as described above). It is **not** equivalent in density to the original accumulated dense FAST-LIO map. The `navigable` catalog flag means files are valid; it does not constitute a Nav2 driving validation.

The existing map catalog discovers the new directory without restarting the console. The commands need a package build/install to appear in `ros2 run`; source modules are available immediately through the explicit module invocation.

## HEU61 validation (2026-11-14)

Input: full MCAP, start at 5 s, leveled sensor frame, ground constraint 0.02 deg / 0.002 m. A new replay was recorded for a matched before/after comparison. It is not the prior height-report run (so its raw endpoint differs).

- 10,842 distinct-timestamp scans, 598 keyframes.
- Three accepted constraints from one revisit near the start.
- Endpoint correction approximately 2.52 m horizontal and -1.05 deg yaw.
- Input and output height range both -0.126542 to +0.090154 m.
- Selected overlapping wall points: median nearest-point inconsistency 0.315 m to 0.052 m; 90th percentile 2.421 m to 0.123 m (11-14 version: scans of the last 60 s against the first 60 s). Since 2026-11-25 the metric is taken per revisit: scans within 10 m of path around the new end of a loop against the map within 15 m around its old end, both before and after. On the same recording that gives 0.422 m to 0.064 m (median) and 2.433 m to 0.155 m (p90); accepted loops and the endpoint correction are unchanged.
- These wall points are selected using the optimized overlap, so the values are an in-sample map-consistency diagnostic, **not ATE or independent accuracy**. Broad first/last-segment statistics remain worse because many surfaces were observed only in one segment.
- Dynamic objects, thin/reflective surfaces and remaining front-end/local scan distortion are not removed by one pose-graph loop.

Validation run references: published map `heu61_fastlio_loop_20261114`, exact comparison baseline `play_odom_heu61_loop_20261114`, optimized run `play_loop_heu61_fastlio_loop_20261114`.

## Release verification still required

The numerical prototype, the HEU61 experiment and the Maps page workflow (test console running the working tree on the HEU61 recording, 2026-11-25: 79 s, 3 of 5 candidates accepted, save, task-center recovery) were exercised. The ROS-package entry points, a fresh install with the optional loop venv, and a new recording through the packaged recorder have not been release-tested. Per the project workflow, run the full console test/build/live checks at the next version release. The correction is offline; nothing is fed back into a running FAST-LIO2.
