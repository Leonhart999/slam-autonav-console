// REST 接口封装。后端出错时返回 {code, params}，这里包装成 ApiError；
// 显示给用户的文字用 messages.ts 里的 errorText() 按当前语言生成
import type { CodedMsg, MapSceneInfo, MappingPreset, NavMap, NavMission, NavSolid, NavStatus, PickedPose, StackInfo } from './types'
import type { BagInfo, PlaybackStatus, BagTrash, PlaybackStorage } from './mapping/stream'

export class ApiError extends Error {
  readonly coded: CodedMsg
  constructor(coded: CodedMsg) {
    super(coded.code)
    this.coded = coded
  }
}

async function request<T>(method: string, path: string, json?: unknown): Promise<T> {
  const res = await fetch(path, json === undefined ? { method } : {
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(json),
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const d = body.detail
    if (d && typeof d === 'object' && 'code' in d) throw new ApiError(d as CodedMsg)
    throw new ApiError({ code: 'unknown', params: { reason: String(d ?? `${res.status} ${res.statusText}`) } })
  }
  return body as T
}

export interface UploadProgress {
  loaded: number
  total: number | null
  sent: boolean
}

export type UploadProgressHandler = (progress: UploadProgress) => void

/** 浏览器生成 multipart 边界；只报告实际发送的字节，发送完后等待后端校验和导入。
 *  data may also be a File sent as the raw body (bag uploads use PUT, one file per request). */
function upload<T>(
  path: string, data: FormData | Blob, onProgress: UploadProgressHandler, method = 'POST', signal?: AbortSignal,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    let loaded = 0
    let total: number | null = null
    xhr.open(method, path)
    xhr.upload.onprogress = (event) => {
      loaded = event.loaded
      total = event.lengthComputable ? event.total : null
      onProgress({ loaded, total, sent: false })
    }
    xhr.upload.onload = () => onProgress({ loaded, total, sent: true })
    xhr.onload = () => {
      let body: unknown
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        reject(new ApiError({ code: 'import_failed', params: { reason: `${xhr.status} ${xhr.statusText}` } }))
        return
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const d = body && typeof body === 'object' && 'detail' in body ? body.detail : undefined
        reject(new ApiError(d && typeof d === 'object' && 'code' in d
          ? d as CodedMsg
          : { code: 'import_failed', params: { reason: String(d ?? `${xhr.status} ${xhr.statusText}`) } }))
        return
      }
      resolve(body as T)
    }
    // 网络中断时后端可能仍在处理，提示先确认结果再重试，避免重复上传。
    xhr.onerror = () => reject(new ApiError({ code: 'import_network', params: {} }))
    xhr.onabort = () => reject(new ApiError({ code: 'import_network', params: {} }))
    signal?.addEventListener('abort', () => xhr.abort())
    onProgress({ loaded: 0, total: null, sent: false })
    xhr.send(data)
  })
}

export const api = {
  /** preset：建图场景预设名；不给时后端按默认预设或正在运行的仿真决定 */
  /** origin: which page started it (the navigation page warns when the simulation came from elsewhere) */
  /** bag (Playback page, SLAM only): a bag of a real robot starts the SLAM with that robot's configuration */
  startStack: (name: string, preset?: string, origin?: 'mapping' | 'navigation' | 'playback', bag?: string) => {
    const q = new URLSearchParams()
    if (preset) q.set('preset', preset)
    if (origin) q.set('origin', origin)
    if (bag) q.set('bag', bag)
    const qs = q.toString()
    return request<StackInfo>('POST', `/api/stacks/${encodeURIComponent(name)}/start${qs ? `?${qs}` : ''}`)
  },
  /** Navigation page simulation switch: the scene comes from the map's meta.json */
  startNavSim: (map: string) => request<StackInfo>('POST', `/api/navigation/sim/start?name=${encodeURIComponent(map)}`),
  stopNavSim: () => request<unknown>('POST', '/api/navigation/sim/stop'),
  restartNavSim: (map: string) => request<unknown>('POST', `/api/navigation/sim/restart?name=${encodeURIComponent(map)}`),
  mappingPresets: () => request<MappingPreset[]>('GET', '/api/mapping/presets'),
  /** Delete an uploaded scene (custom_*); the backend moves its folder to the scenes trash */
  deleteScene: (preset: string) =>
    request<{ name: string; trash: string }>('DELETE', `/api/mapping/presets/${encodeURIComponent(preset)}`),
  importMapping: (data: FormData, onProgress: UploadProgressHandler) =>
    upload<MappingPreset>('/api/mapping/import', data, onProgress),
  importNavigation: (data: FormData, onProgress: UploadProgressHandler) =>
    upload<NavMap>('/api/navigation/import', data, onProgress),
  /** 急停后解除遥控锁定 */
  unlockTeleop: () => request<unknown>('POST', '/api/teleop/unlock'),
  /** 把当前建图结果保存到地图库 ~/maps/<name>/（后台进行，进度看建图状态的 save 字段） */
  saveMap: (name: string) => request<unknown>('POST', `/api/mapping/save?name=${encodeURIComponent(name)}`),
  stopStack: (name: string) => request<StackInfo>('POST', `/api/stacks/${encodeURIComponent(name)}/stop`),
  stackLog: (name: string, lines = 300) =>
    request<{ name: string; text: string }>('GET', `/api/stacks/${encodeURIComponent(name)}/log?lines=${lines}`),
  estop: () => request<{ cancelled_actions: string[]; stopped_stacks: string[] }>('POST', '/api/estop'),
  navigationMaps: () => request<NavMap[]>('GET', '/api/navigation/maps'),
  /** Simulation scene of a map without a recorded one (bag playback / imported maps), see map_scene.py */
  mapScene: (map: string) =>
    request<MapSceneInfo>('GET', `/api/navigation/maps/${encodeURIComponent(map)}/scene`),
  bindMapScene: (map: string, preset: string) =>
    request<MapSceneInfo>('POST',
      `/api/navigation/maps/${encodeURIComponent(map)}/scene?preset=${encodeURIComponent(preset)}`),
  unbindMapScene: (map: string) =>
    request<MapSceneInfo>('DELETE', `/api/navigation/maps/${encodeURIComponent(map)}/scene`),
  generateMapScene: (map: string, height: number) =>
    request<MapSceneInfo>('POST', `/api/navigation/maps/${encodeURIComponent(map)}/scene/generate?height=${height}`),
  navigationStatus: () => request<NavStatus>('GET', '/api/navigation/status'),
  startNavigation: (name: string, variant?: string) =>
    request<StackInfo>('POST', `/api/navigation/start?name=${encodeURIComponent(name)}` +
      (variant ? `&variant=${encodeURIComponent(variant)}` : '')),
  navigationVariants: () => request<{ variants: string[]; default: string }>('GET', '/api/navigation/variants'),
  stopNavigation: () => request<StackInfo>('POST', '/api/navigation/stop'),
  setInitialPose: (p: { x: number; y: number; yaw: number }) =>
    request<unknown>('POST', `/api/navigation/initial_pose?x=${p.x}&y=${p.y}&yaw=${p.yaw}`),
  sendGoal: (p: { x: number; y: number; yaw: number }) =>
    request<NavMission>('POST', `/api/navigation/goal?x=${p.x}&y=${p.y}&yaw=${p.yaw}`),
  /** Goals one after another, each with its own result; recorded in ~/runs/nav_<time>/ */
  startMission: (goals: PickedPose[]) => request<NavMission>('POST', '/api/navigation/mission', { goals }),
  loadGoals: (map: string) =>
    request<{ goals: PickedPose[]; saved: string | null }>('GET', `/api/navigation/maps/${encodeURIComponent(map)}/goals`),
  saveGoals: (map: string, goals: PickedPose[]) =>
    request<{ goals: PickedPose[]; saved: string | null }>('PUT', `/api/navigation/maps/${encodeURIComponent(map)}/goals`, { goals }),
  cancelGoal: () => request<unknown>('POST', '/api/navigation/cancel'),
  setNavSpeed: (scale: number) => request<unknown>('POST', `/api/navigation/speed?scale=${scale}`),
  navigationSolid: (name: string) =>
    request<NavSolid>('GET', `/api/navigation/maps/${encodeURIComponent(name)}/solid`),
  /** Bag playback on the playback page (robot_console/playback.py); begin / end: part of the bag in s */
  playbackBags: () => request<{ roots: string[]; bags: BagInfo[] }>('GET', '/api/playback/bags'),
  startPlayback: (bag: string, rate: number, begin = 0, end?: number) =>
    request<PlaybackStatus>('POST', `/api/playback/start?bag=${encodeURIComponent(bag)}&rate=${rate}&begin=${begin}` +
      (end != null ? `&end=${end}` : '')),
  pausePlayback: (paused: boolean) => request<PlaybackStatus>('POST', `/api/playback/pause?paused=${paused}`),
  setPlaybackRate: (rate: number) => request<PlaybackStatus>('POST', `/api/playback/rate?rate=${rate}`),
  stopPlayback: () => request<PlaybackStatus>('POST', '/api/playback/stop'),
  /** Bag files (robot_console/bag_files.py): upload folder, trash, free space */
  playbackStorage: () => request<PlaybackStorage>('GET', '/api/playback/storage'),
  beginBagUpload: (name: string, size: number, files: number) =>
    request<{ token: string; target: string }>('POST',
      `/api/playback/uploads?name=${encodeURIComponent(name)}&size=${size}&files_count=${files}`),
  putBagFile: (token: string, file: File, onProgress: UploadProgressHandler, signal?: AbortSignal) =>
    upload<{ path: string; bytes: number }>(
      `/api/playback/uploads/${encodeURIComponent(token)}?path=${encodeURIComponent(file.name)}`, file, onProgress, 'PUT', signal),
  finishBagUpload: (token: string) =>
    request<{ name: string; path: string; size: number }>('POST', `/api/playback/uploads/${encodeURIComponent(token)}/finish`),
  cancelBagUpload: (token: string) => request<unknown>('DELETE', `/api/playback/uploads/${encodeURIComponent(token)}`),
  deleteBag: (path: string) => request<{ name: string; trash: string }>('DELETE', `/api/playback/bags?path=${encodeURIComponent(path)}`),
  bagTrash: () => request<BagTrash>('GET', '/api/playback/trash'),
  restoreBag: (id: string) =>
    request<{ restored: string; name: string }>('POST', `/api/playback/trash/restore?id=${encodeURIComponent(id)}`),
  purgeBag: (id: string) => request<{ freed: number }>('DELETE', `/api/playback/trash/item?id=${encodeURIComponent(id)}`),
  emptyBagTrash: () => request<{ freed: number }>('DELETE', '/api/playback/trash'),
  /** Open a data folder in the desktop file manager of the machine running the console (open_folder.py) */
  openFolder: (path: string) =>
    request<{ path: string; opened: string }>('POST', `/api/open-folder?path=${encodeURIComponent(path)}`),
}

/** Binary or image map assets; throws ApiError like request() */
export async function fetchAsset(path: string, signal: AbortSignal): Promise<Response> {
  const res = await fetch(path, { signal })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const d = body.detail
    if (d && typeof d === 'object' && 'code' in d) throw new ApiError(d as CodedMsg)
    throw new ApiError({ code: 'unknown', params: { reason: `${res.status} ${res.statusText}` } })
  }
  return res
}
