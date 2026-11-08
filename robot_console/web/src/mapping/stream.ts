// 建图页的数据流：接收后端推送的建图状态（JSON）和点云（二进制），存进可增长的 Float32Array。
// 点数据量大，不放进 Vue 的响应式系统；3D 视图每帧检查 version，有变化才更新显卡里的数据
import { shallowRef } from 'vue'
import type { useStatusStore } from '@/stores/status'

export interface MappingStatus {
  type: 'mapping'
  epoch: number
  frame: string
  voxel: number
  pose: { x: number; y: number; z: number; yaw: number } | null
  speed: number
  initialized: boolean
  duration: number | null
  length: number
  voxels: number
  extent: [number, number, number, number, number, number] | null // xmin ymin zmin xmax ymax zmax
  error: number | null
  has_gt: boolean
  publishers: { scan: number; odom: number } // 发布者多于 1 个说明有两个 SLAM 在同时跑
  stacks: { sim?: string; slam?: string; waypoints?: string }
  topics: { scan: string; odom: string; gt: string | null }
  /** Selected SLAM (stacks.yaml name), its algorithm and how its map is saved (console.yaml slam_profiles) */
  slam?: string
  algorithm?: string
  save_mode?: SlamSaveMode
  slam_options?: SlamOption[]
  // 航点进程的进度（从它的日志里解析）；从没启动过时为 null
  waypoints: {
    done: number
    total: number | null
    timeout: boolean
    state: 'idle' | 'running' | 'stopping' | 'exited'
    exit_code: number | null
  } | null
  // 遥控状态：active 正在发非零速度；locked 急停后锁定；busy 自动航点在运行
  teleop: {
    active: boolean
    locked: boolean
    busy: boolean
    v: number
    w: number
    max_v: number
    max_w: number
    /** Recommended linear speed band [low, high] in m/s, shown on the slider */
    recommended_v?: [number, number]
  }
  run: string | null // 当前运行记录编号（~/runs/<run>/）
  save: SaveJob // 保存地图任务的进度
  /** Bag playback (robot_console/playback.py); missing from an older backend */
  playback?: PlaybackStatus
}

/** A rosbag2 folder found under playback.bag_dirs (GET /api/playback/bags) */
export interface BagInfo {
  path: string
  name: string // relative to its bag folder root
  root: string
  storage: string
  duration: number // s
  start: number // s since epoch (recording time)
  messages: number
  size: number // bytes
  topics: { name: string; type: string; count: number }[]
  supported: boolean // the storage plugin is installed
  reason: 'storage' | null
  points: string[] // PointCloud2 topics
  imu: string[] // Imu topics
  has_clock: boolean
  has_gt: boolean
  /** Per SLAM (stacks.yaml name): can it run on this bag, and which bag topic feeds which input */
  slams: Record<string, { ok: boolean; missing: string[]; remapped: boolean; plan: PlaybackPlan }>
}
export type PlaybackPlan = Record<string, { from: string; to: string }>

/** Where uploads go, what the trash holds and how much space is left (GET /api/playback/storage) */
export interface PlaybackStorage {
  upload_dir: string
  trash: { root: string; path: string; size: number; items: number }[]
  free: { path: string; free: number }[]
}

export interface PlaybackStatus {
  stack: string
  available: boolean
  state: 'idle' | 'running' | 'stopping' | 'exited'
  rates: number[]
  roots: string[]
  slam: string | null // SLAM stack running now
  slam_ready: boolean // it has subscribed to its point cloud input
  needs_slam_restart: boolean // it already received a playback
  /** s the SLAM's odometry is behind the playback clock (null before the first odometry) */
  slam_lag?: number | null
  /** the bag has ended but the SLAM is still working on queued scans */
  catching_up?: boolean
  // the current / last playback
  bag?: string
  name?: string
  rate?: number
  paused?: boolean
  duration?: number
  position?: number
  plan?: PlaybackPlan
  finished?: boolean
  /** part of the bag being played, [begin, end] in s */
  range?: [number, number]
  exit_code?: number | null
}

/** 保存地图任务（后端 map_library.py）。step 依次是 map_save、copy、project、meta */
/** 'service': FAST-LIO writes the map on /map_save and keeps running; 'dump': GLIM writes it when it exits */
export type SlamSaveMode = 'service' | 'dump'
export interface SlamOption {
  name: string
  algorithm: string
  save_mode: SlamSaveMode
  /** 'sim' and / or 'bag': offered for the simulation, for bag playback or both */
  sources?: ('sim' | 'bag')[]
  needs_imu?: boolean
}

export interface SaveJob {
  state: 'idle' | 'running' | 'done' | 'failed'
  name?: string
  step?: 'stop_slam' | 'map_save' | 'copy' | 'project' | 'figures' | 'meta'
  code?: string
  params?: Record<string, string | number>
  finished?: number
  result?: {
    dir: string
    points: number | null
    voxels: number
    grid: { width: number; height: number; resolution: number; size_m: [number, number] } | null
    figures_error?: string | null
    project_error: string | null
    run: string | null
  }
}

/** 只增不减的点数组（x y z 连续排列），容量不够时翻倍 */
export class PointBuffer {
  data = new Float32Array(3 * 4096)
  count = 0
  version = 0 // 每次内容变化加 1
  resets = 0 // 每次清空加 1（3D 视图据此判断要整体重传，而不是只传新增部分）

  append(pts: Float32Array) {
    const need = this.count * 3 + pts.length
    if (need > this.data.length) {
      const grown = new Float32Array(Math.max(need, this.data.length * 2))
      grown.set(this.data.subarray(0, this.count * 3))
      this.data = grown
    }
    this.data.set(pts, this.count * 3)
    this.count += pts.length / 3
    this.version++
  }

  replace(pts: Float32Array) {
    this.clear()
    this.append(pts)
  }

  clear() {
    this.count = 0
    this.resets++
    this.version++
  }
}

/** 3D 视图的图层和相机模式 */
export type LayerKey = 'map' | 'scan' | 'est' | 'gt' | 'grid'
export type ViewMode = 'follow' | 'free' | 'top'

export type MappingStream = ReturnType<typeof createMappingStream>

export function createMappingStream(store: ReturnType<typeof useStatusStore>) {
  const status = shallowRef<MappingStatus | null>(null)
  const map = new PointBuffer() // 体素地图
  const scan = new PointBuffer() // 当前帧
  const est = new PointBuffer() // 估计轨迹
  const gt = new PointBuffer() // 真值轨迹（已对齐到建图坐标系）
  const buffers = { map, est, gt } as const
  let epoch = -1 // 后端每开始一次新建图 epoch 加 1；-1 表示正在等后端从头重发
  const decoder = new TextDecoder()
  const off: Array<() => void> = []

  function clearAll() {
    map.clear()
    scan.clear()
    est.clear()
    gt.clear()
  }

  /** 数据对不上时：清空，请后端从头重发 */
  function resync() {
    epoch = -1
    clearAll()
    store.subscribe('mapping')
  }

  function onJson(msg: { type: string; [k: string]: unknown }) {
    if (msg.type === 'mapping') status.value = msg as unknown as MappingStatus
    else if (msg.type === 'mapping_reset') {
      epoch = msg.epoch as number
      clearAll()
    }
  }

  // 二进制帧：[4 字节头长度][JSON 头][float32 x y z ...]，后端已保证数据部分 4 字节对齐
  function onBinary(buf: ArrayBuffer) {
    const headLen = new DataView(buf).getUint32(0, true)
    const head = JSON.parse(decoder.decode(new Uint8Array(buf, 4, headLen)))
    if (head.epoch !== epoch) return // 旧一次建图的数据，或者正在等重发
    const pts = new Float32Array(buf, 4 + headLen, head.count * 3)
    if (head.type === 'scan') {
      scan.replace(pts)
      return
    }
    const target = buffers[head.type as keyof typeof buffers]
    if (!target) return
    if (head.offset !== target.count) {
      resync() // 中间漏了数据（正常不会发生）
      return
    }
    target.append(pts)
  }

  function start() {
    off.push(store.addJsonHandler(onJson), store.addBinaryHandler(onBinary))
    resync()
  }

  function stop() {
    off.splice(0).forEach((f) => f())
    store.unsubscribe('mapping')
  }

  return { status, map, scan, est, gt, start, stop }
}
