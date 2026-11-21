<!-- One header component of a plugin. An error inside it hides only that component (reported on the Plugins page)
     instead of breaking the console's header. -->
<script setup lang="ts">
import { onErrorCaptured } from 'vue'
import type { Component } from 'vue'
import { reportCrash } from '@/plugins/host'

const props = defineProps<{ pluginId: string; component: Component }>()

onErrorCaptured((err) => {
  console.error(`plugin ${props.pluginId}:`, err)
  reportCrash(props.pluginId, err instanceof Error ? err.message : String(err))
  return false
})
</script>

<template>
  <component :is="component" />
</template>
