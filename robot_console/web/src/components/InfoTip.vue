<script setup lang="ts">
// Small circled "i" that shows an explanation on hover or keyboard focus.
withDefaults(defineProps<{ content: string; placement?: 'top' | 'right' | 'bottom' | 'left' }>(), { placement: 'top' })
</script>
<template>
  <el-tooltip :content="content" :placement="placement" popper-class="info-tip" :show-after="150">
    <!-- prevent/stop: clicking the icon inside a label or option must not toggle the control -->
    <span class="info" tabindex="0" :aria-label="content" @click.prevent.stop>i</span>
  </el-tooltip>
</template>
<style scoped>
.info{display:inline-grid;place-items:center;flex:none;width:14px;height:14px;margin-left:5px;border:1px solid var(--ink-3);border-radius:50%;color:var(--ink-3);font:italic 600 10px/1 Georgia,serif;cursor:help;vertical-align:-2px;transition:color .15s,border-color .15s}
.info:hover,.info:focus-visible{color:var(--accent);border-color:var(--accent);outline:none}
</style>
<style>
/* The tooltip is teleported to <body>, so this block is not scoped. */
.info-tip.el-popper{max-width:320px;line-height:1.6;font-size:12px}
</style>
