<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { errorText } from '@/messages'
import { mapsApi, type MapDetail, type RegridPreview } from '@/maps/api'
import type { NavMap } from '@/types'
import NavGrid2D from './NavGrid2D.vue'
import NavScene3D from './NavScene3D.vue'
import InfoTip from './InfoTip.vue'

const props = defineProps<{ modelValue: boolean; map: MapDetail | null; cloud: Float32Array | null }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; saved: [string] }>()
const { t } = useI18n({ messages: {
  'zh-CN': { rg: { title: '从 3D 点云重新生成 2D', desc: '保持原点云坐标系，不调平或旋转。先预览，再另存新版本；原地图不变。', res: '分辨率 (m)', low: '障碍物最低高度 (m)', high: '障碍物最高高度 (m)', mode: '地面估计', plane: '整体平面', grid: '分块平面', none: '不估计（绝对 z）', heightmap: '连续高度图（实验）', tol: '地面容差 (m)', points: '每格最少障碍点', tile: '分块尺寸 (m)', voxel: '点云降采样 (m，0 关闭)', inflate: '障碍膨胀 (m，0 关闭)', closing: '自由区闭运算次数', exp: '启用实验功能（默认关闭）', risk: '高度图和小区域过滤尚未正式发布。可能漏掉细小障碍或误判地面，保存后请人工检查。', cell: '地面格宽 (m)', smooth: '地面平滑尺度 (m)', obsArea: '删除小障碍面积 (m²)', freeArea: '删除小自由区面积 (m²)', tip: { res: '每个栅格的边长。越小越精细，但地图更大、投影更慢，点云稀疏处容易出现空洞。Nav2 常用 0.05 m。', low: '高出地面多少以上的点算作障碍。太小会把地面起伏和噪点当成障碍；太大会漏掉门槛、低矮箱子等矮障碍。', high: '高出地面多少以下的点算作障碍。应略高于机器人自身高度；更高的点（天花板、吊灯、横梁）会被忽略，机器人可以从下面通过。', mode: '决定障碍高度相对于什么计算。地面平整时用“整体平面”；地面有缓坡或点云整体有轻微弯曲时用“分块平面”；点云已经水平且地面在 z = 0 附近时可以用“不估计”。', plane: '用全部点拟合一个地面平面，能自动修正点云的轻微倾斜。适合平整的单层室内地面。', grid: '把地图按“分块尺寸”切成方块，每块单独拟合地面平面；地面点太少的方块退回整体平面。适合有缓坡或不太平整的地面。', none: '不估计地面，直接用点云原始 z 作为高度。只在点云已经水平且地面就在 z = 0 时使用，否则障碍高度会整体偏移。', tol: '离地面不超过该距离的点算作地面，这些格子会标为可通行。太小会让地面噪点变成未知区域；太大会把矮障碍的下沿也算作地面。', points: '一个格子里至少要有这么多个障碍高度带内的点，才标为障碍。调大可以压掉零星噪点，但稀疏扫描到的细杆可能消失。', tile: '“分块平面”模式下每块的边长，只在该模式下生效。块越小越能贴合地面起伏，但每块点数少，拟合容易不稳。', voxel: '投影前先按该尺寸做体素降采样，每个体素只留一个点，可以加快大点云的投影。设得比分辨率大时，“每格最少障碍点”会更难满足。0 表示关闭。', inflate: '把障碍按该半径向外扩张后写进地图。Nav2 代价地图本身会膨胀障碍，这里一般保持 0；只在需要让静态地图更保守时使用。', closing: '对可通行区做形态学闭运算的次数，用来填补地面点之间的小空洞和缝隙。次数越多填得越满，但也可能把窄缝或门口误填成可通行。', exp: '打开后可以选择“连续高度图”地面模型，并显示小区域过滤参数。这些功能还没正式发布，关闭开关会恢复默认行为。', heightmap: '把地面分成小格，取每格最低点作为地面高度，补洞、中值去噪、高斯平滑后得到一张连续的地面高度图，障碍高度按它计算。适合有坡度或起伏的单层地面。不适合多楼层；没有扫到地面的大片区域（如桌面下方）可能把桌面当成地面。', cell: '连续高度图每一格的边长，只在“连续高度图”模式下生效。格子越小越贴合地面起伏，但每格点数少、容易受噪点影响；格子越大越稳，但会抹掉小坡。默认 0.5 m。', smooth: '对地面高度图做高斯平滑的尺度，只在“连续高度图”模式下生效。值越大地面越平，能压掉局部噪点，也会削平真实的台阶和坡边。0 表示不平滑。默认 0.5 m。', obsArea: '面积小于该值的孤立障碍块（四连通）会被删除，用来去掉零星噪点。删除的格子变为“未知”，不会变成可通行。可能误删桌腿、细杆等真实的细小障碍。在障碍膨胀之前执行。0 表示关闭。', freeArea: '面积小于该值的孤立自由块会改为“未知”，用来去掉墙后、障碍缝隙里零碎的可通行格。在障碍膨胀之前执行。0 表示关闭。' }, preview: '生成预览', generating: '投影中…', result: '新栅格', original: '原栅格', noPreview: '调整参数后生成预览', name: '新版本名称', save: '保存新版本', cancel: '关闭', saved: '新地图已保存', dirty: '参数已改变，请重新生成预览后保存', stats: '{w} × {h} 格 · {res} m · {sec} s', occupied: '占据 {n}%', free: '自由 {n}%', unknown: '未知 {n}%', failed: '投影失败', advanced: '其他参数', unavailable: '原地图没有 2D 栅格', frame: '高度相对于地面模型；“不估计”模式使用原坐标系绝对 z。调平暂不开放，避免 2D 与 3D / 导航坐标系错位。' } },
  en: { rg: { title: 'Regenerate 2D from the 3D cloud', desc: 'Keeps the cloud frame unchanged, without levelling or rotation. Preview, then save a new version; the source stays unchanged.', res: 'Resolution (m)', low: 'Minimum obstacle height (m)', high: 'Maximum obstacle height (m)', mode: 'Ground model', plane: 'Global plane', grid: 'Tiled planes', none: 'None (absolute z)', heightmap: 'Heightmap (experimental)', tol: 'Ground tolerance (m)', points: 'Minimum obstacle points/cell', tile: 'Tile size (m)', voxel: 'Downsample voxel (m, 0 off)', inflate: 'Obstacle inflation (m, 0 off)', closing: 'Free-space closing iterations', exp: 'Enable experimental features (off by default)', risk: 'Heightmap and component filters are unreleased. They can remove thin obstacles or misclassify ground; inspect the saved map.', cell: 'Ground cell size (m)', smooth: 'Ground smoothing (m)', obsArea: 'Remove obstacle regions below (m²)', freeArea: 'Remove free regions below (m²)', tip: { res: 'Edge length of one grid cell. Smaller cells give more detail, but the map gets larger, projection slower, and sparse areas show more holes. Nav2 commonly uses 0.05 m.', low: 'Points at least this high above the ground count as obstacles. Too low turns floor bumps and noise into obstacles; too high misses thresholds and low boxes.', high: 'Points up to this height above the ground count as obstacles. Set it slightly above the robot height; higher points (ceiling, lamps, beams) are ignored so the robot can pass underneath.', mode: 'What obstacle heights are measured from. Use Global plane on a flat floor, Tiled planes for gentle slopes or a slightly bent cloud, and None only when the cloud is already level with the floor near z = 0.', plane: 'Fits one ground plane to the whole cloud, which also corrects a slight tilt. Suits a flat single-storey indoor floor.', grid: 'Splits the map into squares of Tile size and fits a ground plane in each; squares with too few ground points fall back to the global plane. Suits gentle slopes or uneven floors.', none: 'No ground estimation; the raw cloud z is used as height. Use only when the cloud is level and the floor is at z = 0, otherwise all obstacle heights are shifted.', tol: 'Points within this distance of the ground count as ground, and their cells become free. Too small leaves noisy floor as unknown; too large counts the bottom of low obstacles as ground.', points: 'A cell needs at least this many points in the obstacle height band to become occupied. Raising it suppresses scattered noise, but sparsely scanned thin poles may disappear.', tile: 'Square size in Tiled planes mode; used only in that mode. Smaller tiles follow the floor more closely, but each holds fewer points and the fit becomes less stable.', voxel: 'Voxel downsampling before projection, keeping one point per voxel; speeds up large clouds. If set larger than the resolution, Minimum obstacle points/cell becomes harder to reach. 0 disables it.', inflate: 'Grows obstacles by this radius in the saved map. The Nav2 costmap already inflates obstacles, so this is usually 0; use it only when the static map itself should be more conservative.', closing: 'Number of morphological closing passes on free space, filling small holes and gaps between ground points. More passes fill more, but can also fill narrow gaps or doorways as free.', exp: 'Turning this on offers the Heightmap ground model and shows the small-region filters. These are unreleased; turning it off restores the default behaviour.', heightmap: 'Splits the floor into cells, takes the lowest point of each cell as ground height, then fills holes, applies a median filter and Gaussian smoothing to get a continuous ground heightmap. Obstacle heights are measured from it. Suits a single storey with slopes or uneven floor. Not for multiple storeys; large areas without floor returns (e.g. under a desk) can make the desk top count as ground.', cell: 'Edge length of each heightmap cell; used only in Heightmap mode. Smaller cells follow the floor more closely but hold fewer points and react to noise; larger cells are steadier but flatten small slopes. Default 0.5 m.', smooth: 'Scale of the Gaussian smoothing applied to the heightmap; used only in Heightmap mode. Larger values give a flatter floor and suppress local noise, but also flatten real steps and slope edges. 0 disables smoothing. Default 0.5 m.', obsArea: 'Isolated obstacle regions (4-connected) smaller than this are removed to clean up noise. Removed cells become unknown, never free. Real thin obstacles such as table legs or poles can be removed too. Runs before inflation. 0 disables it.', freeArea: 'Isolated free regions smaller than this become unknown, which removes scattered free cells behind walls or in gaps between obstacles. Runs before inflation. 0 disables it.' }, preview: 'Generate preview', generating: 'Projecting…', result: 'New grid', original: 'Original grid', noPreview: 'Adjust parameters and generate a preview', name: 'New version name', save: 'Save new version', cancel: 'Close', saved: 'New map saved', dirty: 'Parameters changed; regenerate the preview before saving', stats: '{w} × {h} cells · {res} m · {sec} s', occupied: 'Occupied {n}%', free: 'Free {n}%', unknown: 'Unknown {n}%', failed: 'Projection failed', advanced: 'Other parameters', unavailable: 'The source has no 2D grid', frame: 'Heights are relative to the ground model; None uses absolute cloud z. Levelling is disabled to keep the grid, cloud and navigation frames aligned.' } },
} })
const open = computed({ get: () => props.modelValue, set: (v) => { if (!busy.value) emit('update:modelValue', v) } })
const params = reactive({ res: 0.05, obs_min: 0.1, obs_max: 0.7, ground_mode: 'plane', ground_tol: 0.05,
  min_points: 2, tile: 5, voxel: 0, inflate: 0, closing_iters: 2, experimental: false,
  ground_cell: 0.5, ground_smooth: 0.5, min_obstacle_area: 0, min_free_area: 0 })
const fields = ['res', 'obs_min', 'obs_max'] as const
const labels = { res: 'res', obs_min: 'low', obs_max: 'high', ground_tol: 'tol', min_points: 'points', tile: 'tile', voxel: 'voxel', inflate: 'inflate', closing_iters: 'closing', ground_cell: 'cell', ground_smooth: 'smooth', min_obstacle_area: 'obsArea', min_free_area: 'freeArea' } as const
const ranges = { res: [0.01,1,0.01], obs_min: [-100,100,0.05], obs_max: [-100,100,0.05], ground_tol: [0.001,1,0.01], min_points: [1,100,1], tile: [0.5,50,0.5], voxel: [0,0.5,0.01], inflate: [0,2,0.05], closing_iters: [0,5,1], ground_cell: [0.1,5,0.1], ground_smooth: [0,5,0.1], min_obstacle_area: [0,10,0.01], min_free_area: [0,10,0.01] } as const
function payload() {
  const { ground_cell, ground_smooth, min_obstacle_area, min_free_area, ...base } = params
  return params.experimental ? { ...base, ground_cell, ground_smooth, min_obstacle_area, min_free_area } : base
}
watch(() => params.experimental, (v) => { if (!v && params.ground_mode === 'heightmap') params.ground_mode = 'plane' })
const signature = computed(() => JSON.stringify(payload()))
const generatedSignature = ref('')
const dirty = computed(() => signature.value !== generatedSignature.value)
const result = shallowRef<RegridPreview | null>(null)
const bmp = shallowRef<ImageBitmap | null>(null)
const oldBmp = shallowRef<ImageBitmap | null>(null)
const showOriginal = ref(false)
const busy = ref(false)
const error = ref('')
const name = ref('')
let abort = new AbortController()
function clear() { if (result.value) void mapsApi.discardPreview(result.value.token).catch(() => {}); abort.abort(); abort = new AbortController(); bmp.value?.close(); oldBmp.value?.close(); bmp.value = oldBmp.value = null; result.value = null }
watch(() => props.modelValue, async (v) => {
  if (!v) { clear(); return }
  clear(); params.experimental = false; params.ground_mode = 'plane'; error.value = ''; showOriginal.value = false
  name.value = (props.map?.name.slice(0, 50) ?? 'map') + '_g_' + Date.now().toString().slice(-6)
  const ctrl = abort
  if (props.map?.has_grid) {
    try { const image = await mapsApi.grid(props.map.name, ctrl.signal); if (ctrl.signal.aborted) image.close(); else oldBmp.value = image } catch { /* Original is optional. */ }
  }
})
onBeforeUnmount(clear)
const displayImage = computed(() => showOriginal.value ? oldBmp.value : bmp.value)
const displayMap = computed<NavMap | null>(() => {
  const grid = showOriginal.value ? props.map?.grid : result.value?.grid
  return grid && props.map ? { name: (showOriginal.value ? 'old_' : 'preview_') + (result.value?.token ?? props.map.name), grid,
    has_cloud: true, has_solid: false, preset: props.map.preset } : null
})
async function preview() {
  if (!props.map) return
  busy.value = true; error.value = ''; const sig = signature.value
  try {
    if (result.value) {
      const token = result.value.token
      result.value = null; bmp.value?.close(); bmp.value = null
      await mapsApi.discardPreview(token).catch(() => {})
    }
    const r = await mapsApi.preview(props.map.name, payload())
    result.value = r
    const res = await fetch(`/api/maps/regrid/${r.token}/grid.png`, { signal: abort.signal })
    if (!res.ok) throw new Error(String(res.status))
    const image = await createImageBitmap(await res.blob())
    bmp.value?.close(); bmp.value = image; result.value = r; generatedSignature.value = sig; showOriginal.value = false
  } catch (e) { error.value = errorText(e) } finally { busy.value = false }
}
async function save() {
  if (!result.value || dirty.value) return
  busy.value = true; error.value = ''
  try { const d = await mapsApi.savePreview(result.value.token, name.value); result.value = null; ElMessage.success(t('rg.saved')); emit('saved', d.name); emit('update:modelValue', false) }
  catch (e) { error.value = errorText(e) } finally { busy.value = false }
}
</script>
<template>
  <el-dialog v-model="open" :title="t('rg.title')" width="min(1200px, 96vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" destroy-on-close>
    <p>{{ t('rg.desc') }}</p>
    <div class="layout">
      <el-form label-position="top" class="controls" :disabled="busy">
        <el-form-item v-for="key in fields" :key="key">
          <template #label>{{ t(`rg.${labels[key]}`) }}<InfoTip :content="t(`rg.tip.${labels[key]}`)" /></template>
          <el-input-number v-model="params[key]" :min="ranges[key][0]" :max="ranges[key][1]" :step="ranges[key][2]" />
        </el-form-item>
        <el-form-item><template #label>{{ t('rg.mode') }}<InfoTip :content="t('rg.tip.mode')" /></template><el-select v-model="params.ground_mode">
          <el-option v-for="mode in ['plane','grid','none', ...(params.experimental ? ['heightmap'] : [])]" :key="mode" :label="t(`rg.${mode}`)" :value="mode">
            <span>{{ t(`rg.${mode}`) }}</span><InfoTip :content="t(`rg.tip.${mode}`)" placement="right" />
          </el-option>
        </el-select></el-form-item>
        <p class="muted">{{ t('rg.frame') }}</p>
        <details><summary>{{ t('rg.advanced') }}</summary>
          <el-form-item v-for="key in (['ground_tol','min_points','tile','voxel','inflate','closing_iters'] as const)" :key="key">
            <template #label>{{ t(`rg.${labels[key]}`) }}<InfoTip :content="t(`rg.tip.${labels[key]}`)" /></template>
            <el-input-number v-model="params[key]" :min="ranges[key][0]" :max="ranges[key][1]" :step="ranges[key][2]" />
          </el-form-item>
        </details>
        <div class="exp-row"><el-switch v-model="params.experimental" :active-text="t('rg.exp')" /><InfoTip :content="t('rg.tip.exp')" /></div>
        <template v-if="params.experimental"><p class="warning">{{ t('rg.risk') }}</p>
          <el-form-item v-for="key in (['ground_cell','ground_smooth','min_obstacle_area','min_free_area'] as const)" :key="key">
            <template #label>{{ t(`rg.${labels[key]}`) }}<InfoTip :content="t(`rg.tip.${labels[key]}`)" /></template>
            <el-input-number v-model="params[key]" :min="ranges[key][0]" :max="ranges[key][1]" :step="ranges[key][2]" />
          </el-form-item>
        </template>
        <el-button type="primary" :loading="busy" :disabled="!map?.has_cloud" @click="preview">{{ busy ? t('rg.generating') : t('rg.preview') }}</el-button>
      </el-form>
      <div class="output">
        <el-radio-group v-model="showOriginal"><el-radio-button :value="false">{{ t('rg.result') }}</el-radio-button><el-radio-button :value="true">{{ t('rg.original') }}</el-radio-button></el-radio-group>
        <div class="views"><div class="viewport"><NavGrid2D :map="displayMap" :image="displayImage" :status="null" tool="pan" />
          <div v-if="!displayImage" class="empty">{{ showOriginal ? t('rg.unavailable') : t('rg.noPreview') }}</div></div>
          <div class="viewport"><NavScene3D :map="displayMap" :image="displayImage" :cloud="cloud" :solid="null" model="cloud" :status="null" :show-cloud="true" :show-grid="true" /></div>
        </div>
        <template v-if="result"><p>{{ t('rg.stats', { w:result.grid.width, h:result.grid.height, res:result.grid.resolution, sec:result.elapsed }) }}</p>
          <p>{{ t('rg.occupied', { n:(result.stats.occupied*100).toFixed(1) }) }} · {{ t('rg.free', { n:(result.stats.free*100).toFixed(1) }) }} · {{ t('rg.unknown', { n:((1-result.stats.occupied-result.stats.free)*100).toFixed(1) }) }}</p>
          <p class="muted">{{ result.info }}</p><p v-for="w in result.warnings" :key="w" class="warning">{{ w }}</p>
        </template>
        <el-alert v-if="dirty && result" :title="t('rg.dirty')" type="warning" :closable="false" />
        <p v-if="error" class="warning">{{ error }}</p>
        <el-form-item :label="t('rg.name')"><el-input v-model="name" :disabled="busy" maxlength="64" /></el-form-item>
      </div>
    </div>
    <template #footer><el-button :disabled="busy" @click="open=false">{{ t('rg.cancel') }}</el-button><el-button type="primary" :loading="busy" :disabled="!result || dirty || !/^[A-Za-z0-9_-]{1,64}$/.test(name)" @click="save">{{ t('rg.save') }}</el-button></template>
  </el-dialog>
</template>
<style scoped>
.layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:24px}.controls{max-height:65vh;overflow:auto;padding-right:12px}.controls .el-input-number{width:100%}.controls .el-form-item{margin-bottom:12px}.controls .el-switch{height:auto;margin:16px 0}.controls :deep(.el-switch__label){white-space:normal}.output{min-width:0}.views{display:grid;grid-template-columns:1fr 1fr;gap:10px;height:390px;margin-top:12px}.viewport{position:relative;background:var(--glass);border:1px solid var(--hairline);border-radius:12px;overflow:hidden}.empty{position:absolute;inset:0;display:grid;place-items:center;padding:20px;pointer-events:none}.muted{color:var(--ink-3);font-size:12px;line-height:1.6}.warning{color:var(--warn);overflow-wrap:anywhere}summary{cursor:pointer;margin:14px 0}.output p{font-size:13px}.exp-row{display:flex;align-items:center;gap:2px}@media(max-width:800px){.layout{grid-template-columns:1fr}.controls{max-height:300px}.views{grid-template-columns:1fr;height:600px}}
</style>