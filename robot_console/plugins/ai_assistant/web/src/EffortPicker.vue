<!-- Multi-select reasoning effort picker per model row (cc-switch style): free checkboxes with search + default
     level selector inside a teleported popover. Effort values stay in English everywhere. -->
<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { ElOption, ElSelect } from 'element-plus'
import type { Effort, EffortValue } from './api'
import { EFFORT_VALUES, effortSummary } from './presets'
import { t } from './sdk'
import Icon from './Icon.vue'

const efforts = defineModel<EffortValue[]>('efforts', { required: true })
const effort = defineModel<Effort>('effort', { required: true })

const open = ref(false)
const query = ref('')
const btn = ref<HTMLElement | null>(null)
const pop = ref<HTMLElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)
const pos = ref<{ top?: string; bottom?: string; left: string; minWidth: string }>({
  top: '0px', left: '0px', minWidth: '220px',
})

const summary = computed(() => effortSummary(efforts.value, effort.value, t('editor.models.effortUnset')))
const isUnset = computed(() => !efforts.value.length && !effort.value)
const tooltip = computed(() => {
  const list = efforts.value.length ? efforts.value.join(', ') : t('editor.models.effortUnset')
  return `${list} (${t('editor.models.effortDefaultLabel')}: ${effort.value || 'default'})`
})
const shown = computed(() => {
  const q = query.value.trim().toLowerCase()
  return q ? EFFORT_VALUES.filter((e) => e.includes(q)) : EFFORT_VALUES
})
const defaultChoices = computed(() => (efforts.value.length ? efforts.value : EFFORT_VALUES))

function updatePos() {
  const el = btn.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const width = Math.max(r.width, 224)
  const left = Math.max(12, Math.min(r.left, window.innerWidth - width - 12))
  const spaceBelow = window.innerHeight - r.bottom - 12
  const spaceAbove = r.top - 12
  const popH = pop.value?.offsetHeight || 320
  if (spaceBelow < popH && spaceAbove > spaceBelow) {
    pos.value = { bottom: `${Math.round(window.innerHeight - r.top + 6)}px`, left: `${Math.round(left)}px`, minWidth: `${Math.round(width)}px` }
  } else {
    pos.value = { top: `${Math.round(r.bottom + 6)}px`, left: `${Math.round(left)}px`, minWidth: `${Math.round(width)}px` }
  }
}

async function toggleOpen() {
  open.value = !open.value
  if (open.value) {
    query.value = ''
    await nextTick()
    updatePos()
    await nextTick()
    updatePos()
    searchInput.value?.focus()
  }
}

function toggleEffort(e: EffortValue) {
  const has = efforts.value.includes(e)
  const next = has ? efforts.value.filter((x) => x !== e) : EFFORT_VALUES.filter((x) => x === e || efforts.value.includes(x))
  efforts.value = next
  if (has && effort.value === e) effort.value = ''
}

function setDefault(v: Effort) {
  effort.value = v
  if (v && !efforts.value.length) efforts.value = [v]
}

function onDoc(e: PointerEvent) {
  const target = e.target as Node | null
  if (target && (btn.value?.contains(target) || pop.value?.contains(target))) return
  open.value = false
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}
function onScroll(e: Event) {
  const target = e.target as Node | null
  if (target && pop.value?.contains(target)) return
  updatePos()
}

watch(open, (o) => {
  if (o) {
    document.addEventListener('pointerdown', onDoc, true)
    document.addEventListener('keydown', onKey, true)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', updatePos)
  } else {
    document.removeEventListener('pointerdown', onDoc, true)
    document.removeEventListener('keydown', onKey, true)
    window.removeEventListener('scroll', onScroll, true)
    window.removeEventListener('resize', updatePos)
  }
})
onUnmounted(() => { open.value = false })
</script>

<template>
  <button ref="btn" type="button" class="aip-epick-btn" :class="{ open, unset: isUnset }" :title="tooltip"
          :aria-expanded="open" @click="toggleOpen">
    <span class="aip-epick-text mono">{{ summary }}</span>
    <Icon name="chevron" class="aip-epick-caret" />
  </button>
  <Teleport to="body">
    <transition name="aip-epop">
      <div v-if="open" ref="pop" class="aip-epop" :style="pos" role="dialog" :aria-label="t('editor.models.effort')">
        <div class="aip-epop-search">
          <Icon name="search" class="aip-epop-sicon" />
          <input ref="searchInput" v-model="query" type="text" class="aip-epop-input"
                 :placeholder="t('editor.models.effortSearch')" />
        </div>
        <div class="aip-epop-list">
          <button v-for="e in shown" :key="e" type="button" class="aip-epop-item"
                  :class="{ on: efforts.includes(e) }" @click="toggleEffort(e)">
            <span class="aip-epop-check"><Icon v-if="efforts.includes(e)" name="check" /></span>
            <span class="mono">{{ e }}</span>
          </button>
          <div v-if="!shown.length" class="aip-epop-empty">{{ t('editor.models.effortSearchEmpty') }}</div>
        </div>
        <div class="aip-epop-foot">
          <span class="aip-epop-flabel">{{ t('editor.models.effortDefaultLabel') }}</span>
          <el-select :model-value="effort" size="small" :teleported="false" class="aip-epop-def"
                     @update:model-value="setDefault($event as Effort)">
            <el-option value="" label="default" />
            <el-option v-for="e in defaultChoices" :key="e" :value="e" :label="e" />
          </el-select>
        </div>
      </div>
    </transition>
  </Teleport>
</template>
