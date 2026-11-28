<!-- Settings row that records a key combination: focus it and press the keys (Backspace / Delete clears it) -->
<script setup lang="ts">
import { ref } from 'vue'
import { comboOf } from '../prefs'

defineProps<{ label: string; value: string; placeholder: string }>()
const emit = defineEmits<{ change: [value: string] }>()
const recording = ref(false)

function onKey(e: KeyboardEvent) {
  if (e.key === 'Tab') return
  e.preventDefault()
  e.stopPropagation()
  if (e.key === 'Escape') { (e.target as HTMLElement).blur(); return }
  if ((e.key === 'Backspace' || e.key === 'Delete') && !e.ctrlKey && !e.altKey && !e.metaKey) { emit('change', ''); return }
  const c = comboOf(e)
  if (c) emit('change', c)
}
</script>

<template>
  <label class="aia-field-row aia-shortcut-row">
    <span class="aia-field-label"><strong>{{ label }}</strong></span>
    <input class="aia-field-input" :class="{ recording }" readonly :value="recording && !value ? placeholder : value"
           :placeholder="placeholder" @focus="recording = true" @blur="recording = false" @keydown="onKey">
  </label>
</template>
