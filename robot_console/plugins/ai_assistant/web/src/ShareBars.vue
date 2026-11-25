<!-- Share of tokens: the top 6 rows as thin bars (only the first one in the accent colour), the rest merged into
     "other". Clicking a row picks it as the filter of the page. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { UsageRow } from './api'
import { compact, shortModel } from './format'
import { t } from './sdk'

const props = defineProps<{ rows: UsageRow[]; by: 'model' | 'profile'; pickable?: boolean }>()
const emit = defineEmits<{ pick: [key: string] }>()
const played = ref(false)
onMounted(() => requestAnimationFrame(() => requestAnimationFrame(() => (played.value = true))))

const tok = (r: UsageRow) => r.input + r.output + r.cache_read + r.cache_write
const lines = computed(() => {
  const sorted = [...props.rows].sort((a, b) => tok(b) - tok(a))
  const all = sorted.reduce((a, r) => a + tok(r), 0)
  if (!all) return []
  const top = sorted.slice(0, 6).map((r) => ({
    key: r.key, other: false,
    name: props.by === 'model' ? shortModel(r.label || r.key) : (r.label || r.key), full: r.label || r.key, value: tok(r),
  }))
  const rest = sorted.slice(6).reduce((a, r) => a + tok(r), 0)
  if (rest) top.push({ key: '', other: true, name: t('usage.share.other'), full: '', value: rest })
  return top.map((l) => ({ ...l, pct: (l.value / all) * 100 }))
})
</script>

<template>
  <div class="aiu-share" :class="{ played }">
    <div v-if="!lines.length" class="aiu-share-empty">{{ t('usage.share.empty') }}</div>
    <button
      v-for="(l, i) in lines" :key="l.key || 'other'" type="button" class="aiu-share-row"
      :disabled="!pickable || l.other" :title="l.full" @click="emit('pick', l.key)"
    >
      <span class="aiu-share-name">{{ l.name }}</span>
      <span class="aiu-share-track"><i :class="{ first: i === 0 && !l.other }" :style="{ width: `${l.pct}%`, transitionDelay: `${i * 20}ms` }" /></span>
      <span class="aiu-share-val"><b>{{ l.pct.toFixed(1) }}%</b> · {{ compact(l.value) }}</span>
    </button>
  </div>
</template>
