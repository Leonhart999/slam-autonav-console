<!-- Playback page: sensor setup of a bag, so a real robot's bag can be played without editing sensor_profiles.yaml.
     Opening the dialog reads the bag's first scans and ~2 s of IMU (robot_console/sensor_profiles.py, bag_probe.py)
     and fills the form with the guesses: cloud / IMU topic, per-point time field (unit, absolute or relative),
     ring / intensity fields, acceleration unit. Extrinsics default to identity and can be pasted.
     Saving writes a profile of the page (checked before the built-in ones); built-in profiles are read only. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { errorText } from '@/messages'
import type { BagInfo, SensorProbe, SensorProfileForm } from '@/mapping/stream'

const props = defineProps<{ modelValue: boolean; bag: BagInfo | null; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; saved: [] }>()
const { t } = useI18n({
  messages: {
    en: {
      sp: {
        title: 'Sensor setup: {bag}',
        desc: 'Tells the console how to read this robot\'s lidar and IMU. The fields are guessed from the first scans and about 2 s of IMU data; check them before saving. Bags with the same topics use the profile too.',
        probing: 'Reading the first messages of the bag…',
        retry: 'Read again',
        matched: 'This bag currently uses the profile "{name}"',
        matchedBuiltin: 'This bag currently uses the built-in profile "{name}" (config/sensor_profiles.yaml, read only). A profile saved here with the same topics is used instead of it.',
        fillGuess: 'Use the detected values',
        fillMatched: 'Use the current profile',
        name: 'Profile name', nameRule: '1–32 letters, digits, underscores or hyphens',
        label: 'Display name (optional)',
        points: 'Point cloud topic', points2: 'Second lidar (optional, merged into the first)', none: 'None',
        imu: 'IMU topic', noImu: 'No IMU (only LiDAR-only algorithms can run)',
        timeField: 'Per-point time field', noTime: 'None (no motion compensation)',
        timeUnit: 'Unit', absolute: 'Absolute time (time of day, not an offset from the scan stamp)',
        ring: 'Ring field', intensity: 'Intensity field',
        acc: 'IMU acceleration unit',
        reference: 'Reference odometry (optional, shown as deviation, not ground truth)',
        extrinsics: 'Extrinsics (4×4, map points from A into B; identity by default)',
        lidarToImu: 'lidar_to_imu', imuToBase: 'imu_to_base (robot base: x forward, y left, z up)',
        lidar2: 'lidar2_to_lidar (second lidar into the first)',
        matrixHint: '16 numbers (4×4), 12 (3×4) or 9 (rotation only), separated by spaces, commas or new lines; brackets are ignored.',
        identity: 'Identity',
        matrixBad: '{key}: enter 16, 12 or 9 numbers',
        detected: 'Detected',
        cloudInfo: '{frame} · {n} points per scan ({finite} valid) · fields {fields}',
        timeInfo: '{field}: {min} … {max} (header stamp {stamp})',
        imuInfo: '{frame} · {rate} Hz · acceleration norm {norm} · gyro {gyro} rad/s',
        tilt: 'gravity vs base +z {deg}° (through imu_to_base)',
        save: 'Save', saveNew: 'Save as new profile', remove: 'Delete profile', cancel: 'Close',
        saved: 'Sensor profile {name} saved', removed: 'Sensor profile {name} deleted',
        removeAsk: 'Delete the sensor profile {name}? Bags with its topics are then played without it.',
        confirm: 'Delete', locked: 'Profiles cannot change while a playback runs',
        warn: {
          no_cloud: 'The bag has no PointCloud2 topic.',
          no_time: 'No usable per-point time (no time field, or all points carry the same time): scans are not motion compensated.',
          time_negative: 'Point times in "{field}" are negative (the stamp is the end of the scan). The adapter clips them to 0, so motion compensation is wrong for this lidar.',
          no_imu: 'The bag has no IMU topic: only LiDAR-only algorithms can run.',
          imu_unit_unknown: 'The mean acceleration norm is {norm}, neither about 1 (g) nor about 9.8 (m/s²). Check the unit.',
          imu_moving: 'The robot was moving during the first IMU samples, so the unit and gravity checks are less certain.',
          gravity_tilt: 'Through imu_to_base, gravity is {deg}° away from +z of the robot base, so the map will be tilted. Enter the rotation of the IMU mounting in imu_to_base.',
        },
      },
    },
    'zh-CN': {
      sp: {
        title: '传感器配置：{bag}',
        desc: '告诉控制台怎么读这台机器人的雷达和 IMU。下面的值是从数据包前几帧点云和约 2 秒 IMU 自动识别的，保存前请核对。话题相同的其他数据包也会用这份配置。',
        probing: '正在读取数据包开头的消息…',
        retry: '重新读取',
        matched: '这个包现在用的配置是“{name}”',
        matchedBuiltin: '这个包现在用的是内置配置“{name}”（config/sensor_profiles.yaml，只读）。在这里保存一份话题相同的配置后，会优先用新配置。',
        fillGuess: '填入自动识别结果',
        fillMatched: '填入当前配置',
        name: '配置名称', nameRule: '1–32 个英文字母、数字、下划线或连字符',
        label: '显示名称（可选）',
        points: '点云话题', points2: '第二台雷达（可选，与第一台合并）', none: '无',
        imu: 'IMU 话题', noImu: '无 IMU（只能用仅激光算法）',
        timeField: '逐点时间字段', noTime: '无（不做运动畸变补偿）',
        timeUnit: '单位', absolute: '绝对时间（一天中的时刻，而不是相对帧时间戳的偏移）',
        ring: '线号字段', intensity: '强度字段',
        acc: 'IMU 加速度单位',
        reference: '参考里程计（可选，只算偏差，不当真值）',
        extrinsics: '外参（4×4，把 A 中的点变换到 B；默认单位阵）',
        lidarToImu: 'lidar_to_imu', imuToBase: 'imu_to_base（车体：x 向前，y 向左，z 向上）',
        lidar2: 'lidar2_to_lidar（第二台雷达到第一台）',
        matrixHint: '16 个数（4×4）、12 个（3×4）或 9 个（只有旋转），用空格、逗号或换行分隔；方括号会被忽略。',
        identity: '单位阵',
        matrixBad: '{key}：请输入 16、12 或 9 个数',
        detected: '识别结果',
        cloudInfo: '{frame} · 每帧 {n} 点（有效 {finite}） · 字段 {fields}',
        timeInfo: '{field}：{min} … {max}（帧时间戳 {stamp}）',
        imuInfo: '{frame} · {rate} Hz · 加速度模长 {norm} · 角速度 {gyro} rad/s',
        tilt: '重力与车体 +z 夹角 {deg}°（按 imu_to_base 换算）',
        save: '保存', saveNew: '另存为新配置', remove: '删除配置', cancel: '关闭',
        saved: '传感器配置 {name} 已保存', removed: '传感器配置 {name} 已删除',
        removeAsk: '删除传感器配置 {name}？之后含这些话题的数据包将不经过它回放。',
        confirm: '删除', locked: '回放进行中，暂时不能修改配置',
        warn: {
          no_cloud: '数据包里没有 PointCloud2 话题。',
          no_time: '没有可用的逐点时间（没有时间字段，或所有点的时间都一样）：不会做运动畸变补偿。',
          time_negative: '“{field}”里的点时间是负数（帧时间戳是扫描结束时刻）。适配器会把负值截成 0，这台雷达的运动补偿会出错。',
          no_imu: '数据包里没有 IMU 话题：只能用仅激光算法。',
          imu_unit_unknown: '加速度模长平均为 {norm}，既不接近 1（g）也不接近 9.8（m/s²），请确认单位。',
          imu_moving: '前几百个 IMU 样本里机器人在动，单位和重力方向的判断不太可靠。',
          gravity_tilt: '按 imu_to_base 换算后，重力方向偏离车体 +z 轴 {deg}°，地图会倾斜。请在 imu_to_base 里填入 IMU 的安装旋转。',
        },
      },
    },
  },
})

const NAME_RE = /^[A-Za-z0-9_-]{1,32}$/
const IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
const MATRICES = ['lidar_to_imu', 'imu_to_base', 'lidar2_to_lidar'] as const
type MatrixKey = (typeof MATRICES)[number]

const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => { if (!busy.value) emit('update:modelValue', v) },
})
const probing = ref(false)
const busy = ref(false)
const error = ref('')
const probe = ref<SensorProbe | null>(null)
const form = ref<SensorProfileForm | null>(null)
const editing = ref(false) // the form holds a profile made on the page: save overwrites it
const matrixText = ref<Record<MatrixKey, string>>({ lidar_to_imu: '', imu_to_base: '', lidar2_to_lidar: '' })

const fmtMatrix = (m: number[][]) => m.map((r) => r.map((v) => String(+v.toPrecision(9))).join('  ')).join('\n')
function parseMatrix(text: string): number[][] | null {
  const n = (text.match(/[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g) ?? []).map(Number)
  if (n.length === 16) return [n.slice(0, 4), n.slice(4, 8), n.slice(8, 12), n.slice(12, 16)]
  if (n.length === 12) return [n.slice(0, 4), n.slice(4, 8), n.slice(8, 12), [0, 0, 0, 1]]
  if (n.length === 9) return [[...n.slice(0, 3), 0], [...n.slice(3, 6), 0], [...n.slice(6, 9), 0], [0, 0, 0, 1]]
  return null
}

function fill(f: SensorProfileForm, edit: boolean) {
  form.value = { ...f, name: edit ? f.name : '' }
  editing.value = edit
  for (const k of MATRICES) matrixText.value[k] = fmtMatrix(f[k] ?? IDENTITY)
}
async function load() {
  const b = props.bag
  if (!b) return
  probing.value = true
  error.value = ''
  probe.value = null
  form.value = null
  try {
    const p = await api.probeSensors(b.path)
    if (props.bag?.path !== b.path) return
    probe.value = p
    if (p.matched?.user) fill(p.matched, true)
    else fill(p.guess, false)
  } catch (e) {
    error.value = errorText(e)
  } finally {
    probing.value = false
  }
}
watch(() => props.modelValue, (v) => { if (v) void load() })

const clouds = computed(() => (probe.value?.topics ?? []).filter((x) => x.type === 'sensor_msgs/msg/PointCloud2').map((x) => x.name))
const imus = computed(() => (probe.value?.topics ?? []).filter((x) => x.type === 'sensor_msgs/msg/Imu').map((x) => x.name))
const odoms = computed(() => (probe.value?.topics ?? []).filter((x) => x.type === 'nav_msgs/msg/Odometry').map((x) => x.name))
const cloud = computed(() => (form.value?.points ? probe.value?.probe.clouds[form.value.points] : undefined))
const imu = computed(() => (form.value?.imu ? probe.value?.probe.imus[form.value.imu] : undefined))
const cloudFields = computed(() => (cloud.value?.fields ?? []).filter((f) => f.count === 1).map((f) => f.name))

// Switching the cloud / IMU topic applies that topic's guesses
function onPoints(topic: string) {
  const g = probe.value?.per_topic.clouds[topic]
  if (form.value && g) {
    Object.assign(form.value, {
      time_field: g.time_field, time_unit: g.time_unit, time_absolute: g.time_absolute,
      ring_field: g.ring_field, intensity_field: g.intensity_field,
    })
  }
  if (form.value?.points2 === topic) form.value.points2 = null
}
function onImu(topic: string | null) {
  const g = topic ? probe.value?.per_topic.imus[topic] : undefined
  if (form.value && g) form.value.imu_acc_unit = g
}

const num = (v: number, d = 3) => (Number.isFinite(v) ? +v.toFixed(d) : '—')
const timeStats = computed(() => {
  const f = form.value?.time_field
  const s = f ? cloud.value?.stats[f] : undefined
  return s && cloud.value ? t('sp.timeInfo', { field: f, min: num(s.min, 6), max: num(s.max, 6), stamp: num(cloud.value.stamp, 3) }) : ''
})
// Angle between the measured gravity reaction, rotated into the base by the imu_to_base being edited, and +z
const tilt = computed(() => {
  const a = imu.value?.acc_mean
  const m = parseMatrix(matrixText.value.imu_to_base)
  if (!a || !m) return null
  const z = m[2][0] * a[0] + m[2][1] * a[1] + m[2][2] * a[2]
  const n = Math.hypot(a[0], a[1], a[2])
  return n > 0 ? +(Math.acos(Math.min(1, Math.max(-1, z / n))) * 180 / Math.PI).toFixed(1) : null
})
// The probe's gravity_tilt assumes an identity imu_to_base; the page checks the matrix being edited instead
const warnings = computed(() => {
  const list = (probe.value?.warnings ?? []).filter((w) => w.code !== 'gravity_tilt').map((w) => t(`sp.warn.${w.code}`, w.params))
  if (tilt.value !== null && tilt.value > 10) list.push(t('sp.warn.gravity_tilt', { deg: tilt.value }))
  return list
})
const matchedLabel = computed(() => {
  const m = probe.value?.matched
  if (!m) return ''
  return t(m.user ? 'sp.matched' : 'sp.matchedBuiltin', { name: m.label || m.name })
})

const nameInvalid = computed(() => !!form.value?.name && !NAME_RE.test(form.value.name))
const canSave = computed(() => !!form.value && !busy.value && !props.disabled && NAME_RE.test(form.value.name) && !!form.value.points)

async function save(asNew = false) {
  const f = form.value
  const b = props.bag
  if (!f || !b || !canSave.value) return
  const body: SensorProfileForm = { ...f }
  for (const k of MATRICES) {
    if (k === 'lidar2_to_lidar' && !f.points2) continue
    const m = parseMatrix(matrixText.value[k])
    if (!m) {
      error.value = t('sp.matrixBad', { key: k })
      return
    }
    body[k] = m
  }
  busy.value = true
  error.value = ''
  try {
    const r = editing.value && !asNew ? await api.updateSensorProfile(b.path, body) : await api.createSensorProfile(b.path, body)
    ElMessage.success(t('sp.saved', { name: r.name }))
    emit('saved')
    busy.value = false
    open.value = false
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}
async function remove() {
  const f = form.value
  if (!f || !editing.value) return
  try {
    await ElMessageBox.confirm(t('sp.removeAsk', { name: f.name }), { type: 'warning', confirmButtonText: t('sp.confirm'), cancelButtonText: t('sp.cancel') })
  } catch {
    return
  }
  busy.value = true
  try {
    await api.deleteSensorProfile(f.name)
    ElMessage.success(t('sp.removed', { name: f.name }))
    emit('saved')
    busy.value = false
    open.value = false
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="open" class="import-dialog" :title="t('sp.title', { bag: bag?.name ?? '' })"
    width="min(720px, calc(100vw - 32px))" append-to-body destroy-on-close
    :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy"
  >
    <p class="import-note">{{ t('sp.desc') }}</p>
    <p v-if="probing" class="import-note" role="status">{{ t('sp.probing') }}</p>
    <template v-if="probe && form">
      <el-alert v-if="matchedLabel" :title="matchedLabel" type="info" :closable="false" show-icon />
      <div class="fill">
        <el-button size="small" @click="fill(probe.guess, false)">{{ t('sp.fillGuess') }}</el-button>
        <el-button v-if="probe.matched" size="small" @click="fill(probe.matched, !!probe.matched.user)">{{ t('sp.fillMatched') }}</el-button>
      </div>
      <ul v-if="warnings.length" class="warns">
        <li v-for="w in warnings" :key="w">{{ w }}</li>
      </ul>
      <el-form label-position="top" :disabled="busy" class="import-form sp-form" @submit.prevent="save()">
        <div class="row">
          <el-form-item :label="t('sp.name')" :error="nameInvalid ? t('sp.nameRule') : undefined" required>
            <el-input v-model="form.name" maxlength="32" :disabled="editing" :placeholder="t('sp.nameRule')" />
          </el-form-item>
          <el-form-item :label="t('sp.label')">
            <el-input v-model="form.label" maxlength="64" />
          </el-form-item>
        </div>
        <el-form-item :label="t('sp.points')" required>
          <el-select v-model="form.points" filterable @change="onPoints">
            <el-option v-for="c in clouds" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <p v-if="cloud" class="import-note mono">
          {{ t('sp.cloudInfo', { frame: cloud.frame_id || '—', n: cloud.points, finite: cloud.finite, fields: cloudFields.join(', ') }) }}
        </p>
        <div class="row three">
          <el-form-item :label="t('sp.timeField')">
            <el-select v-model="form.time_field" :placeholder="t('sp.noTime')">
              <el-option :label="t('sp.noTime')" value="" />
              <el-option v-for="f in cloudFields" :key="f" :label="f" :value="f" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('sp.timeUnit')">
            <el-select v-model="form.time_unit" :disabled="!form.time_field">
              <el-option v-for="u in ['s', 'ms', 'us', 'ns']" :key="u" :label="u" :value="u" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('sp.ring')">
            <el-select v-model="form.ring_field" :placeholder="t('sp.none')">
              <el-option :label="t('sp.none')" value="" />
              <el-option v-for="f in cloudFields" :key="f" :label="f" :value="f" />
            </el-select>
          </el-form-item>
        </div>
        <el-checkbox v-model="form.time_absolute" :disabled="!form.time_field" :label="t('sp.absolute')" />
        <p v-if="timeStats" class="import-note mono">{{ timeStats }}</p>
        <div class="row">
          <el-form-item :label="t('sp.intensity')">
            <el-select v-model="form.intensity_field" :placeholder="t('sp.none')">
              <el-option :label="t('sp.none')" value="" />
              <el-option v-for="f in cloudFields" :key="f" :label="f" :value="f" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('sp.points2')">
            <el-select v-model="form.points2" clearable :placeholder="t('sp.none')">
              <el-option v-for="c in clouds.filter((x) => x !== form?.points)" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>
        </div>
        <div class="row">
          <el-form-item :label="t('sp.imu')">
            <el-select v-model="form.imu" clearable :placeholder="t('sp.noImu')" @change="onImu">
              <el-option v-for="i in imus" :key="i" :label="i" :value="i" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('sp.acc')">
            <el-radio-group v-model="form.imu_acc_unit" :disabled="!form.imu">
              <el-radio-button value="g">g</el-radio-button>
              <el-radio-button value="m/s2">m/s²</el-radio-button>
            </el-radio-group>
          </el-form-item>
        </div>
        <p v-if="imu" class="import-note mono">
          {{ t('sp.imuInfo', { frame: imu.frame_id || '—', rate: imu.rate ? num(imu.rate, 1) : '—', norm: num(imu.acc_norm), gyro: num(imu.gyro_norm) }) }}
          <template v-if="tilt !== null"> · {{ t('sp.tilt', { deg: tilt }) }}</template>
        </p>
        <el-form-item :label="t('sp.reference')">
          <el-select v-model="form.reference" clearable :placeholder="t('sp.none')">
            <el-option v-for="o in odoms" :key="o" :label="o" :value="o" />
          </el-select>
        </el-form-item>
        <p class="sub">{{ t('sp.extrinsics') }}</p>
        <p class="import-note">{{ t('sp.matrixHint') }}</p>
        <div class="row">
          <el-form-item>
            <template #label>{{ t('sp.lidarToImu') }}
              <el-button text size="small" @click="matrixText.lidar_to_imu = fmtMatrix(IDENTITY)">{{ t('sp.identity') }}</el-button>
            </template>
            <el-input v-model="matrixText.lidar_to_imu" type="textarea" :rows="5" wrap="off" class="mono matrix" />
          </el-form-item>
          <el-form-item>
            <template #label>{{ t('sp.imuToBase') }}
              <el-button text size="small" @click="matrixText.imu_to_base = fmtMatrix(IDENTITY)">{{ t('sp.identity') }}</el-button>
            </template>
            <el-input v-model="matrixText.imu_to_base" type="textarea" :rows="5" wrap="off" class="mono matrix" />
          </el-form-item>
        </div>
        <el-form-item v-if="form.points2">
          <template #label>{{ t('sp.lidar2') }}</template>
          <el-input v-model="matrixText.lidar2_to_lidar" type="textarea" :rows="5" wrap="off" class="mono matrix" />
        </el-form-item>
      </el-form>
    </template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
    <p v-if="disabled" class="import-note">{{ t('sp.locked') }}</p>
    <template #footer>
      <el-button v-if="editing" type="danger" plain :disabled="busy || disabled" @click="remove">{{ t('sp.remove') }}</el-button>
      <el-button v-if="!probing && !probe" :disabled="busy" @click="load">{{ t('sp.retry') }}</el-button>
      <el-button :disabled="busy" @click="open = false">{{ t('sp.cancel') }}</el-button>
      <el-button v-if="editing" :disabled="!canSave" @click="editing = false; form && (form.name = '')">{{ t('sp.saveNew') }}</el-button>
      <el-button type="primary" :loading="busy" :disabled="!canSave" @click="save()">{{ t('sp.save') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.fill {
  margin: 8px 0;
}
.warns {
  margin: 0 0 10px;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--warn, #b45309);
}
.row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 12px;
}
.row.three {
  grid-template-columns: 1.4fr 0.8fr 1fr;
}
.sp-form :deep(.el-select) {
  width: 100%;
}
.sub {
  margin: 10px 0 2px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
}
.matrix :deep(textarea) {
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 11.5px;
  white-space: pre;
  overflow-x: auto;
}
</style>
