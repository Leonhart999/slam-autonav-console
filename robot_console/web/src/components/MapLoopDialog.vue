<!-- Loop optimization of a map saved with raw scans: offline planar loop back-end as a background task,
     preview of loops and figures, then a new map + run. The source map is never changed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { errorText } from '@/messages'
import { isCancelled, validResponse } from '@/http'
import { tasksApi, waitTask, type ConsoleTask } from '@/tasks'
import { taskMessages } from '@/taskMessages'
import { mapsApi, type LoopEnv, type LoopParams, type LoopResult, type MapDetail } from '@/maps/api'
import InfoTip from './InfoTip.vue'

const props = defineProps<{ modelValue: boolean; map: MapDetail | null; taskId?: string }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; saved: [string] }>()
const { t } = useI18n({ messages: {
  'zh-CN': { ...taskMessages['zh-CN'], lp: {
    title: '回环优化', desc: '用这张地图保存的原始扫描，找出重复经过的地方，配准验证后做平面位姿图优化（只改 x、y、航向），再重建点云和 2D 栅格。先预览，满意再另存为新地图；原地图和原运行记录不变。',
    noGround: '这张地图不是用"地面车辆约束"建的。没有地面约束时高度会漂，回环可能因"地面不一致"被拒。',
    goals: '新地图以第一帧为原点，离起点越远修正越大，所以原地图的导航目标点不会带到新地图。',
    notInstalled: '回环后端的 Python 环境不可用：{reason}。安装命令：', checking: '正在检查回环后端环境…',
    radius: '回环搜索半径 (m)', gap: '最小间隔路程 (m)', range: '地图取点距离 (m)', res: '地图体素 (m)', stride: '扫描间隔',
    overlap: '最低重叠率', rmse: '最大 RMSE (m)', shift: '单条回环最大平移修正 (m)', yaw: '单条回环最大航向修正 (°)',
    advanced: '其他参数', start: '开始优化', running: '优化中…', cancel: '关闭', discard: '放弃结果', save: '保存为新地图',
    name: '新地图名称', saved: '新地图已保存，运行记录 {run} 可在评估页对比', dirty: '参数已改变，请重新优化后保存',
    empty: '设置参数后点"开始优化"。HEU61 约 600 m 的数据大约需要 1–2 分钟。',
    summary: '{scans} 帧扫描 · {kf} 个关键帧 · 路程 {path} m · 用时 {sec} s',
    accepted: '检查 {n} 个候选，接受 {ok} 个；重访地点 {rev} 处', none: '没有接受的回环，不能保存。可以调大搜索半径、调小最小间隔路程，或检查下面的拒绝原因。',
    end: '终点修正：水平 {xy} m，航向 {yaw}°；全程最大水平修正 {max} m',
    wall: '重访处墙面点（路程 {sj} m ↔ {si} m）：中位距离 {b} → {a} cm，90% 在 {pb} → {pa} cm 以内',
    wallNote: '墙面点距离是同一批点在优化前后的自洽程度，不是真值精度。',
    col: { where: '路程 (m)', dist: '距离 (m)', overlap: '重叠率', rmse: 'RMSE (m)', shift: '修正', state: '结果' },
    ok: '接受',
    reason: { not_converged: '未收敛', poor_fit: '配准差', excessive_correction: '修正过大', ground_inconsistent: '地面不一致', degenerate_geometry: '几何退化' },
    fig: { trajectory_correction: '轨迹修正', junction_before_after: '重访处墙面', map_top_view_before_after: '地图俯视', grid_odom_preview: '原栅格', grid_loop_preview: '新栅格' },
    tip: {
      radius: '里程计认为两处相距不到这个距离，才把它们当作回环候选去配准验证。漂移比这个大的回环找不到；调大会多出候选，配准时间也更长。',
      gap: '两次经过之间至少要走过这么远，才算回环，防止把刚走过的路当成回环。场景小、路线短时可以调小；走廊里来回走时调小会增加误匹配的风险。',
      range: '重建点云和栅格时每帧只用离传感器这么近的点。远处的点受角度误差影响大。回环配准本身固定使用 50 m 以内的点。',
      res: '重建点云的体素边长。越小越细，文件越大。',
      stride: '重建点云和栅格时每隔几帧取一帧。调大更快，点云更稀。',
      overlap: '配准后新子图里落在旧子图 0.3 m 以内的点所占比例，低于这个值的回环被拒绝。',
      rmse: '这些点到旧子图距离的均方根，高于这个值的回环被拒绝。',
      shift: '单条回环把当前位置挪动超过这个距离就拒绝，防止配到别处。',
      yaw: '单条回环的航向修正超过这个角度就拒绝。',
    },
  } },
  en: { ...taskMessages.en, lp: {
    title: 'Loop optimization', desc: 'Uses the raw scans saved with this map to find places the robot passed twice, verifies them by registration, optimizes a planar pose graph (x, y and heading only) and rebuilds the cloud and the 2D grid. Preview first, then save as a new map; the source map and its run stay unchanged.',
    noGround: 'This map was not built with the ground vehicle constraint. Without it the height drifts, and loops may be rejected as ground-inconsistent.',
    goals: 'The new map keeps the first scan as its origin and corrections grow with the distance from the start, so the navigation goals of the source map are not copied.',
    notInstalled: 'The loop back-end Python environment is not usable: {reason}. Install it with:', checking: 'Checking the loop back-end environment…',
    radius: 'Loop search radius (m)', gap: 'Minimum path between visits (m)', range: 'Map point range (m)', res: 'Map voxel (m)', stride: 'Scan stride',
    overlap: 'Minimum overlap', rmse: 'Maximum RMSE (m)', shift: 'Maximum shift per loop (m)', yaw: 'Maximum heading change per loop (°)',
    advanced: 'Other parameters', start: 'Start optimization', running: 'Optimizing…', cancel: 'Close', discard: 'Discard result', save: 'Save as new map',
    name: 'New map name', saved: 'New map saved; compare run {run} on the Evaluation page', dirty: 'Parameters changed; run the optimization again before saving',
    empty: 'Set the parameters and press Start optimization. About 600 m of HEU61 data takes 1–2 minutes.',
    summary: '{scans} scans · {kf} keyframes · path {path} m · took {sec} s',
    accepted: '{n} candidates checked, {ok} accepted; places revisited: {rev}', none: 'No loop was accepted, so there is nothing to save. Try a larger search radius, a shorter minimum path, or check the rejection reasons below.',
    end: 'Correction at the end: {xy} m horizontal, {yaw}° heading; largest horizontal correction {max} m',
    wall: 'Wall points at the revisit (path {sj} m ↔ {si} m): median distance {b} → {a} cm, 90% within {pb} → {pa} cm',
    wallNote: 'Wall point distances measure how well the same points agree before and after; they are not ground-truth accuracy.',
    col: { where: 'Path (m)', dist: 'Distance (m)', overlap: 'Overlap', rmse: 'RMSE (m)', shift: 'Correction', state: 'Result' },
    ok: 'Accepted',
    reason: { not_converged: 'not converged', poor_fit: 'poor fit', excessive_correction: 'correction too large', ground_inconsistent: 'ground inconsistent', degenerate_geometry: 'degenerate geometry' },
    fig: { trajectory_correction: 'Trajectory correction', junction_before_after: 'Walls at the revisit', map_top_view_before_after: 'Map top view', grid_odom_preview: 'Original grid', grid_loop_preview: 'New grid' },
    tip: {
      radius: 'Two places become a loop candidate only if the odometry puts them closer than this. Loops with more drift are not found; a larger value gives more candidates and takes longer.',
      gap: 'The robot must travel at least this far between the two visits, so the path it has just driven does not count as a loop. Lower it for small scenes and short routes; in corridors driven back and forth a lower value raises the risk of false matches.',
      range: 'Only points this close to the sensor go into the rebuilt cloud and grid. Far points suffer more from angular error. Loop registration itself always uses points within 50 m.',
      res: 'Voxel edge of the rebuilt cloud. Smaller is finer and makes larger files.',
      stride: 'Use every n-th scan for the rebuilt cloud and grid. Larger is faster and gives a sparser cloud.',
      overlap: 'Share of points of the new submap within 0.3 m of the old submap after registration; loops below it are rejected.',
      rmse: 'Root mean square distance of those points to the old submap; loops above it are rejected.',
      shift: 'A loop that moves the current position by more than this is rejected, so a match to another place is not used.',
      yaw: 'A loop that changes the heading by more than this is rejected.',
    },
  } },
} })

const DEFAULTS: LoopParams = { loop_radius: 10, loop_min_gap: 100, map_range: 20, map_res: 0.05, scan_stride: 2,
  accept_overlap: 0.6, accept_rmse: 0.15, max_loop_shift: 5, max_loop_yaw_deg: 10 }
const params = reactive<LoopParams>({ ...DEFAULTS })
type Key = keyof LoopParams
const fields: { key: Key; label: string; min: number; max: number; step: number }[] = [
  { key: 'loop_radius', label: 'radius', min: 2, max: 30, step: 1 },
  { key: 'loop_min_gap', label: 'gap', min: 10, max: 1000, step: 10 },
  { key: 'map_range', label: 'range', min: 2, max: 50, step: 1 },
  { key: 'map_res', label: 'res', min: 0.02, max: 0.5, step: 0.01 },
  { key: 'scan_stride', label: 'stride', min: 1, max: 10, step: 1 },
]
const advanced: typeof fields = [
  { key: 'accept_overlap', label: 'overlap', min: 0.3, max: 0.95, step: 0.05 },
  { key: 'accept_rmse', label: 'rmse', min: 0.03, max: 0.5, step: 0.01 },
  { key: 'max_loop_shift', label: 'shift', min: 0.5, max: 20, step: 0.5 },
  { key: 'max_loop_yaw_deg', label: 'yaw', min: 1, max: 45, step: 1 },
]

const open = computed({ get: () => props.modelValue, set: (v) => { if (!saving.value) emit('update:modelValue', v) } })
const env = shallowRef<LoopEnv | null>(null)
const result = shallowRef<LoopResult | null>(null)
const figures = shallowRef<Record<string, string>>({})   // figure -> object URL
const figure = ref('')
const task = shallowRef<ConsoleTask<LoopResult> | null>(null)
const busy = ref(false)
const saving = ref(false)
const error = ref('')
const name = ref('')
const signature = computed(() => JSON.stringify(params))
const resultSignature = ref('')
const dirty = computed(() => !!result.value && signature.value !== resultSignature.value)
const canSave = computed(() => !!result.value && result.value.accepted > 0 && !dirty.value && !busy.value
  && /^[A-Za-z0-9_-]{1,48}$/.test(name.value))
let abort = new AbortController()

function dropFigures() {
  for (const url of Object.values(figures.value)) URL.revokeObjectURL(url)
  figures.value = {}
  figure.value = ''
}
function clear() {
  abort.abort(); abort = new AbortController()
  dropFigures(); result.value = null; task.value = null; busy.value = false
}
onBeforeUnmount(clear)

watch([() => props.modelValue, () => props.map?.name, () => props.taskId], async ([v]) => {
  clear()
  if (!v || !props.map) return
  Object.assign(params, DEFAULTS)
  error.value = ''; env.value = null
  name.value = props.map.name.slice(0, 43) + '_loop'
  const ctrl = abort
  void mapsApi.loopEnv(ctrl.signal).then(e => { if (!ctrl.signal.aborted) env.value = e }).catch(() => {})
  if (!props.taskId) return
  busy.value = true
  try {
    const recovered = await tasksApi.get<LoopResult>(props.taskId, ctrl.signal)
    if (ctrl.signal.aborted) return
    if (recovered.kind !== 'loop' || recovered.target !== props.map.name) throw new Error('Task belongs to another map')
    if (recovered.saved_map) { emit('saved', recovered.saved_map); emit('update:modelValue', false); return }
    for (const [key, value] of Object.entries(recovered.input)) {
      if (key in params && typeof value === 'number') params[key as Key] = value
    }
    const sig = signature.value
    const r = await waitTask(recovered, ctrl.signal, current => { if (!ctrl.signal.aborted) task.value = current })
    await show(r, ctrl, sig)
  } catch (e) {
    if (!ctrl.signal.aborted && !isCancelled(e)) {
      error.value = e instanceof Error && 'status' in e && e.status === 404 ? t('tasks.expired') : errorText(e)
    }
  } finally { if (abort === ctrl) busy.value = false }
}, { immediate: true })

async function show(r: LoopResult, ctrl: AbortController, sig: string) {
  if (ctrl.signal.aborted) return
  if (!validResponse(`/api/maps/${encodeURIComponent(r.parent)}/loop`, r) || r.parent !== props.map?.name) {
    throw new Error('Invalid loop optimization result')
  }
  const urls: Record<string, string> = {}
  for (const f of r.figures) {
    try { urls[f] = URL.createObjectURL(await mapsApi.loopFigure(r.token, f, ctrl.signal)) } catch (e) {
      // cancelled, or the result was saved / discarded / expired meanwhile (404): show nothing of it
      const gone = e instanceof Error && 'status' in e && e.status === 404
      if (isCancelled(e) || gone) { for (const u of Object.values(urls)) URL.revokeObjectURL(u) }
      if (isCancelled(e)) return
      if (gone) throw e
    }
  }
  if (ctrl.signal.aborted) { for (const u of Object.values(urls)) URL.revokeObjectURL(u); return }
  dropFigures()
  figures.value = urls
  figure.value = r.figures.find(f => f === 'junction_before_after') ?? r.figures[0] ?? ''
  result.value = r
  resultSignature.value = sig
}

async function start() {
  if (!props.map || busy.value || saving.value) return
  const previous = result.value?.token
  const ctrl = abort, sig = signature.value, map = props.map.name
  busy.value = true; error.value = ''; result.value = null; dropFigures()
  // An earlier unsaved result of this dialog is replaced; results are large and only 3 may wait.
  if (previous) void mapsApi.discardLoop(previous).catch(() => {})
  try {
    const r = await mapsApi.loop(map, { ...params }, ctrl.signal, current => { if (!ctrl.signal.aborted) task.value = current })
    await show(r, ctrl, sig)
  } catch (e) { if (!ctrl.signal.aborted && !isCancelled(e)) error.value = errorText(e) }
  finally { if (abort === ctrl) busy.value = false }
}

async function discard() {
  const token = result.value?.token
  if (!token || busy.value || saving.value) return
  try { await mapsApi.discardLoop(token) } catch { /* already expired */ }
  dropFigures(); result.value = null; task.value = null
}

async function save() {
  if (!result.value || !canSave.value || saving.value) return
  const ctrl = abort, token = result.value.token
  saving.value = true; error.value = ''
  try {
    const d = await mapsApi.saveLoop(token, name.value)
    if (ctrl.signal.aborted) return
    dropFigures(); result.value = null
    ElMessage.success(t('lp.saved', { run: d.loop_run }))
    emit('saved', d.name); emit('update:modelValue', false)
  } catch (e) { if (!ctrl.signal.aborted) error.value = errorText(e) }
  finally { saving.value = false }
}

const cm = (v: number | undefined) => v === undefined ? '—' : (v * 100).toFixed(1)
function reasons(list: string[]) { return list.map(r => t(`lp.reason.${r}`)).join('、') }
</script>

<template>
  <el-dialog v-model="open" :title="t('lp.title')" width="min(1200px, 96vw)" :close-on-click-modal="false"
    :close-on-press-escape="!saving" :show-close="!saving" destroy-on-close>
    <p>{{ t('lp.desc') }}</p>
    <p>{{ t('tasks.background') }}</p>
    <el-alert v-if="map && map.loop && !map.loop.ground" :title="t('lp.noGround')" type="warning" :closable="false" show-icon />
    <div v-if="env && !env.installed" class="warning install">
      {{ t('lp.notInstalled', { reason: env.reason ?? '' }) }}<code class="mono">{{ env.install }}</code>
    </div>
    <p v-if="task" role="status" class="mono">{{ t('tasks.id') }}: {{ task.id }} · {{ t(`tasks.phases.${task.phase}`) }}</p>
    <div class="layout">
      <el-form label-position="top" class="controls" :disabled="busy || saving">
        <el-form-item v-for="f in fields" :key="f.key">
          <template #label>{{ t(`lp.${f.label}`) }}<InfoTip :content="t(`lp.tip.${f.label}`)" /></template>
          <el-input-number v-model="params[f.key]" :min="f.min" :max="f.max" :step="f.step" />
        </el-form-item>
        <details><summary>{{ t('lp.advanced') }}</summary>
          <el-form-item v-for="f in advanced" :key="f.key">
            <template #label>{{ t(`lp.${f.label}`) }}<InfoTip :content="t(`lp.tip.${f.label}`)" /></template>
            <el-input-number v-model="params[f.key]" :min="f.min" :max="f.max" :step="f.step" />
          </el-form-item>
        </details>
        <el-button type="primary" :loading="busy" :disabled="!map?.has_scans || (env !== null && !env.installed)" @click="start">
          {{ busy ? t('lp.running') : t('lp.start') }}
        </el-button>
      </el-form>
      <div class="output">
        <p v-if="!result && !busy" class="muted">{{ t('lp.empty') }}</p>
        <template v-if="result">
          <p>{{ t('lp.summary', { scans: result.scans, kf: result.keyframes, path: result.path_m, sec: result.elapsed }) }}</p>
          <p>{{ t('lp.accepted', { n: result.loops.length, ok: result.accepted, rev: result.revisits }) }}</p>
          <el-alert v-if="result.accepted === 0" :title="t('lp.none')" type="warning" :closable="false" show-icon />
          <p v-if="result.end_correction && result.max_correction">{{ t('lp.end', { xy: result.end_correction.xy_m.toFixed(2),
            yaw: result.end_correction.yaw_deg.toFixed(2), max: result.max_correction.xy_m.toFixed(2) }) }}</p>
          <template v-for="j in result.junctions" :key="`${j.s_i}-${j.s_j}`">
            <p v-if="j.wall_before && j.wall_after">{{ t('lp.wall', { sj: j.s_j, si: j.s_i, b: cm(j.wall_before.median_m),
              a: cm(j.wall_after.median_m), pb: cm(j.wall_before.p90_m), pa: cm(j.wall_after.p90_m) }) }}</p>
          </template>
          <p v-if="result.junctions.length" class="muted">{{ t('lp.wallNote') }}</p>
          <el-table v-if="result.loops.length" :data="result.loops" size="small" max-height="220" class="loops">
            <el-table-column :label="t('lp.col.where')" min-width="110"><template #default="{ row }">{{ row.s_j }} ↔ {{ row.s_i }}</template></el-table-column>
            <el-table-column :label="t('lp.col.dist')" prop="dist" min-width="110" />
            <el-table-column :label="t('lp.col.overlap')" min-width="100"><template #default="{ row }">{{ row.overlap_before.toFixed(2) }} → {{ row.overlap_after.toFixed(2) }}</template></el-table-column>
            <el-table-column :label="t('lp.col.rmse')" min-width="110"><template #default="{ row }">{{ row.rmse_before.toFixed(3) }} → {{ row.rmse_after.toFixed(3) }}</template></el-table-column>
            <el-table-column :label="t('lp.col.shift')" min-width="110"><template #default="{ row }">{{ row.shift_xy.toFixed(2) }} m · {{ row.yaw_deg.toFixed(2) }}°</template></el-table-column>
            <el-table-column :label="t('lp.col.state')" min-width="130">
              <template #default="{ row }"><span :class="row.ok ? 'good' : 'warning'">{{ row.ok ? t('lp.ok') : reasons(row.reasons) }}</span></template>
            </el-table-column>
          </el-table>
          <template v-if="result.figures.length">
            <el-radio-group v-model="figure" size="small" class="figure-tabs">
              <el-radio-button v-for="f in result.figures" :key="f" :value="f">{{ t(`lp.fig.${f}`) }}</el-radio-button>
            </el-radio-group>
            <div class="figure"><img v-if="figures[figure]" :src="figures[figure]" :alt="t(`lp.fig.${figure}`)" /></div>
          </template>
        </template>
        <el-alert v-if="dirty" :title="t('lp.dirty')" type="warning" :closable="false" />
        <p v-if="error" class="warning">{{ error }}</p>
        <p class="muted">{{ t('lp.goals') }}</p>
        <el-form-item :label="t('lp.name')"><el-input v-model="name" :disabled="busy || saving" maxlength="48" /></el-form-item>
      </div>
    </div>
    <template #footer>
      <el-button :disabled="saving" @click="open=false">{{ t('lp.cancel') }}</el-button>
      <el-button :disabled="!result || busy || saving" @click="discard">{{ t('lp.discard') }}</el-button>
      <el-button type="primary" :loading="saving" :disabled="!canSave" @click="save">{{ t('lp.save') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:24px}.controls{max-height:65vh;overflow:auto;padding-right:12px}.controls .el-input-number{width:100%}.controls .el-form-item{margin-bottom:12px}.output{min-width:0}.output p{font-size:13px}.muted{color:var(--ink-3);font-size:12px;line-height:1.6}.warning{color:var(--warn);overflow-wrap:anywhere}.good{color:var(--ok, #1baf7a)}summary{cursor:pointer;margin:14px 0}.install{margin:10px 0;font-size:13px}.install code{display:block;margin-top:6px;padding:6px 8px;border-radius:8px;background:var(--glass);border:1px solid var(--hairline);user-select:all}.loops{margin:8px 0 12px}.figure-tabs{margin:6px 0 10px;flex-wrap:wrap}.figure{background:#fff;border:1px solid var(--hairline);border-radius:12px;overflow:auto;max-height:430px;min-height:120px}.figure img{display:block;width:100%;height:auto}.el-alert{margin:8px 0}@media(max-width:800px){.layout{grid-template-columns:1fr}.controls{max-height:300px}}
</style>
