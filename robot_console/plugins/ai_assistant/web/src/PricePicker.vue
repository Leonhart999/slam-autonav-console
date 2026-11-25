<!-- Price of one model for the usage page's cost estimate: input, output, cache read and cache write per 1M tokens in
     USD or CNY. A compact chip in the model row; the form opens in a popover like the effort picker. -->
<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { ElButton, ElInputNumber, ElRadioButton, ElRadioGroup } from 'element-plus'
import type { Currency, Price } from './api'
import { t } from './sdk'

const price = defineModel<Price | null | undefined>({ required: true })

type Field = 'input' | 'output' | 'cache_read' | 'cache_write'
const FIELDS: Field[] = ['input', 'output', 'cache_read', 'cache_write']
const SYMBOL: Record<Currency, string> = { USD: '$', CNY: '¥' }

const open = ref(false)
const btn = ref<HTMLElement | null>(null)
const pop = ref<HTMLElement | null>(null)
const pos = ref<{ top?: string; bottom?: string; left: string }>({ top: '0px', left: '0px' })
const form = ref<{ currency: Currency } & Record<Field, number | undefined>>(blank())

function blank() {
  return { currency: 'USD' as Currency, input: undefined, output: undefined, cache_read: undefined, cache_write: undefined }
}

const isSet = computed(() => !!price.value && FIELDS.some((f) => typeof price.value?.[f] === 'number'))
const num = (v: number) => (+v.toFixed(4)).toString()
const chip = computed(() => {
  const p = price.value
  if (!p || !isSet.value) return t('editor.pricing.unset')
  const s = SYMBOL[p.currency] ?? ''
  const a = typeof p.input === 'number' ? `${s}${num(p.input)}` : '–'
  const b = typeof p.output === 'number' ? `${s}${num(p.output)}` : '–'
  return `${a} / ${b}`
})

function updatePos() {
  const el = btn.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const w = 270
  const left = Math.max(12, Math.min(r.right - w, window.innerWidth - w - 12))
  const h = pop.value?.offsetHeight || 250
  pos.value = window.innerHeight - r.bottom - 12 < h && r.top > h
    ? { bottom: `${Math.round(window.innerHeight - r.top + 6)}px`, left: `${Math.round(left)}px` }
    : { top: `${Math.round(r.bottom + 6)}px`, left: `${Math.round(left)}px` }
}

async function toggle() {
  open.value = !open.value
  if (!open.value) return
  const p = price.value
  form.value = p ? { currency: p.currency, input: p.input ?? undefined, output: p.output ?? undefined,
    cache_read: p.cache_read ?? undefined, cache_write: p.cache_write ?? undefined } : blank()
  await nextTick()
  updatePos()
}

function save() {
  const f = form.value
  const has = FIELDS.some((k) => typeof f[k] === 'number')
  price.value = has ? {
    currency: f.currency,
    ...Object.fromEntries(FIELDS.filter((k) => typeof f[k] === 'number').map((k) => [k, f[k]])),
  } as Price : null
  open.value = false
}

function clear() {
  price.value = null
  open.value = false
}

function onDoc(e: PointerEvent) {
  const target = e.target as Element | null
  if (target && (btn.value?.contains(target) || pop.value?.contains(target) || target.closest?.('.el-input-number'))) return
  open.value = false
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
  else if (e.key === 'Enter' && open.value && pop.value?.contains(document.activeElement)) save()
}
watch(open, (o) => {
  const fn = o ? 'addEventListener' : 'removeEventListener'
  document[fn]('pointerdown', onDoc as EventListener, true)
  document[fn]('keydown', onKey as EventListener, true)
  window[fn]('resize', updatePos)
  window[fn]('scroll', updatePos, true)
})
onUnmounted(() => { open.value = false })
</script>

<template>
  <button ref="btn" type="button" class="aip-epick-btn aip-price-btn" :class="{ open, unset: !isSet }"
          :aria-expanded="open" :title="t('editor.pricing.title')" @click="toggle">
    <span class="aip-epick-text mono">{{ chip }}</span>
  </button>
  <Teleport to="body">
    <transition name="aip-epop">
      <div v-if="open" ref="pop" class="aip-epop aip-price" :style="pos" role="dialog" :aria-label="t('editor.pricing.title')">
        <div class="aip-price-head">
          <span>{{ t('editor.pricing.title') }}</span>
          <el-radio-group v-model="form.currency" size="small">
            <el-radio-button value="USD">USD</el-radio-button>
            <el-radio-button value="CNY">CNY</el-radio-button>
          </el-radio-group>
        </div>
        <label v-for="k in FIELDS" :key="k" class="aip-price-row">
          <span>{{ t(`editor.pricing.${k}`) }}</span>
          <el-input-number v-model="form[k]" :min="0" :max="100000" :precision="4" :step="0.1" :controls="false" size="small"
                           :placeholder="t('editor.pricing.none')" class="mono" />
        </label>
        <p class="aip-price-unit">{{ t('editor.pricing.unit', { c: form.currency }) }}</p>
        <div class="aip-price-foot">
          <el-button size="small" text :disabled="!isSet" @click="clear">{{ t('editor.pricing.clear') }}</el-button>
          <span class="aip-grow" />
          <el-button size="small" type="primary" @click="save">{{ t('editor.pricing.apply') }}</el-button>
        </div>
      </div>
    </transition>
  </Teleport>
</template>
