<!-- A setting row bound to one console.yaml key of the shared draft (store.ts). The control depends on `kind`:
     number | text | range (two numbers) | list (strings, add with Enter) | numlist (numbers) | multi (fixed options).
     Texts come from settings/messages.ts under `msg` (label, sub, tip). -->
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshLeft } from '@element-plus/icons-vue'
import SettingRow from './SettingRow.vue'
import { settingsMessages } from './messages'
import { draft, fieldErrors, payload, saving, writable } from './store'
import type { SettingValue } from './api'

const props = withDefaults(defineProps<{
  k: string
  msg: string
  kind?: 'number' | 'text' | 'range' | 'list' | 'numlist' | 'multi'
  min?: number
  max?: number
  step?: number
  precision?: number
  unit?: string
  placeholder?: string
  options?: { value: string; label: string }[]
  stacked?: boolean
  /** extra description under the standard one (e.g. folder size) */
  noTip?: boolean
}>(), { kind: 'text' })

const { t, te } = useI18n({ messages: settingsMessages })

const field = computed(() => payload.value?.fields[props.k])
const changedFromDefault = computed(() => !!field.value && JSON.stringify(draft[props.k]) !== JSON.stringify(field.value.default))
const dirty = computed(() => !!field.value && JSON.stringify(draft[props.k]) !== JSON.stringify(field.value.value))
const defaultText = computed(() => {
  const d = field.value?.default
  return Array.isArray(d) ? d.join(', ') : d == null || d === '' ? '—' : String(d)
})
function useDefault() {
  if (field.value) draft[props.k] = JSON.parse(JSON.stringify(field.value.default))
}
const error = computed(() => {
  const e = fieldErrors[props.k]
  if (!e) return ''
  const key = `settings.reason.${e.reason}`
  return te(key) ? t(key, { min: e.min ?? '', max: e.max ?? '' }) : e.reason
})
const tip = computed(() => {
  if (props.noTip) return ''
  const key = `${props.msg}.tip`
  return te(key) ? t(key) : ''
})
// locked while a save is in flight: its answer replaces the draft, so an edit made meanwhile would be lost
const disabled = computed(() => !writable.value || saving.value)

// Scalars
const num = computed<number | undefined>({
  get: () => (typeof draft[props.k] === 'number' ? draft[props.k] as number : undefined),
  set: (v) => { draft[props.k] = v ?? null },
})
const text = computed<string>({
  get: () => (draft[props.k] == null ? '' : String(draft[props.k])),
  set: (v) => { draft[props.k] = v.trim() },
})
// [lo, hi]
const pair = (i: 0 | 1) => computed<number | undefined>({
  get: () => {
    const v = draft[props.k]
    return Array.isArray(v) && typeof v[i] === 'number' ? v[i] as number : undefined
  },
  set: (x) => {
    const v = Array.isArray(draft[props.k]) ? [...draft[props.k] as SettingValue[]] : [null, null]
    v[i] = x ?? null
    draft[props.k] = v
  },
})
const lo = pair(0)
const hi = pair(1)
// Lists: el-select works on strings; numeric lists are converted back to numbers (invalid entries are dropped)
const items = computed<string[]>({
  get: () => (Array.isArray(draft[props.k]) ? (draft[props.k] as SettingValue[]).map(String) : []),
  set: (v) => {
    const clean = v.map((s) => s.trim()).filter(Boolean)
    draft[props.k] = props.kind === 'numlist'
      ? clean.map(Number).filter((n) => Number.isFinite(n))
      : clean
  },
})
const listOptions = computed(() => props.options ?? items.value.map((v) => ({ value: v, label: v })))
</script>

<template>
  <SettingRow
    :label="t(`${msg}.label`)" :sub="t(`${msg}.sub`)" :tip="tip" :overridden="changedFromDefault"
    :error="error" :stacked="stacked" :dirty="dirty"
  >
    <template #label-extra>
      <el-tooltip v-if="changedFromDefault && !disabled" :content="t('settings.useDefault', { v: defaultText })" :show-after="200">
        <button class="reset" type="button" :aria-label="t('settings.useDefault', { v: defaultText })" @click="useDefault">
          <el-icon :size="12"><RefreshLeft /></el-icon>
        </button>
      </el-tooltip>
    </template>
    <template v-if="kind === 'number'">
      <el-input-number
        v-model="num" :min="min" :max="max" :step="step ?? 1" :precision="precision" controls-position="right"
        :disabled="disabled" class="num"
      />
      <span v-if="!stacked" class="unit">{{ unit ?? '' }}</span>
    </template>
    <template v-else-if="kind === 'range'">
      <el-input-number
        v-model="lo" :min="min" :max="max" :step="step ?? 0.1" :precision="precision" controls-position="right"
        :disabled="disabled" class="num" :aria-label="t('settings.min')"
      />
      <span class="unit sep">–</span>
      <el-input-number
        v-model="hi" :min="min" :max="max" :step="step ?? 0.1" :precision="precision" controls-position="right"
        :disabled="disabled" class="num" :aria-label="t('settings.max')"
      />
      <span v-if="!stacked" class="unit">{{ unit ?? '' }}</span>
    </template>
    <template v-else-if="kind === 'list' || kind === 'numlist' || kind === 'multi'">
      <el-select
        v-model="items" multiple :filterable="kind !== 'multi'" :allow-create="kind !== 'multi'"
        default-first-option :reserve-keyword="false" :disabled="disabled" :placeholder="t('settings.listHint')"
        class="list" :class="{ wide: stacked }"
      >
        <el-option v-for="o in listOptions" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>
      <span v-if="!stacked" class="unit">{{ unit ?? '' }}</span>
    </template>
    <template v-else>
      <el-input
        v-model="text" :disabled="disabled" :placeholder="placeholder" class="text mono-in"
        :class="{ wide: stacked }" spellcheck="false"
      />
      <slot name="after" />
    </template>
  </SettingRow>
</template>

<style scoped>
.num { width: 132px; }
.reset {
  display: inline-grid;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  color: var(--ink-3);
  background: none;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s, background 0.15s;
}
.row:hover .reset, .reset:focus-visible { opacity: 1; }
.reset:hover { color: var(--accent); background: rgba(var(--accent-rgb), 0.1); }
.unit { font-size: 12px; color: var(--ink-3); white-space: nowrap; }
.ctrl-unit, .unit:not(.sep) { min-width: 34px; }
.list { width: 340px; }
.text { width: 300px; }
.wide { width: 100%; }
.mono-in :deep(input) { font-family: var(--font-mono); font-size: 12.5px; }
</style>
