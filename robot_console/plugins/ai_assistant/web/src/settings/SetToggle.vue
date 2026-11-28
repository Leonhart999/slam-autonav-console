<!-- Settings row with an on / off switch (ClipDeck's Toggle + field row) -->
<script setup lang="ts">
defineProps<{ label: string; description?: string; checked: boolean; disabled?: boolean }>()
defineEmits<{ toggle: [] }>()
</script>

<template>
  <div class="aia-field-row" :class="{ 'is-disabled': disabled }">
    <div class="aia-field-label"><strong>{{ label }}</strong><span v-if="description">{{ description }}</span></div>
    <button type="button" class="aia-toggle" :class="{ 'is-on': checked }" :aria-label="label" :aria-pressed="checked"
            :disabled="disabled" @click="$emit('toggle')">
      <span class="aia-toggle-knob" />
    </button>
  </div>
</template>
