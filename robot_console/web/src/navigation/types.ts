export interface NavigationGrid {
  resolution: number
  width: number
  height: number
  origin: [number, number, number]
}

export interface NavigationMap {
  name: string
  has_cloud: boolean
  preset: string | null
  grid: NavigationGrid | null
}

export interface NavigationStatus {
  type: 'navigation'
  map: string | null
  state: 'idle' | 'starting' | 'running' | 'exited' | 'stopping'
  ready: boolean
  pose: { x: number; y: number; z: number; yaw: number } | null
  scan: number[][]
  global_path: number[][]
  local_path: number[][]
  age: number | null
  tf_ok: boolean
  error: string | null
}
