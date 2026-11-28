<!-- Settings row with a capsule of choices (ClipDeck's choice row; with `custom` it is a preset row that shows
     "Custom" when the values match no preset) -->
<script setup lang="ts">
defineProps<{
  label: string
  description?: string
  value: string | null
  options: { value: string; label: string; disabled?: boolean }[]
  preset?: boolean
  customLabel?: string
  disabled?: boolean
}>()
defineEmits<{ change: [value: string] }>()
</script>

<template>
  <div class="aia-field-row aia-choice-row" :class="{ 'aia-preset-row': preset, 'is-disabled': disabled }">
    <div class="aia-field-label"><strong>{{ label }}</strong><span v-if="description">{{ description }}</span></div>
    <div class="aia-choice-group" role="radiogroup" :aria-label="label">
      <button v-for="o in options" :key="o.value" type="button" role="radio" class="aia-choice"
              :class="{ 'is-selected': o.value === value }" :aria-checked="o.value === value"
              :disabled="disabled || o.disabled" @click="$emit('change', o.value)">
        {{ o.label }}
      </button>
      <span v-if="preset && value === null" class="aia-choice is-selected is-custom" role="radio" aria-checked="true">{{ customLabel }}</span>
    </div>
  </div>
</template>
