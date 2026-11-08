// 后端通过 WebSocket 推送的状态消息格式（和 robot_console/server.py、ros_node.py 对应）

export interface ClockInfo {
  sim_time: number
  stale: boolean // 超过 2 s 没收到 /clock
}

export interface TopicInfo {
  name: string
  type: string | null
  publishers: number
  expected_hz: number | null // null 表示事件触发的话题，不检查频率
  hz: number
  age: number | null // 距最后一条消息的秒数
}

export interface TfEdge {
  parent: string
  child: string
  present: boolean
  static?: boolean
  age?: number
  hz?: number
}

export type StackState = 'idle' | 'running' | 'stopping' | 'exited'

/** 后端配置里的双语文字（stacks.yaml 的 label、description） */
export interface Bilingual {
  zh: string
  en: string
}

/** 后端返回的结果或错误：只有代码和参数，文字在 locales/ 里按语言生成 */
export interface CodedMsg {
  code: string
  params: Record<string, string | number>
}

export interface StackInfo {
  name: string
  label: Bilingual
  description: Bilingual
  cmd: string
  state: StackState
  preset: string | null // 最近一次启动时用的场景预设（建图页）
  origin?: 'mapping' | 'navigation' | 'playback' | 'monitor' | null
  started_at: number | null
  uptime: number | null
  exit_code: number | null
  messages: CodedMsg[]
  /** Started only by its own page (e.g. bag playback from the mapping page), not from the monitor page */
  page_only?: boolean
}

/** 建图场景预设（config/mapping_presets.yaml） */
export interface MappingPreset {
  name: string
  label: Bilingual
  description: Bilingual
  custom?: boolean
  waypoints?: boolean // 内置预设未提供时默认支持航点
  spawn?: [number, number, number, number] // 米、弧度
}

/** Saved map that can be used for navigation (GET /api/navigation/maps) */
/** matrix: 4x4 pose in the map frame, column-major (THREE.Matrix4.fromArray) */
export interface NavSolidPrimitive {
  type: 'box' | 'cylinder'
  size?: [number, number, number]
  radius?: number
  length?: number
  matrix: number[]
  color: [number, number, number]
}

/** Mesh from the original Gazebo model; file is served at /api/navigation/solid/{model}/{file} */
export interface NavSolidMesh {
  model: string
  file: string
  format: 'dae' | 'stl'
  matrix: number[]
}

/** 3D solid model in the map frame (GET /api/navigation/maps/{name}/solid) */
export interface NavSolid {
  frame: 'map'
  primitives: NavSolidPrimitive[]
  meshes: NavSolidMesh[]
}

export interface NavMap {
  name: string
  has_cloud: boolean
  has_solid: boolean
  preset: string | null
  /** how preset was set when not recorded while mapping: bound to an existing scene, or generated from the grid */
  scene_source?: 'bound' | 'generated' | null
  from_bag?: boolean
  grid: { resolution: number; width: number; height: number; origin: [number, number, number] }
}

/** Scene of a map for the navigation simulation (GET /api/navigation/maps/{name}/scene, map_scene.py) */
export interface MapSceneInfo {
  name: string
  preset: string | null
  scene_source: 'bound' | 'generated' | null
  source: 'bag' | 'sim' | null
  bag: string | null
  preset_ok: boolean
  suggested: string | null
  has_gt: boolean
  generated?: { boxes: number; cell: number; height: number }
}

export interface NavPose {
  x: number
  y: number
  z: number
  yaw: number
}

/** Navigation telemetry in the map frame (GET /api/navigation/status) */
export interface NavStatus {
  type: 'navigation'
  state: StackState
  map: string | null
  sim_running: boolean
  sim_preset: string | null
  /** origin: page that started the simulation; job: background stop / restart from the navigation page */
  sim: {
    state: StackState
    origin: 'mapping' | 'navigation' | 'monitor' | null
    job: 'idle' | 'stopping' | 'restarting'
    error: string | null
  }
  exit_code: number | null
  pose: NavPose | null
  scan: number[][]
  global_path: number[][]
  local_path: number[][]
  tf_ok: boolean
  age: number | null
  ready: boolean
  error: string | null
  goal: NavGoal | null
  /** Navigation speed multiplier and the resulting Nav2 limits; applied = active in the running Nav2 */
  speed: {
    scale: number
    max_v: number | null // null when the derived Nav2 params could not be prepared (see error)
    max_w: number | null
    range: [number, number]
    applied: boolean // Nav2's controller is listening to /speed_limit
    error: string | null
  }
  /** AMCL pose standard deviations (m, m, rad); null until AMCL reports */
  amcl: { std_x: number; std_y: number; std_yaw: number } | null
  /** Costmap versions; the PNG (GET /api/navigation/costmap/<key>.png) is fetched again when one changes */
  costmaps: { global: number | null; local: number | null }
  /** Current or last waypoint mission (every goal, single or a list, runs as one) */
  mission: NavMission | null
  /** Nav2 parameter variant of the running navigation (nav2_params_<variant>.yaml) */
  variant: string | null
}

export type NavGoalResult = NavGoalState | 'timeout' | 'stopped' | 'preempted'

/** One goal of a mission with its metrics (null until measured, or without ground truth) */
export interface NavMissionGoal {
  index: number
  x: number
  y: number
  yaw: number
  state: 'waiting' | 'active' | 'settling' | 'done'
  result: NavGoalResult | null
  time_s?: number | null
  path_m?: number | null
  goal_error_m?: number | null
  goal_yaw_error_deg?: number | null
  loc_error_m?: number | null
  recoveries?: number | null
  min_clearance_m?: number | null
  speed_scale?: number
}

export interface NavMission {
  id: string // run record folder ~/runs/<id>
  state: 'running' | 'finished' | 'canceled' | 'stopped' | 'preempted'
  index: number
  total: number
  has_gt: boolean
  goals: NavMissionGoal[]
  summary: {
    goals: number
    finished: number
    succeeded: number
    success_rate: number | null
    total_time_s: number | null
    total_path_m: number | null
    mean_goal_error_m: number | null
    min_clearance_m: number | null
    recoveries: number
    amcl_error: { samples: number; mean_m: number; rmse_m: number; max_m: number } | null
  }
  live?: { path_m: number | null; clearance_m: number | null; elapsed_s: number | null }
}

/** Costmap image placed in the map frame (origin = lower-left corner of the grid) */
export interface CostmapLayer {
  image: ImageBitmap
  origin: [number, number, number]
  resolution: number
  width: number
  height: number
}

export type NavGoalState = 'pending' | 'active' | 'canceling' | 'succeeded' | 'aborted' | 'canceled' | 'rejected'

export interface NavGoal {
  seq: number
  x: number
  y: number
  yaw: number
  state: NavGoalState
  elapsed: number
  feedback: { distance: number; nav_time: number; eta: number; recoveries: number } | null
}

/** 2D map mouse mode: pan the view, set the AMCL initial pose, or pick a navigation goal */
export type MapTool = 'pan' | 'pose' | 'goal' | 'waypoint'

/** Numbered waypoint drawn on the 2D map; kind colours it (draft = planned, not sent yet) */
export interface WaypointMark {
  x: number
  y: number
  yaw: number
  kind: 'draft' | 'waiting' | 'active' | 'ok' | 'fail'
}

/** Pose picked on the 2D map (map frame) */
export interface PickedPose {
  x: number
  y: number
  yaw: number
}

export interface StatusMsg {
  type: 'status'
  time: number
  clock: ClockInfo | null
  topics: TopicInfo[]
  tf: { expected: TfEdge[]; other: TfEdge[] }
  nodes: string[]
  stacks: StackInfo[]
  resources: { cpu: number; mem: number }
  /** Data folders of this console, for display (home written as ~); missing with an older backend */
  paths?: { maps: string; runs: string; results?: string }
}
