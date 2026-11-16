<!-- SLAM step of the Mapping and Playback pages: parameters of the selected SLAM (robot_console/slam_config.py).
     The console builds the SLAM's parameter file in layers: validated base -> data source -> option switches ->
     the user's values. The table lists the parameters a user may change with the base value, the automatic value
     (and the layer it came from) and the user's value; the rest is shown read only. Values are stored per SLAM
     variant and data source (the simulation, or the sensor profile of the bag) and apply at the next start.
     Layout: layer strip (how many parameters each layer sets) -> validation record -> measured sensor facts ->
     tunable table (each numeric row has a range bar with the automatic value marked) -> read-only list. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import InfoTip from '@/components/InfoTip.vue'
import type { SlamConfigInfo, SlamParamRow, SlamParamValue } from '@/mapping/slamConfig'

const props = defineProps<{
  modelValue: boolean
  /** stacks.yaml name of the selected SLAM */
  stack: string | null
  /** Playback page: path of the selected bag (its sensor profile decides the data source) */
  bag?: string | null
  /** the SLAM is running: changes wait for the next start */
  running?: boolean
  disabled?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; saved: [] }>()
const { t } = useI18n({
  messages: {
    en: {
      sc: {
        title: 'Parameters: {algorithm}',
        desc: 'The console writes the parameter file of the SLAM at every start: the validated base values, then the values that follow from the data source and the switches, then your changes. Empty a field or press the arrow to go back to the automatic value.',
        loading: 'Reading the configuration…',
        scopeSim: 'Your values are stored for this algorithm variant on the simulated robot (also bags recorded from the simulation).',
        scopeSensor: 'Your values are stored for this algorithm variant on the sensor profile "{sensor}"; other robots keep their own.',
        layersLead: 'Built in layers',
        layer: { base: 'Base', source: 'Data source', option: 'Switches', yours: 'Your changes' },
        validatedData: 'Validated on', validatedResult: 'Result',
        notValidated: 'This combination of data source and switches has no validation record.',
        modified: '{n} value changed: the validation result above no longer describes this configuration. | {n} values changed: the validation result above no longer describes this configuration.',
        col: { param: 'Parameter', base: 'Base', auto: 'Automatic', yours: 'Your value' },
        src: { input: 'data source', derived: 'from the sensor', option: 'switch', console: 'console' },
        factsFrom: 'Measured from the bag {bag}',
        fact: { rings: '{n} rings', scan: '{n} scans/s', points: '{n} points per scan', imu: 'IMU {n} Hz' },
        suggest: 'suggested {value}',
        suggestTip: {
          rings: 'One entry per ring number of the cloud. Not in force because a switch above sets this parameter.',
          scan_rate: 'The measured scan rate. Not in force because another layer sets this parameter.',
          decimate: 'Leaves about as many points per scan as the validated simulation setup. Not in force because another layer sets this parameter. Click to use it.',
          ring_neighbors: 'Chosen from the lidar ring count (30 for sparse lidars up to 24 rings, 20 for denser ones). Not in force because another layer sets this parameter. Click to use it.',
        },
        on: 'on', off: 'off',
        autoMark: 'Automatic value: {value}',
        reset: 'Back to the automatic value',
        resetAll: 'Reset all',
        others: 'Other parameters (read only, {n})',
        bad: { number: 'Enter a number', int: 'Enter a whole number', range: 'Allowed: {min} … {max}' },
        applies: 'Changes apply the next time the SLAM starts.',
        appliesRunning: 'The SLAM is running: changes apply after you stop it and start it again.',
        save: 'Save', cancel: 'Close',
        saved: 'Parameters saved ({n} changed)', savedNone: 'Parameters back to the automatic values',
      },
    },
    'zh-CN': {
      sc: {
        title: '参数：{algorithm}',
        desc: '每次启动 SLAM 时，控制台都会生成它的参数文件：先用验证过的基准值，再按数据来源和开关自动取值，最后套上你的改动。清空输入框或点箭头可以回到自动值。',
        loading: '正在读取配置…',
        scopeSim: '你的改动只用于这个算法变体在仿真小车上的运行（也包括仿真录的数据包）。',
        scopeSensor: '你的改动只用于这个算法变体在传感器配置“{sensor}”上的运行，其他机器人各存各的。',
        layersLead: '按层生成',
        layer: { base: '基准值', source: '数据来源', option: '开关', yours: '你的改动' },
        validatedData: '验证数据', validatedResult: '结果',
        notValidated: '这个“数据来源 + 开关”的组合没有验证记录。',
        modified: '已改 {n} 项：上面的验证结果不再适用于这份配置。',
        col: { param: '参数', base: '基准值', auto: '自动值', yours: '你的值' },
        src: { input: '数据来源', derived: '按传感器推导', option: '开关', console: '控制台' },
        factsFrom: '从数据包 {bag} 实测',
        fact: { rings: '{n} 线', scan: '每秒 {n} 帧', points: '每帧 {n} 点', imu: 'IMU {n} Hz' },
        suggest: '建议 {value}',
        suggestTip: {
          rings: '按点云里的线号个数取值。没有生效，因为上面的开关设置了这个参数。',
          scan_rate: '实测的帧率。没有生效，因为另一层设置了这个参数。',
          decimate: '按这个值，每帧留下的点数和验证过的仿真配置差不多。没有生效，因为另一层设置了这个参数。点一下可以填入。',
          ring_neighbors: '按实测雷达线数取值（24 线及以下取 30，更多线取 20）。没有生效，因为另一层设置了这个参数。点一下可以填入。',
        },
        on: '开', off: '关',
        autoMark: '自动值：{value}',
        reset: '回到自动值',
        resetAll: '全部恢复',
        others: '其余参数（只读，{n} 项）',
        bad: { number: '请输入数字', int: '请输入整数', range: '允许范围：{min} … {max}' },
        applies: '改动在下次启动 SLAM 时生效。',
        appliesRunning: 'SLAM 正在运行：停止并重新启动后改动才生效。',
        save: '保存', cancel: '关闭',
        saved: '参数已保存（改动 {n} 项）', savedNone: '参数已恢复为自动值',
      },
    },
  },
})

const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => { if (!busy.value) emit('update:modelValue', v) },
})
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const info = ref<SlamConfigInfo | null>(null)
type RequestContext = { sequence: number; stack: string; bag: string | null }
let sequence = 0
let loaded: RequestContext | null = null
function isCurrent(context: RequestContext) {
  return context.sequence === sequence && props.modelValue && props.stack === context.stack
    && (props.bag ?? null) === context.bag
}
// What the user typed, per tunable key: text for numbers, boolean for switches
const text = ref<Record<string, string>>({})
const flag = ref<Record<string, boolean>>({})

const tunable = computed(() => (info.value?.params ?? []).filter((r) => r.tunable))
const others = computed(() => (info.value?.params ?? []).filter((r) => !r.tunable))

const num = (v: number) => String(+v.toPrecision(10))
function fmt(v: SlamParamValue): string {
  if (typeof v === 'boolean') return v ? t('sc.on') : t('sc.off')
  if (Array.isArray(v)) return `[${v.map(fmt).join(', ')}]`
  return typeof v === 'number' ? num(v) : v === '' ? '—' : String(v)
}
const sourceKind = (s: string) => s.split(':')[0]
const sourceLabel = (s: string) => (s === 'base' ? '' : t(`sc.src.${sourceKind(s)}`))
const factItems = computed(() => {
  const f = info.value?.facts
  if (!f) return []
  return [
    f.rings != null ? t('sc.fact.rings', { n: f.rings }) : '',
    f.scan_rate != null ? t('sc.fact.scan', { n: num(f.scan_rate) }) : '',
    f.points_per_scan != null ? t('sc.fact.points', { n: f.points_per_scan }) : '',
    f.imu_rate != null ? t('sc.fact.imu', { n: num(f.imu_rate) }) : '',
  ].filter(Boolean)
})
/** A dotted parameter key as [path with trailing dot, last segment]; the last segment is what the eye looks for */
function keyParts(key: string): [string, string] {
  const i = key.lastIndexOf('.')
  return [key.slice(0, i + 1), key.slice(i + 1)]
}
/** Put a rule's suggested value into the row's field */
function useSuggestion(r: SlamParamRow) {
  if (!r.tunable || !r.suggest || r.tunable.type === 'bool') return
  text.value[r.key] = num(r.suggest.value as number)
}

function fill(i: SlamConfigInfo) {
  info.value = i
  text.value = {}
  flag.value = {}
  for (const r of i.params) {
    if (!r.tunable) continue
    if (r.tunable.type === 'bool') flag.value[r.key] = r.value as boolean
    else text.value[r.key] = num(r.value as number)
  }
}
async function load() {
  const requestSequence = ++sequence
  loaded = null
  info.value = null
  error.value = ''
  loading.value = false
  busy.value = false
  const s = props.stack
  if (!props.modelValue || !s) return
  const context: RequestContext = { sequence: requestSequence, stack: s, bag: props.bag ?? null }
  loading.value = true
  try {
    const i = await api.slamConfig(context.stack, context.bag)
    if (isCurrent(context)) {
      loaded = context
      fill(i)
    }
  } catch (e) {
    if (isCurrent(context)) error.value = errorText(e)
  } finally {
    if (isCurrent(context)) loading.value = false
  }
}
watch([() => props.modelValue, () => props.stack, () => props.bag ?? null], () => void load(),
  { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { ++sequence; loaded = null })

/** The value of a row as entered: undefined when the field holds no valid value of the row's type. */
function entered(r: SlamParamRow): number | boolean | undefined {
  const tn = r.tunable
  if (!tn) return undefined
  if (tn.type === 'bool') return flag.value[r.key]
  const s = (text.value[r.key] ?? '').trim()
  if (s === '') return r.auto as number
  const v = Number(s)
  if (!Number.isFinite(v)) return undefined
  if (tn.type === 'int' && !Number.isInteger(v)) return undefined
  return v
}
function problem(r: SlamParamRow): string {
  const tn = r.tunable
  if (!tn || tn.type === 'bool') return ''
  const v = entered(r)
  if (v === undefined) return t(tn.type === 'int' ? 'sc.bad.int' : 'sc.bad.number')
  if (tn.min !== undefined && tn.max !== undefined && ((v as number) < tn.min || (v as number) > tn.max)) {
    return t('sc.bad.range', { min: num(tn.min), max: num(tn.max) })
  }
  return ''
}
const changed = (r: SlamParamRow) => {
  const v = entered(r)
  return v !== undefined && v !== r.auto
}
const changedCount = computed(() => tunable.value.filter(changed).length)
const invalid = computed(() => tunable.value.some((r) => problem(r) !== ''))
// something differs from what is stored
const dirty = computed(() => tunable.value.some((r) => {
  const v = entered(r)
  return v === undefined || v !== r.value
}))

// Layer strip: how many parameters each layer of the parameter file sets (the last one follows the fields live)
const layers = computed(() => {
  const rows = info.value?.params ?? []
  const count = (...kinds: string[]) => rows.filter((r) => kinds.includes(sourceKind(r.source))).length
  return [
    { id: 'base', n: count('base') },
    { id: 'source', n: count('input', 'derived') },
    { id: 'option', n: count('option') },
    { id: 'yours', n: changedCount.value },
  ]
})

const hasRange = (r: SlamParamRow) => r.tunable?.min !== undefined && r.tunable?.max !== undefined
/** Position (0..1) of a value on the row's range bar, null when it cannot be placed.
 *  Ranges of two decades and more use a log scale, otherwise small values all sit at the left end. */
function position(r: SlamParamRow, v: SlamParamValue | undefined): number | null {
  const tn = r.tunable
  if (!tn || typeof v !== 'number' || tn.min === undefined || tn.max === undefined || tn.max <= tn.min) return null
  const log = tn.min > 0 && tn.max / tn.min >= 100
  const f = log ? Math.log(v / tn.min) / Math.log(tn.max / tn.min) : (v - tn.min) / (tn.max - tn.min)
  return Number.isFinite(f) ? Math.min(1, Math.max(0, f)) : null
}
const percent = (f: number | null) => `${((f ?? 0) * 100).toFixed(1)}%`

function reset(r: SlamParamRow) {
  if (!r.tunable) return
  if (r.tunable.type === 'bool') flag.value[r.key] = r.auto as boolean
  else text.value[r.key] = num(r.auto as number)
}
function resetAll() {
  tunable.value.forEach(reset)
}

async function save() {
  const context = loaded
  if (!context || !isCurrent(context) || !info.value || loading.value || invalid.value || busy.value || props.disabled) return
  const values: Record<string, number | boolean> = {}
  for (const r of tunable.value) {
    const v = entered(r)
    if (v !== undefined && v !== r.auto) values[r.key] = v
  }
  busy.value = true
  error.value = ''
  try {
    const i = await api.saveSlamConfig(context.stack, context.bag, values)
    if (!isCurrent(context)) return
    fill(i)
    ElMessage.success(i.user_count ? t('sc.saved', { n: i.user_count }) : t('sc.savedNone'))
    emit('saved')
    busy.value = false
    open.value = false
  } catch (e) {
    if (isCurrent(context)) error.value = errorText(e)
  } finally {
    if (isCurrent(context)) busy.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="open" class="import-dialog slam-config"
    width="min(900px, calc(100vw - 32px))" append-to-body destroy-on-close
    :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy"
  >
    <template #header="{ titleId, titleClass }">
      <div class="head">
        <i class="tick" />
        <div class="head-text">
          <h2 :id="titleId" :class="titleClass">{{ t('sc.title', { algorithm: info?.algorithm ?? '' }) }}</h2>
          <p v-if="info" class="scope">
            {{ info.sensor ? t('sc.scopeSensor', { sensor: info.sensor }) : t('sc.scopeSim') }}
          </p>
        </div>
      </div>
    </template>

    <p v-if="loading" class="import-note" role="status">{{ t('sc.loading') }}</p>
    <template v-if="info">
      <!-- Layers of the parameter file, in the order they are applied -->
      <div class="layers">
        <span class="lead">{{ t('sc.layersLead') }}</span>
        <template v-for="(l, i) in layers" :key="l.id">
          <svg v-if="i" class="arrow" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" /></svg>
          <span class="layer" :class="[l.id, { empty: !l.n }]">
            <i class="dot" />{{ t(`sc.layer.${l.id}`) }}<b class="mono">{{ l.n }}</b>
          </span>
        </template>
        <InfoTip :content="t('sc.desc')" placement="bottom" />
      </div>

      <!-- Validation record of this data source + switches; a warning line once a value differs from it -->
      <div class="card" :class="info.validated ? 'ok' : 'warn'">
        <dl v-if="info.validated" class="validated">
          <dt>{{ t('sc.validatedData') }}</dt>
          <dd>{{ pick(info.validated.data) }}</dd>
          <dt>{{ t('sc.validatedResult') }}</dt>
          <dd>{{ pick(info.validated.result) }}</dd>
        </dl>
        <p v-else class="card-line" role="status">{{ t('sc.notValidated') }}</p>
        <p v-if="changedCount" class="card-line modified" role="status">
          {{ t('sc.modified', { n: changedCount }, changedCount) }}
        </p>
      </div>

      <div v-if="info.facts && factItems.length" class="facts">
        <span class="lead">{{ t('sc.factsFrom', { bag: info.facts.bag }) }}</span>
        <span v-for="item in factItems" :key="item" class="fact mono">{{ item }}</span>
      </div>

      <div class="table-wrap">
        <table class="params">
          <thead>
            <tr>
              <th>{{ t('sc.col.param') }}</th>
              <th class="r">{{ t('sc.col.base') }}</th>
              <th class="r">{{ t('sc.col.auto') }}</th>
              <th>{{ t('sc.col.yours') }}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in tunable" :key="r.key" :class="{ changed: changed(r), bad: !!problem(r) }">
              <td class="c-name">
                <span class="name">{{ pick(r.tunable!.label) }}<InfoTip :content="pick(r.tunable!.help)" /></span>
                <span class="key mono"><span class="path">{{ keyParts(r.key)[0] }}</span>{{ keyParts(r.key)[1] }}</span>
              </td>
              <td class="r mono base">{{ fmt(r.base) }}</td>
              <td class="r mono c-auto">
                {{ fmt(r.auto) }}
                <span v-if="r.source !== 'base'" class="src" :class="sourceKind(r.source)">{{ sourceLabel(r.source) }}</span>
                <button
                  v-if="r.suggest" type="button" class="suggest" :disabled="busy || disabled"
                  :title="t(`sc.suggestTip.${r.suggest.rule}`)" @click="useSuggestion(r)"
                >{{ t('sc.suggest', { value: fmt(r.suggest.value) }) }}</button>
              </td>
              <td class="c-yours">
                <el-switch
                  v-if="r.tunable!.type === 'bool'" v-model="flag[r.key]" size="small" :disabled="busy || disabled"
                  :aria-label="pick(r.tunable!.label)"
                />
                <template v-else>
                  <el-input
                    v-model="text[r.key]" size="small" class="val mono" :class="{ bad: !!problem(r) }"
                    :disabled="busy || disabled" :placeholder="fmt(r.auto)" :aria-label="pick(r.tunable!.label)"
                    inputmode="decimal" @keyup.enter="save"
                  />
                  <!-- Range bar: fill and knob at the entered value, tick at the automatic value -->
                  <div v-if="hasRange(r)" class="range">
                    <span class="end mono">{{ num(r.tunable!.min!) }}</span>
                    <span class="bar">
                      <template v-if="position(r, entered(r)) !== null">
                        <i class="fill" :style="{ width: percent(position(r, entered(r))) }" />
                        <i class="knob" :style="{ left: percent(position(r, entered(r))) }" />
                      </template>
                      <i
                        v-if="position(r, r.auto) !== null" class="auto-mark"
                        :style="{ left: percent(position(r, r.auto)) }" :title="t('sc.autoMark', { value: fmt(r.auto) })"
                      />
                    </span>
                    <span class="end mono">{{ num(r.tunable!.max!) }}</span>
                  </div>
                  <span v-if="problem(r)" class="problem" role="alert">{{ problem(r) }}</span>
                </template>
              </td>
              <td class="c-reset">
                <button
                  v-if="changed(r) || !!problem(r)" type="button" class="reset" :disabled="busy || disabled"
                  :title="t('sc.reset')" :aria-label="t('sc.reset')" @click="reset(r)"
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /></svg>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <el-collapse class="others">
        <el-collapse-item :title="t('sc.others', { n: others.length })" name="others">
          <dl class="ro">
            <template v-for="r in others" :key="r.key">
              <dt class="mono"><span class="path">{{ keyParts(r.key)[0] }}</span>{{ keyParts(r.key)[1] }}</dt>
              <dd class="mono">
                {{ fmt(r.value) }}
                <span v-if="r.source !== 'base'" class="src" :class="sourceKind(r.source)">{{ sourceLabel(r.source) }}</span>
                <span v-if="r.suggest" class="src hint" :title="t(`sc.suggestTip.${r.suggest.rule}`)">
                  {{ t('sc.suggest', { value: fmt(r.suggest.value) }) }}
                </span>
              </dd>
            </template>
          </dl>
        </el-collapse-item>
      </el-collapse>
    </template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />

    <template #footer>
      <span v-if="info" class="applies" :class="{ running }">
        <i class="dot" />{{ running ? t('sc.appliesRunning') : t('sc.applies') }}
      </span>
      <el-button v-if="info" :disabled="busy || disabled || !changedCount" @click="resetAll">{{ t('sc.resetAll') }}</el-button>
      <el-button :disabled="busy" @click="open = false">{{ t('sc.cancel') }}</el-button>
      <el-button type="primary" :loading="busy" :disabled="loading || !info || disabled || invalid || !dirty" @click="save">
        {{ t('sc.save') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
/* One colour per layer of the parameter file; the strip, the source tags and the changed rows share them */
.layers,
.params,
.ro {
  --layer-base: var(--idle);
  --layer-source: var(--accent);
  --layer-derived: var(--accent-2);
  --layer-option: #9333ea;
}
html.dark .layers,
html.dark .params,
html.dark .ro {
  --layer-option: #c084fc;
}

/* ---- header ---- */
.head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
  padding-right: 28px;
}
.tick {
  flex: none;
  width: 3px;
  height: 16px;
  margin-top: 4px;
  border-radius: 2px;
  background: var(--accent-grad);
  box-shadow: 0 0 8px rgba(6, 182, 212, 0.6);
}
.head-text {
  min-width: 0;
}
.head h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  line-height: 24px;
  letter-spacing: 0.02em;
  color: var(--ink);
}
.scope {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}

/* ---- layer strip ---- */
.layers {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}
.lead {
  margin-right: 4px;
  font-size: 12px;
  color: var(--ink-3);
}
.arrow {
  flex: none;
  width: 12px;
  height: 12px;
  fill: none;
  stroke: var(--ink-3);
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
  opacity: 0.7;
}
.layer {
  --c: var(--layer-base);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 10px;
  border-radius: 8px;
  font-size: 12px;
  white-space: nowrap;
  color: var(--ink-2);
  background: rgba(var(--paper-rgb), 0.7);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.layer.source { --c: var(--layer-source); }
.layer.option { --c: var(--layer-option); }
.layer.yours { --c: var(--accent); }
.layer .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--c);
}
.layer b {
  font-weight: 600;
  color: var(--ink);
}
.layer.empty b {
  font-weight: 400;
  color: var(--ink-3);
}
.layer.yours:not(.empty) {
  color: color-mix(in srgb, var(--accent) 80%, var(--deepen));
  background: color-mix(in srgb, var(--accent) 9%, var(--paper));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 34%, transparent);
}
.layer.yours:not(.empty) b {
  color: inherit;
}

/* ---- validation card ---- */
.card {
  --c: var(--accent);
  position: relative;
  margin-bottom: 10px;
  padding: 9px 12px 9px 15px;
  border-radius: 10px;
  font-size: 12.5px;
  line-height: 1.6;
  background: color-mix(in srgb, var(--c) 5%, rgba(var(--paper-rgb), 0.72));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--c) 16%, transparent);
}
.card::before {
  content: '';
  position: absolute;
  top: 9px;
  bottom: 9px;
  left: 6px;
  width: 3px;
  border-radius: 2px;
  background: var(--c);
}
.card.warn { --c: var(--warn); }
.validated {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 2px 12px;
  margin: 0;
}
.validated dt {
  color: var(--ink-3);
}
.validated dd {
  margin: 0;
  color: var(--ink);
}
.card-line {
  margin: 0;
  color: color-mix(in srgb, var(--warn) 78%, var(--deepen));
}
.card-line.modified:not(:first-child) {
  margin-top: 7px;
  padding-top: 7px;
  border-top: 1px dashed color-mix(in srgb, var(--warn) 34%, transparent);
}

/* ---- measured sensor facts ---- */
.facts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}
.facts .lead {
  min-width: 0;
  overflow-wrap: anywhere;
}
.fact {
  height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  font-size: 11.5px;
  line-height: 22px;
  white-space: nowrap;
  color: var(--ink-2);
  background: rgba(var(--tint-rgb), 0.045);
}

/* ---- tunable parameters ---- */
.table-wrap {
  overflow-x: auto;
  margin-bottom: 10px;
  border-radius: 12px;
  background: rgba(var(--paper-rgb), 0.62);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.params {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.params th {
  padding: 9px 10px 7px;
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-align: left;
  text-transform: uppercase;
  white-space: nowrap;
  color: var(--ink-3);
  border-bottom: 1px solid var(--hairline);
}
.params td {
  padding: 9px 10px;
  vertical-align: top;
  border-bottom: 1px solid var(--hairline);
  transition: background-color 0.15s;
}
.params tbody tr:last-child td {
  border-bottom: 0;
}
.params th:first-child,
.params td:first-child {
  padding-left: 16px;
}
.params tbody tr:hover td {
  background: rgba(var(--accent-rgb), 0.035);
}
.params tr.changed td {
  background: color-mix(in srgb, var(--accent) 6%, transparent);
}
.params tr.changed td:first-child {
  box-shadow: inset 3px 0 0 var(--accent);
}
.params tr.bad td:first-child {
  box-shadow: inset 3px 0 0 var(--bad);
}
.params .r {
  text-align: right;
  white-space: nowrap;
}
.c-name {
  max-width: 300px;
}
.name {
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
  color: var(--ink);
}
.key {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  line-height: 1.45;
  overflow-wrap: anywhere;
  color: var(--ink-2);
}
.path {
  color: var(--ink-3);
  opacity: 0.75;
}
.base {
  color: var(--ink-3);
}
.c-auto {
  color: var(--ink);
}
.params td.r {
  line-height: 24px;
}

/* Source tag of an automatic value: which layer set it */
.src {
  --c: var(--layer-source);
  display: block;
  width: fit-content;
  margin-left: auto;
  padding: 0 6px;
  border-radius: 4px;
  font-family: var(--el-font-family);
  font-size: 11px;
  line-height: 17px;
  white-space: nowrap;
  color: color-mix(in srgb, var(--c) 82%, var(--deepen));
  background: color-mix(in srgb, var(--c) 10%, transparent);
}
.src.derived { --c: var(--layer-derived); }
.src.option { --c: var(--layer-option); }
.src.console { --c: var(--layer-base); color: var(--ink-2); }
.src.hint { --c: var(--warn); }
.suggest {
  display: block;
  width: fit-content;
  margin: 3px 0 0 auto;
  padding: 0 6px;
  border: 0;
  border-radius: 4px;
  font: inherit;
  font-family: var(--el-font-family);
  font-size: 11px;
  line-height: 17px;
  white-space: nowrap;
  color: color-mix(in srgb, var(--warn) 82%, var(--deepen));
  background: none;
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--warn) 40%, transparent);
  cursor: pointer;
  transition: background-color 0.15s;
}
.suggest:hover:not(:disabled),
.suggest:focus-visible {
  background: color-mix(in srgb, var(--warn) 12%, transparent);
  outline: none;
}
.suggest:disabled {
  cursor: default;
  opacity: 0.6;
}

/* Your value: input over a range bar */
.c-yours {
  width: 216px;
  min-width: 184px;
}
.val {
  width: 100%;
}
.val :deep(.el-input__wrapper) {
  background: rgba(var(--paper-rgb), 0.85);
}
.val :deep(.el-input__inner) {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.changed .val :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--accent) inset;
}
.changed .val :deep(.el-input__inner) {
  font-weight: 600;
  color: color-mix(in srgb, var(--accent) 72%, var(--deepen));
}
.val.bad :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--bad) inset;
}
.range {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 6px;
}
.end {
  flex: none;
  font-size: 10.5px;
  line-height: 12px;
  color: var(--ink-3);
}
.bar {
  position: relative;
  flex: 1;
  min-width: 40px;
  height: 4px;
  border-radius: 2px;
  background: rgba(var(--tint-rgb), 0.09);
}
.fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 2px;
  background: var(--idle);
  transition: width 0.18s ease-out;
}
.changed .fill {
  background: var(--accent-grad);
}
.bad .fill {
  background: var(--bad);
}
.auto-mark {
  position: absolute;
  top: -3px;
  width: 2px;
  height: 10px;
  margin-left: -1px;
  border-radius: 1px;
  background: var(--ink-2);
}
.knob {
  position: absolute;
  top: 50%;
  width: 8px;
  height: 8px;
  margin: -4px 0 0 -4px;
  border-radius: 50%;
  background: var(--paper);
  box-shadow: 0 0 0 1.5px var(--idle);
  transition: left 0.18s ease-out;
}
.changed .knob {
  box-shadow: 0 0 0 1.5px var(--accent), 0 0 6px rgba(6, 182, 212, 0.55);
}
.bad .knob {
  box-shadow: 0 0 0 1.5px var(--bad);
}
.problem {
  display: block;
  margin-top: 4px;
  font-size: 11px;
  color: var(--bad);
}
.params td.c-reset {
  width: 30px;
  padding-left: 0;
}
.reset {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  color: var(--ink-3);
  background: none;
  cursor: pointer;
  transition: color 0.15s, background-color 0.15s;
}
.reset svg {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.reset:hover:not(:disabled),
.reset:focus-visible {
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  outline: none;
}
.reset:disabled {
  cursor: default;
  opacity: 0.5;
}

/* ---- read-only parameters ---- */
.others {
  margin-bottom: 4px;
  border: 0;
  --el-collapse-header-height: 36px;
  --el-collapse-header-bg-color: transparent;
  --el-collapse-content-bg-color: transparent;
  --el-collapse-border-color: transparent;
}
.others :deep(.el-collapse-item__header) {
  font-size: 12.5px;
  color: var(--ink-2);
}
.others :deep(.el-collapse-item__content) {
  padding-bottom: 6px;
}
.ro {
  display: grid;
  grid-template-columns: minmax(0, max-content) minmax(0, 1fr);
  margin: 0;
  padding: 4px 14px;
  border-radius: 12px;
  font-size: 12px;
  line-height: 1.5;
  background: rgba(var(--tint-rgb), 0.03);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.ro dt,
.ro dd {
  margin: 0;
  padding: 5px 0;
  overflow-wrap: anywhere;
  border-bottom: 1px solid var(--hairline);
}
.ro dt {
  padding-right: 18px;
  color: var(--ink-2);
}
.ro dd {
  color: var(--ink);
}
.ro dt:nth-last-of-type(1),
.ro dd:nth-last-of-type(1) {
  border-bottom: 0;
}
.ro .src {
  display: inline-block;
  margin-left: 6px;
  vertical-align: 1px;
}

/* ---- footer ---- */
.applies {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  margin-right: auto;
  font-size: 12px;
  line-height: 1.5;
  text-align: left;
  color: var(--ink-3);
}
.applies .dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--idle);
}
.applies.running {
  color: color-mix(in srgb, var(--warn) 80%, var(--deepen));
}
.applies.running .dot {
  background: var(--warn);
}

@media (max-width: 720px) {
  .params th:nth-child(2),
  .params td:nth-child(2) {
    display: none;
  }
}
</style>

<style>
/* The dialog root is rendered by Element Plus, so its HUD corner marks (as on GlassPanel) are not scoped. */
.el-dialog.slam-config::before,
.el-dialog.slam-config::after {
  content: '';
  position: absolute;
  width: 14px;
  height: 14px;
  pointer-events: none;
  border-color: var(--accent);
  border-style: solid;
  opacity: 0.55;
}
.el-dialog.slam-config::before {
  top: 7px;
  left: 7px;
  border-width: 1.5px 0 0 1.5px;
  border-top-left-radius: 6px;
}
.el-dialog.slam-config::after {
  right: 7px;
  bottom: 7px;
  border-width: 0 1.5px 1.5px 0;
  border-bottom-right-radius: 6px;
}
</style>
