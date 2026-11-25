import type { NavGoalRecord, NavMetrics, RunDetail, RunEval } from '@/evaluation/api'
import type { MapDetail, MapGrid } from '@/maps/api'
import type { MappingStatus, PlaybackStatus } from '@/mapping/stream'
import type { MarketView, PluginList } from '@/plugins/types'
import type { MappingPreset, MapSceneInfo, NavMap, NavStatus, StackInfo, StatusMsg } from '@/types'
import { poseAt, speedAt, type DemoData, type DemoRun } from '@/demo/data'

export const MAP = 'warehouse-demo'
export const RUN = 'map_warehouse_demo'
export const PRESET = 'warehouse'
export const OFFICE_MAP = 'office-demo'
export const OFFICE_RUN = 'map_office_demo'
export const NAV_SAMPLES = ['navfn-dwb', 'smac-mppi'] as const
export type NavSample = typeof NAV_SAMPLES[number]
const rates = [0.5, 1, 2, 4, 8]
const variants = ['navfn_dwb', 'smac_mppi', 'theta_dwb']

export interface ConsoleAssets {
  grid: Pick<MapGrid, 'width' | 'height' | 'origin' | 'resolution'>
  started: string
  ended: string
  evaluation: RunEval
}

export interface MapSummary extends Pick<DemoRun, 'algorithm' | 'frame' | 'duration' | 'length' | 'extent' | 'map'> {
  version: number
}

export interface NavigationAssets {
  started: string
  ended: string
  nav: NavMetrics
  goals: NavGoalRecord[]
  amcl: RunDetail['amcl']
}

const presets: MappingPreset[] = [{
  name: PRESET, label: { zh: '仿真仓库', en: 'Simulated warehouse' },
  description: { zh: '货架通道场景，带有一份保存的 FAST-LIO2 地图。', en: 'Shelf aisles with a saved FAST-LIO2 map.' },
}, {
  name: 'office', label: { zh: '仿真办公室', en: 'Simulated office' },
  description: { zh: '可浏览建图结果和 Nav2 三航点实验。', en: 'Saved mapping results and three-waypoint Nav2 trials.' },
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

export interface PluginCatalog {
  bundled: PluginList
  market: MarketView
}

function mapFixture(preset: string, name: string, id: string, run: MapSummary, assets: ConsoleAssets, gridBytes: number) {
  const ev = assets.evaluation
  const grid: MapGrid = { ...assets.grid,
    size_m: [assets.grid.width, assets.grid.height].map(
      n => Number((n * assets.grid.resolution).toFixed(2))) as [number, number], image: `${preset}-grid.png` }
  const navMap: NavMap = { name, has_cloud: true, has_solid: false, preset, grid }
  const map: MapDetail = { ...navMap, grid, kind: 'folder', created: assets.ended, source: null, bag: null,
    algorithm: run.algorithm, points: run.map.count, voxels: run.map.count, has_grid: true, navigable: true,
    extent: run.extent, issues: [], frame: run.frame, run: id, duration: run.duration, length: run.length,
    voxel_size: run.map.voxel, sim_cmd: null, slam_cmd: null, location: `samples/${preset}`, location_win: '',
    yaml_path: null, files: [{ name: grid.image, dir: false, size: gridBytes, count: null, role: null },
      { name: run.map.file, dir: false, size: run.map.bytes, count: run.map.count, role: null }],
    total_size: run.map.bytes + gridBytes }
  const detail: RunDetail = { id, type: 'mapping', preset, started: assets.started, ended: assets.ended,
    duration: ev.duration, length: ev.length, maps: [name], recording: false, evaluable: true,
    ape_rmse: ev.ape.rmse, rpe_rmse: ev.rpe?.rmse ?? null, evaluated: ev.created, eval_error: null, variant: null,
    nav: null, figures: [], algorithm: run.algorithm, playback: null, params_changed: null, info: {},
    eval: ev, ref_eval: null, amcl: null }
  const scene: MapSceneInfo = { name, preset, scene_source: null, source: 'sim', bag: null,
    preset_ok: true, suggested: preset, has_gt: true }
  return { map, navMap, detail, scene }
}

export function navigationRunId(sample: NavSample) { return `nav_office_${sample.replaceAll('-', '_')}` }

function navigationFixture(sample: NavSample, assets: NavigationAssets): RunDetail {
  const { nav, started, ended, goals, amcl } = assets
  return { id: navigationRunId(sample), type: 'navigation', preset: 'office', started, ended,
    duration: nav.total_time_s, length: nav.total_path_m, maps: [OFFICE_MAP], recording: false, evaluable: true,
    ape_rmse: null, rpe_rmse: null, evaluated: null, eval_error: null, variant: nav.variant, nav,
    figures: ['path.png'], algorithm: null, playback: null, params_changed: null, info: { goals },
    eval: null, ref_eval: null, amcl }
}

export function buildFixtures(data: DemoData, warehouse: ConsoleAssets, gridBytes: number,
  office: { run: MapSummary; assets: ConsoleAssets; gridBytes: number },
  navSamples: { sample: NavSample; assets: NavigationAssets }[], catalog: PluginCatalog) {
  const { run, track } = data
  const maps = [mapFixture(PRESET, MAP, RUN, { ...run, duration: warehouse.evaluation.duration,
    length: warehouse.evaluation.length }, warehouse, gridBytes),
  mapFixture('office', OFFICE_MAP, OFFICE_RUN, office.run, office.assets, office.gridBytes)]
  const details = [...maps.map(m => m.detail), ...navSamples.map(n => navigationFixture(n.sample, n.assets))]
  const entries = details.map(({ info: _info, eval: _ev, ref_eval: _ref, amcl: _amcl, ...entry }) => entry)
  const ev = warehouse.evaluation
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
    '/api/maps': maps.map(m => m.map),
    '/api/map-trash': [],
    '/api/navigation/maps': maps.map(m => m.navMap),
    '/api/navigation/status': nav,
    '/api/navigation/variants': { variants, default: variants[0] },
    '/api/evaluation/runs': { runs: entries, params: { max_diff: ev.params.max_diff, rpe_delta: ev.params.rpe_delta } },
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
    '/api/plugins': catalog.bundled,
    '/api/plugins/market': catalog.market,
  }
  for (const { map, scene } of maps) {
    routes[`/api/maps/${map.name}`] = map
    routes[`/api/navigation/maps/${map.name}/scene`] = scene
    routes[`/api/navigation/maps/${map.name}/goals`] = { goals: [], saved: null }
  }
  for (const detail of details) routes[`/api/evaluation/runs/${detail.id}`] = detail
  return { routes, status, mapping }
}
