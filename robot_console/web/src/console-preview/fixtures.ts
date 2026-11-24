import type { RunDetail, RunEval } from '@/evaluation/api'
import type { MapDetail, MapGrid } from '@/maps/api'
import type { MappingStatus, PlaybackStatus } from '@/mapping/stream'
import type { MarketView, PluginInfo, PluginList } from '@/plugins/types'
import type { MappingPreset, MapSceneInfo, NavMap, NavStatus, StackInfo, StatusMsg } from '@/types'
import { poseAt, speedAt, type DemoData } from '@/demo/data'

export const MAP = 'warehouse-demo'
export const RUN = 'map_warehouse_demo'
export const PRESET = 'warehouse'
const rates = [0.5, 1, 2, 4, 8]
const variants = ['navfn_dwb', 'smac_mppi', 'theta_dwb']

export interface WarehouseAssets {
  grid: Pick<MapGrid, 'width' | 'height' | 'origin' | 'resolution'>
  started: string
  ended: string
  evaluation: RunEval
}

const presets: MappingPreset[] = [{
  name: PRESET, label: { zh: '仿真仓库', en: 'Simulated warehouse' },
  description: { zh: '货架通道场景，带有一份保存的 FAST-LIO2 地图。', en: 'Shelf aisles with a saved FAST-LIO2 map.' },
}]

function stack(name: string, zh: string, en: string, cmd: string, page_only = false): StackInfo {
  return { name, label: { zh, en }, description: { zh: '演示进程条目', en: 'Example process entry' }, cmd, state: 'idle',
    preset: null, started_at: null, uptime: null, exit_code: null, messages: [], page_only }
}

const stacks = [stack('sim', 'Gazebo 仿真', 'Gazebo simulation', 'ros2 launch sim_robot sim.launch.py'),
  stack('mapping_fastlio', 'FAST-LIO2 建图', 'FAST-LIO2 mapping', 'ros2 launch fast_lio mapping.launch.py'),
  stack('waypoints', '自动航点', 'Automatic waypoints', 'ros2 run sim_robot waypoint_driver'),
  stack('navigation', '自主导航', 'Autonomous navigation', 'ros2 launch nav2_bringup navigation_launch.py', true),
  stack('playback', '数据包回放', 'Bag playback', 'ros2 bag play', true)]

const playback: PlaybackStatus = { stack: 'playback', available: false, state: 'idle', rates,
  roots: [], slam: null, slam_ready: false, needs_slam_restart: false }

function plugin(id: string, name: { zh: string; en: string }, description: { zh: string; en: string }): PluginInfo {
  return { id, dir: `plugins/${id}`, path: `plugins/${id}`, state: 'disabled', enabled: false, restart_needed: false,
    issue: null, manifest: { api: 1, version: '0.1.0', name, description, author: '', license: '', homepage: '' },
    has_backend: false, has_frontend: false, frontend: null, settings: [] }
}
const plugins: PluginList = { api_version: 1, dirs: [], paths: [], disabled_by_env: true, plugins: [
  plugin('bag_convert', { zh: '数据包转换', en: 'Bag conversion' }, { zh: '展示插件列表，在线预览不运行插件。', en: 'Example catalog entry. Plugins do not run in the preview.' }),
] }
const market: MarketView = { index_urls: [], source: null, fetched: null, name: '', updated: '', stale: false,
  error: null, skipped: [], install_dir: null, api_version: 1, supported_apis: [1], busy: false, plugins: [] }

export function buildFixtures(data: DemoData, assets: WarehouseAssets, gridBytes: number) {
  const { run, track } = data
  const ev = assets.evaluation
  const grid: MapGrid = { ...assets.grid, size_m: [assets.grid.width, assets.grid.height].map(
    n => Number((n * assets.grid.resolution).toFixed(2))) as [number, number], image: 'warehouse-grid.png' }
  const navMap: NavMap = { name: MAP, has_cloud: true, has_solid: false, preset: PRESET, grid }
  const map: MapDetail = { ...navMap, grid, kind: 'folder', created: assets.ended, source: null, bag: null,
    algorithm: run.algorithm, points: run.voxels, voxels: run.voxels, has_grid: true, navigable: true,
    extent: run.extent, issues: [], frame: run.frame, run: RUN, duration: run.duration, length: run.length,
    voxel_size: run.map.voxel, sim_cmd: null, slam_cmd: null, location: 'samples/warehouse', location_win: '',
    yaml_path: null, files: [{ name: 'warehouse-grid.png', dir: false, size: gridBytes, count: null, role: null },
      { name: run.map.file, dir: false, size: run.map.bytes, count: run.voxels, role: null }],
    total_size: run.map.bytes + gridBytes }
  const detail: RunDetail = { id: RUN, type: 'mapping', preset: PRESET, started: assets.started, ended: assets.ended,
    duration: run.duration, length: run.length, maps: [MAP], recording: false, evaluable: true,
    ape_rmse: ev.ape.rmse, rpe_rmse: ev.rpe?.rmse ?? null, evaluated: ev.created, eval_error: null, variant: null,
    nav: null, figures: [], algorithm: run.algorithm, playback: null, params_changed: null, info: {},
    eval: ev, ref_eval: null, amcl: null }
  const { info: _info, eval: _ev, ref_eval: _ref, amcl: _amcl, ...entry } = detail
  const scene: MapSceneInfo = { name: MAP, preset: PRESET, scene_source: null, source: 'sim', bag: null,
    preset_ok: true, suggested: PRESET, has_gt: true }
  const nav: NavStatus = { type: 'navigation', state: 'idle', map: MAP, sim_running: false, sim_preset: null,
    sim: { state: 'idle', origin: null, job: 'idle', error: null }, exit_code: null, pose: null, scan: [],
    global_path: [], local_path: [], tf_ok: false, age: null, ready: false, error: null, goal: null,
    speed: { scale: 1, max_v: null, max_w: null, range: [0.5, 2], applied: false, error: null },
    amcl: null, costmaps: { global: null, local: null }, mission: null, variant: null }
  const status: StatusMsg = { type: 'status', time: 0, clock: { sim_time: run.duration, stale: true },
    topics: [], tf: { expected: [], other: [] }, nodes: [], stacks, resources: { cpu: 0, mem: 0 },
    paths: { maps: 'samples/maps', runs: 'samples/runs' } }
  const mapping: MappingStatus = { type: 'mapping', epoch: 1, frame: run.frame, voxel: run.map.voxel,
    pose: poseAt(run, run.duration), speed: speedAt(track, track.t.length - 1), initialized: true,
    duration: run.duration, length: run.length, voxels: run.voxels, extent: run.extent, error: null, has_gt: true,
    publishers: { scan: 0, odom: 0 }, stacks: { sim: 'sim', slam: 'mapping_fastlio', waypoints: 'waypoints' },
    topics: { scan: '/cloud_registered', odom: '/Odometry', gt: '/ground_truth' }, algorithm: run.algorithm,
    slam_options: [{ name: 'mapping_fastlio', algorithm: run.algorithm, save_mode: 'service', sources: ['sim', 'bag'], needs_imu: true }],
    waypoints: null, teleop: { active: false, locked: true, busy: false, v: 0, w: 0, max_v: 0.6, max_w: 1 },
    run: RUN, save: { state: 'idle' }, playback }

  const options = { variants, default_variant: variants[0], localizers: ['amcl', 'gt'], max_runs: 5,
    goal_timeout: 120, speed_scale_range: [0.5, 2] }
  const routes: Record<string, unknown> = {
    '/api/mapping/presets': presets,
    '/api/maps': [map],
    [`/api/maps/${MAP}`]: map,
    '/api/map-trash': [],
    '/api/navigation/maps': [navMap],
    '/api/navigation/status': nav,
    '/api/navigation/variants': { variants, default: variants[0] },
    [`/api/navigation/maps/${MAP}/scene`]: scene,
    [`/api/navigation/maps/${MAP}/goals`]: { goals: [], saved: null },
    '/api/evaluation/runs': { runs: [entry], params: { max_diff: ev.params.max_diff, rpe_delta: ev.params.rpe_delta } },
    [`/api/evaluation/runs/${RUN}`]: detail,
    '/api/run-trash': { entries: [], size: 0 },
    '/api/batch/options': { presets, slam: [stacks[1]], max_runs: 5, run_timeout: 600, speeds: [1, 2, 5] },
    '/api/batch/status': { state: 'idle' },
    '/api/batch/history': [],
    '/api/nav-batch/options': options,
    '/api/nav-batch/status': { state: 'idle' },
    '/api/nav-batch/history': [],
    '/api/playback/bags': { roots: [], bags: [] },
    '/api/playback/storage': { upload_dir: '', trash: { root: '', path: '', size: 0, items: 0 }, free: [] },
    '/api/playback/trash': { entries: [], dirs: [] },
    '/api/playback/sensor_profiles': [],
    '/api/tasks': { tasks: [] },
    '/api/plugins': plugins,
    '/api/plugins/market': market,
  }
  return { routes, status, mapping }
}
