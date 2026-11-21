<!-- Editor of one model service profile: identity, API format, key, address (with candidate addresses and a latency
     check), model list (fetched from the service or typed), test, and advanced request overrides. -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElButton, ElInput, ElMessage, ElMessageBox, ElOption, ElRadioButton, ElRadioGroup, ElSelect, ElSwitch, ElTooltip } from 'element-plus'
import {
  probeLatency, testProfile, type EffortValue, type Latency, type ModelEntry, type Profile, type ProfileInput, type TestResult,
} from './api'
import { COLORS, USER_AGENTS, avatarColor, initial } from './presets'
import { RC, t } from './sdk'
import Icon from './Icon.vue'
import EffortPicker from './EffortPicker.vue'
import ModelFetchDialog from './ModelFetchDialog.vue'

const props = defineProps<{ profile: Profile | null; draft: ProfileInput | null; active: boolean }>()
const emit = defineEmits<{
  save: [input: ProfileInput, id: string | null]
  remove: [id: string]
  activate: [id: string]
  cancel: []
  dirty: [dirty: boolean]
}>()

const OPENAI_DEFAULT = 'https://api.openai.com/v1'
const ANTHROPIC_DEFAULT = 'https://api.anthropic.com'

const pf = ref<ProfileInput>(fromSource())
const keyInput = ref('')
const clearKey = ref(false)
const headersText = ref('')
const bodyText = ref('')
const baseline = ref('')
const showColors = ref(false)
const showEndpoints = ref(false)
const showAdvanced = ref(false)
const newEndpoint = ref('')
const latency = ref<Record<string, Latency>>({})
const measuring = ref(false)
const testing = ref(false)
const testResult = ref<TestResult | null>(null)
const testError = ref('')
const saving = ref(false)
const fetchOpen = ref(false)
const dragRow = ref(-1)
const modelInputs = ref<HTMLElement | null>(null)

const id = computed(() => props.profile?.id ?? null)
const isNew = computed(() => !props.profile)

function fromSource(): ProfileInput {
  const src = props.profile ?? props.draft
  const p = JSON.parse(JSON.stringify(src ?? {})) as Partial<Profile>
  return {
    name: p.name ?? '', note: p.note ?? '', website: p.website ?? '', color: p.color ?? '',
    format: p.format ?? 'openai', base_url: p.base_url ?? '', endpoints: p.endpoints ?? [], full_url: !!p.full_url,
    models: (p.models ?? []).map((m) => ({
      id: m.id, label: m.label === m.id ? '' : m.label, effort: m.effort ?? '', efforts: (m.efforts ?? []) as EffortValue[],
    })),
    model: p.model ?? '', proxy: p.proxy ?? '', user_agent: p.user_agent ?? '', headers: p.headers ?? {}, body: p.body ?? {},
  }
}

const json = (o: Record<string, unknown>) => (Object.keys(o).length ? JSON.stringify(o, null, 2) : '')

function reset() {
  pf.value = fromSource()
  keyInput.value = ''
  clearKey.value = false
  headersText.value = json(pf.value.headers)
  bodyText.value = json(pf.value.body)
  latency.value = {}
  testResult.value = null
  testError.value = ''
  showEndpoints.value = false
  showAdvanced.value = !!(pf.value.proxy || pf.value.user_agent || pf.value.full_url || headersText.value || bodyText.value)
  void nextTick(() => (baseline.value = snapshot()))
}
watch(() => [props.profile, props.draft], reset, { immediate: true })

function parseObject(text: string): { value: Record<string, unknown> | null; error: string } {
  if (!text.trim()) return { value: {}, error: '' }
  try {
    const v = JSON.parse(text) as unknown
    if (!v || typeof v !== 'object' || Array.isArray(v)) return { value: null, error: t('editor.jsonObject') }
    return { value: v as Record<string, unknown>, error: '' }
  } catch (e) {
    return { value: null, error: e instanceof Error ? e.message : String(e) }
  }
}
const headersParsed = computed(() => parseObject(headersText.value))
const bodyParsed = computed(() => parseObject(bodyText.value))

const modelProblem = computed(() => {
  const ids = pf.value.models.map((m) => m.id.trim())
  if (ids.some((x) => !x)) return t('editor.models.emptyId')
  if (new Set(ids).size !== ids.length) return t('editor.models.duplicate')
  return ''
})
const urlProblem = computed(() => {
  const all = [pf.value.base_url, pf.value.website, pf.value.proxy].filter((u) => u.trim())
  return all.some((u) => !/^https?:\/\/\S+/.test(u.trim())) ? t('editor.urlInvalid') : ''
})
const invalid = computed(() => !pf.value.name.trim() || !!headersParsed.value.error || !!bodyParsed.value.error
  || !!modelProblem.value || !!urlProblem.value)

function toInput(): ProfileInput {
  const models: ModelEntry[] = pf.value.models.map((m) => ({
    id: m.id.trim(), label: m.label.trim() || m.id.trim(), effort: m.effort, efforts: m.efforts ?? [],
  }))
  return {
    ...pf.value,
    name: pf.value.name.trim(),
    models,
    model: models.some((m) => m.id === pf.value.model) ? pf.value.model : (models[0]?.id ?? ''),
    endpoints: pf.value.endpoints.filter((u) => u.trim() && u.trim() !== pf.value.base_url.trim()),
    full_url: pf.value.format === 'openai' && pf.value.full_url,
    headers: (headersParsed.value.value ?? {}) as Record<string, string>,
    body: bodyParsed.value.value ?? {},
    api_key: keyInput.value.trim(),
    clear_key: clearKey.value,
  }
}
function snapshot() {
  return JSON.stringify([pf.value, keyInput.value, clearKey.value, headersText.value, bodyText.value])
}
const dirty = computed(() => isNew.value || snapshot() !== baseline.value)
watch(dirty, (d) => emit('dirty', d), { immediate: true })

const requestUrl = computed(() => {
  const base = pf.value.base_url.trim().replace(/\/+$/, '')
  if (pf.value.format === 'anthropic') return `${base || ANTHROPIC_DEFAULT}/v1/messages`
  if (pf.value.full_url) return base
  return `${(base || OPENAI_DEFAULT).replace(/\/chat\/completions$/, '')}/chat/completions`
})
const keyPlaceholder = computed(() => {
  if (props.profile?.key_set && !clearKey.value) return t('editor.key.kept', { hint: props.profile.key_hint })
  return pf.value.format === 'anthropic' ? 'sk-ant-…' : 'sk-…'
})

// ---- candidate addresses + latency ----
const isHttp = (u: string) => /^https?:\/\/\S+/.test(u.trim())
const currentUrl = computed(() => pf.value.base_url.trim().replace(/\/+$/, ''))
const candidates = computed(() => {
  const list = [pf.value.base_url.trim(), ...pf.value.endpoints.map((u) => u.trim())].filter(Boolean)
  return [...new Set(list.map((u) => u.replace(/\/+$/, '')))]
})
function addEndpoint() {
  const u = newEndpoint.value.trim().replace(/\/+$/, '')
  if (!/^https?:\/\/\S+/.test(u)) { ElMessage.error(t('editor.urlInvalid')); return }
  if (!candidates.value.includes(u)) pf.value.endpoints.push(u)
  if (!pf.value.base_url.trim()) pf.value.base_url = u
  newEndpoint.value = ''
}
function removeEndpoint(u: string) {
  pf.value.endpoints = pf.value.endpoints.filter((x) => x.replace(/\/+$/, '') !== u)
  if (pf.value.base_url.replace(/\/+$/, '') === u) pf.value.base_url = pf.value.endpoints[0] ?? ''
}
function useEndpoint(u: string) {
  const old = pf.value.base_url.trim().replace(/\/+$/, '')
  pf.value.endpoints = pf.value.endpoints.filter((x) => x.replace(/\/+$/, '') !== u)
  if (old && old !== u && !pf.value.endpoints.includes(old)) pf.value.endpoints.unshift(old)
  pf.value.base_url = u
}
async function measure() {
  const urls = candidates.value.length ? candidates.value
    : [pf.value.format === 'anthropic' ? ANTHROPIC_DEFAULT : OPENAI_DEFAULT]
  measuring.value = true
  try {
    const r = await probeLatency(id.value, toInput(), urls)
    latency.value = Object.fromEntries(r.results.map((x) => [x.url, x]))
  } catch (e) {
    ElMessage.error(RC().errorText(e))
  } finally {
    measuring.value = false
  }
}
const fastest = computed(() => {
  const ok = Object.values(latency.value).filter((x) => x.ms !== null)
  return ok.length > 1 ? ok.reduce((a, b) => (a.ms! <= b.ms! ? a : b)).url : ''
})
const speedClass = (ms: number) => (ms < 400 ? 'fast' : ms < 1200 ? 'mid' : 'slow')

// ---- models ----
function addModel() {
  pf.value.models.push({ id: '', label: '', effort: '', efforts: [] })
  void nextTick(() => {
    const inputs = modelInputs.value?.querySelectorAll<HTMLInputElement>('.aip-model-id input')
    inputs?.[inputs.length - 1]?.focus()
  })
}
function removeModel(i: number) {
  const [m] = pf.value.models.splice(i, 1)
  if (m && m.id === pf.value.model) pf.value.model = pf.value.models[0]?.id ?? ''
}
function addFetched(ids: string[]) {
  const have = new Set(pf.value.models.map((m) => m.id))
  for (const mid of ids) if (!have.has(mid)) pf.value.models.push({ id: mid, label: '', effort: '', efforts: [] })
  if (!pf.value.model && pf.value.models.length) pf.value.model = pf.value.models[0].id
}
function onRowDragStart(i: number, e: DragEvent) {
  dragRow.value = i
  e.dataTransfer?.setData('text/plain', String(i))
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
}
function onRowDrop(i: number) {
  const from = dragRow.value
  dragRow.value = -1
  if (from < 0 || from === i) return
  const [m] = pf.value.models.splice(from, 1)
  pf.value.models.splice(i, 0, m)
}

// ---- actions ----
async function test() {
  testing.value = true
  testResult.value = null
  testError.value = ''
  try {
    testResult.value = await testProfile(id.value, toInput())
  } catch (e) {
    testError.value = RC().errorText(e)
  } finally {
    testing.value = false
  }
}
function save() {
  if (invalid.value) return
  saving.value = true
  emit('save', toInput(), id.value)
}
defineExpose({ saved: () => { saving.value = false; keyInput.value = ''; clearKey.value = false }, failed: () => (saving.value = false) })

async function remove() {
  if (!props.profile) return
  try {
    await ElMessageBox.confirm(t('editor.removeConfirm', { name: props.profile.name }), t('editor.remove'),
      { type: 'warning', confirmButtonText: t('editor.remove'), cancelButtonText: t('editor.cancel') })
  } catch {
    return
  }
  emit('remove', props.profile.id)
}
function pickUserAgent(v: string) {
  pf.value.user_agent = v === '-' ? '' : v
}
</script>

<template>
  <div class="aip-editor">
    <div v-if="active" class="aip-banner"><Icon name="info" />{{ t('editor.activeBanner') }}</div>
    <div v-else-if="profile" class="aip-banner idle">
      <Icon name="info" />{{ t('editor.idleBanner') }}
      <span class="aip-grow" />
      <el-button size="small" type="primary" :disabled="dirty" @click="emit('activate', profile.id)">{{ t('list.use') }}</el-button>
    </div>

    <div class="aip-grid2">
      <label class="aip-field">
        <span class="aip-label">{{ t('editor.name') }}<b>*</b></span>
        <span class="aip-row">
          <button type="button" class="aip-avatar big" :style="{ background: avatarColor(pf.color, pf.name) }"
                  :title="t('editor.color')" @click="showColors = !showColors">{{ initial(pf.name) }}</button>
          <el-input v-model="pf.name" maxlength="60" :placeholder="t('editor.namePlaceholder')" />
        </span>
      </label>
      <label class="aip-field">
        <span class="aip-label">{{ t('editor.note') }}</span>
        <el-input v-model="pf.note" maxlength="200" :placeholder="t('editor.notePlaceholder')" />
      </label>
    </div>
    <div class="aip-fold" :class="{ open: showColors }">
      <div class="aip-fold-inner">
        <div class="aip-swatches">
          <button type="button" class="aip-swatch auto" :class="{ on: !pf.color }" :title="t('editor.colorAuto')"
                  @click="pf.color = ''">A</button>
          <button v-for="c in COLORS" :key="c" type="button" class="aip-swatch" :class="{ on: pf.color === c }"
                  :style="{ background: avatarColor(c, '') }" :aria-label="c" @click="pf.color = c" />
        </div>
      </div>
    </div>

    <label class="aip-field">
      <span class="aip-label">{{ t('editor.website') }}</span>
      <span class="aip-row">
        <el-input v-model="pf.website" maxlength="500" placeholder="https://example.com" />
        <a v-if="isHttp(pf.website)" class="aip-ibtn" :href="pf.website" target="_blank"
           rel="noopener noreferrer" :title="t('editor.openWebsite')">↗</a>
      </span>
    </label>

    <div class="aip-field">
      <span class="aip-label">{{ t('editor.format') }}</span>
      <el-radio-group v-model="pf.format" size="small">
        <el-radio-button value="openai">{{ t('editor.formats.openai') }}</el-radio-button>
        <el-radio-button value="anthropic">{{ t('editor.formats.anthropic') }}</el-radio-button>
      </el-radio-group>
      <span class="aip-help">{{ t(`editor.formatHelp.${pf.format}`) }}</span>
    </div>

    <div class="aip-field">
      <span class="aip-label">API Key<b v-if="pf.format === 'anthropic'">*</b></span>
      <span class="aip-row">
        <el-input v-model="keyInput" type="password" show-password autocomplete="new-password" :placeholder="keyPlaceholder"
                  @input="clearKey = false" />
        <el-button v-if="profile?.key_set" size="small" text :type="clearKey ? 'danger' : 'default'"
                   @click="clearKey = !clearKey; keyInput = ''">{{ clearKey ? t('editor.key.undoClear') : t('editor.key.clear') }}</el-button>
      </span>
      <span class="aip-help">{{ clearKey ? t('editor.key.willClear') : t('editor.key.help') }}</span>
    </div>

    <div class="aip-field">
      <span class="aip-label">
        {{ t('editor.baseUrl') }}
        <span class="aip-grow" />
        <button type="button" class="aip-link" @click="showEndpoints = !showEndpoints">
          <Icon name="bolt" />{{ t('editor.endpoints.toggle') }}
        </button>
      </span>
      <el-input v-model="pf.base_url" maxlength="500"
                :placeholder="pf.format === 'anthropic' ? ANTHROPIC_DEFAULT : OPENAI_DEFAULT" />
      <span class="aip-help mono">{{ t('editor.requestUrl') }} {{ requestUrl }}</span>
    </div>
    <div class="aip-fold" :class="{ open: showEndpoints }">
      <div class="aip-fold-inner">
        <div class="aip-card">
          <div class="aip-card-head">
            <span>{{ t('editor.endpoints.title') }}</span>
            <span class="aip-grow" />
            <el-button size="small" :loading="measuring" @click="measure"><Icon name="bolt" />{{ t('editor.endpoints.measure') }}</el-button>
          </div>
          <p class="aip-help">{{ t('editor.endpoints.help') }}</p>
          <div v-for="u in candidates" :key="u" class="aip-ep" :class="{ current: u === currentUrl }">
            <span class="aip-ep-url mono" :title="u">{{ u }}</span>
            <span v-if="latency[u]" class="aip-ms mono" :class="latency[u].ms !== null ? speedClass(latency[u].ms!) : 'down'"
                  :title="latency[u].error || (latency[u].status ? `HTTP ${latency[u].status}` : '')">
              {{ latency[u].ms !== null ? `${latency[u].ms} ms` : t('editor.endpoints.down') }}
            </span>
            <span v-if="u === fastest" class="aip-tag">{{ t('editor.endpoints.fastest') }}</span>
            <span v-if="u === currentUrl" class="aip-tag on">{{ t('editor.endpoints.current') }}</span>
            <el-button v-else size="small" text @click="useEndpoint(u)">{{ t('editor.endpoints.use') }}</el-button>
            <button type="button" class="aip-ibtn" :title="t('editor.endpoints.remove')" @click="removeEndpoint(u)"><Icon name="close" /></button>
          </div>
          <div class="aip-row">
            <el-input v-model="newEndpoint" size="small" :placeholder="t('editor.endpoints.addPlaceholder')" @keyup.enter="addEndpoint" />
            <el-button size="small" @click="addEndpoint"><Icon name="plus" />{{ t('editor.endpoints.add') }}</el-button>
          </div>
        </div>
      </div>
    </div>

    <div class="aip-field">
      <span class="aip-label">
        {{ t('editor.models.title') }}
        <span class="aip-grow" />
        <el-button size="small" @click="fetchOpen = true"><Icon name="download" />{{ t('editor.models.fetch') }}</el-button>
        <el-button size="small" @click="addModel"><Icon name="plus" />{{ t('editor.models.add') }}</el-button>
      </span>
      <span class="aip-help">{{ pf.format === 'anthropic' && !pf.models.length ? t('editor.models.anthropicDefault') : t('editor.models.help') }}</span>
      <div v-if="pf.models.length" ref="modelInputs" class="aip-models">
        <div class="aip-mrow head">
          <span /><span>{{ t('editor.models.label') }}</span><span>{{ t('editor.models.id') }}</span>
          <span>{{ t('editor.models.effort') }}</span><span>{{ t('editor.models.default') }}</span><span />
        </div>
        <div v-for="(m, i) in pf.models" :key="i" class="aip-mrow" :class="{ dragging: dragRow === i }"
             @dragover.prevent @drop="onRowDrop(i)">
          <span class="aip-grip" draggable="true" :title="t('editor.models.drag')" @dragstart="onRowDragStart(i, $event)" @dragend="dragRow = -1">⠿</span>
          <el-input v-model="m.label" size="small" :placeholder="m.id || t('editor.models.labelPlaceholder')" />
          <el-input v-model="m.id" size="small" class="aip-model-id" :placeholder="t('editor.models.idPlaceholder')" />
          <EffortPicker v-model:efforts="m.efforts" v-model:effort="m.effort" />
          <button type="button" class="aip-radio" :class="{ on: pf.model === m.id && !!m.id }" :aria-pressed="pf.model === m.id"
                  :title="t('editor.models.makeDefault')" :disabled="!m.id" @click="pf.model = m.id" />
          <button type="button" class="aip-ibtn" :title="t('editor.models.remove')" @click="removeModel(i)"><Icon name="trash" /></button>
        </div>
      </div>
      <span v-if="modelProblem" class="aip-error">{{ modelProblem }}</span>
    </div>

    <div class="aip-adv">
      <button type="button" class="aip-adv-head" :aria-expanded="showAdvanced" @click="showAdvanced = !showAdvanced">
        <Icon name="chevron" class="aip-caret" :class="{ open: showAdvanced }" />{{ t('editor.advanced.title') }}
      </button>
      <div class="aip-fold" :class="{ open: showAdvanced }">
        <div class="aip-fold-inner">
          <div class="aip-adv-body">
            <label class="aip-field">
              <span class="aip-label">{{ t('editor.advanced.proxy') }}</span>
              <el-input v-model="pf.proxy" maxlength="500" placeholder="http://127.0.0.1:7890" />
              <span class="aip-help">{{ t('editor.advanced.proxyHelp') }}</span>
            </label>
            <div v-if="pf.format === 'openai'" class="aip-switch-row">
              <div>
                <div class="aip-label">{{ t('editor.advanced.fullUrl') }}</div>
                <span class="aip-help">{{ t('editor.advanced.fullUrlHelp') }}</span>
              </div>
              <el-switch v-model="pf.full_url" />
            </div>
            <div class="aip-field">
              <span class="aip-label">{{ t('editor.advanced.userAgent') }}</span>
              <span class="aip-row">
                <el-input v-model="pf.user_agent" maxlength="500" placeholder="Mozilla/5.0 …" />
                <el-select class="aip-ua" size="default" :model-value="''" :placeholder="t('editor.advanced.uaPreset')"
                           @update:model-value="pickUserAgent">
                  <el-option v-for="u in USER_AGENTS" :key="u.key" :value="u.value" :label="t(`editor.advanced.ua.${u.key}`)" />
                  <el-option value="-" :label="t('editor.advanced.ua.none')" />
                </el-select>
              </span>
            </div>
            <div class="aip-grid2">
              <label class="aip-field">
                <span class="aip-label">{{ t('editor.advanced.headers') }}</span>
                <el-input v-model="headersText" type="textarea" :rows="5" class="mono" placeholder='{&#10;  "X-Provider": "cc-switch"&#10;}' />
                <span v-if="headersParsed.error" class="aip-error">{{ headersParsed.error }}</span>
                <span v-else class="aip-help">{{ t('editor.advanced.headersHelp') }}</span>
              </label>
              <label class="aip-field">
                <span class="aip-label">{{ t('editor.advanced.body') }}</span>
                <el-input v-model="bodyText" type="textarea" :rows="5" class="mono" placeholder='{&#10;  "temperature": 0.2&#10;}' />
                <span v-if="bodyParsed.error" class="aip-error">{{ bodyParsed.error }}</span>
                <span v-else class="aip-help">{{ t('editor.advanced.bodyHelp') }}</span>
              </label>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="testResult || testError" class="aip-test" :class="{ bad: !!testError }">
      <template v-if="testResult"><Icon name="check" />{{ t('editor.testOk', { model: testResult.model, seconds: testResult.seconds }) }}
        <span v-if="testResult.reply" class="aip-dim">“{{ testResult.reply }}”</span></template>
      <template v-else>{{ testError }}</template>
    </div>
    <span v-if="urlProblem" class="aip-error">{{ urlProblem }}</span>

    <footer class="aip-actions">
      <el-tooltip :content="t('editor.testHelp')" placement="top" :show-after="400">
        <el-button :loading="testing" :disabled="invalid" @click="test">{{ t('editor.test') }}</el-button>
      </el-tooltip>
      <span class="aip-grow" />
      <el-button v-if="profile" text type="danger" @click="remove">{{ t('editor.remove') }}</el-button>
      <el-button v-if="isNew" @click="emit('cancel')">{{ t('editor.cancel') }}</el-button>
      <el-button v-else :disabled="!dirty" @click="reset">{{ t('editor.revert') }}</el-button>
      <el-button type="primary" :loading="saving" :disabled="invalid || !dirty" @click="save">
        {{ isNew ? t('editor.create') : t('editor.save') }}
      </el-button>
    </footer>

    <ModelFetchDialog v-model="fetchOpen" :profile-id="id" :input="toInput" :have="pf.models.map((m) => m.id)" @add="addFetched" />
  </div>
</template>
