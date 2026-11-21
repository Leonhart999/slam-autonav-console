<!-- Inline 1em icons. They sit in the text line and are lowered by 0.125em, so they line up with the text next to them
     (a box centred on the line looks too high next to Latin and CJK glyphs). -->
<script setup lang="ts">
defineProps<{
  name: 'spark' | 'close' | 'chevron' | 'info' | 'copy' | 'check' | 'like' | 'plus' | 'bolt' | 'download' | 'trash' | 'gear'
    | 'chat' | 'eye' | 'shield' | 'gauge' | 'search'
  filled?: boolean
}>()
</script>

<template>
  <svg class="aia-i" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <template v-if="name === 'spark'">
      <path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6z" fill="currentColor" />
      <path d="M18.5 14l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" fill="currentColor" />
    </template>
    <path v-else-if="name === 'close'" d="M6 6l12 12M18 6L6 18" class="aia-i-line" />
    <path v-else-if="name === 'chevron'" d="M9 5l7 7-7 7" class="aia-i-line" />
    <path v-else-if="name === 'plus'" d="M12 5v14M5 12h14" class="aia-i-line" />
    <template v-else-if="name === 'info'">
      <circle cx="12" cy="12" r="9" class="aia-i-line" />
      <path d="M12 11v6M12 7.5v.01" class="aia-i-line" />
    </template>
    <template v-else-if="name === 'copy'">
      <rect x="8.5" y="8.5" width="12" height="12" rx="2.5" class="aia-i-line" />
      <path d="M15.5 8.5V6a2.5 2.5 0 0 0-2.5-2.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5" class="aia-i-line" />
    </template>
    <path v-else-if="name === 'check'" d="M5 12.5l4.5 4.5L19 7.5" class="aia-i-line" />
    <path v-else-if="name === 'bolt'" d="M13 3L5 13.5h6L10 21l8-10.5h-6z" class="aia-i-line" />
    <path v-else-if="name === 'download'" d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 19.5h14" class="aia-i-line" />
    <path v-else-if="name === 'trash'" d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13M10 11v5.5M14 11v5.5" class="aia-i-line" />
    <template v-else-if="name === 'gear'">
      <circle cx="12" cy="12" r="3" class="aia-i-line" />
      <path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7" class="aia-i-line" />
    </template>
    <path v-else-if="name === 'chat'" class="aia-i-line"
          d="M5 5h14a1.5 1.5 0 0 1 1.5 1.5v8.5a1.5 1.5 0 0 1-1.5 1.5h-7.5L7 20v-3.5H5A1.5 1.5 0 0 1 3.5 15V6.5A1.5 1.5 0 0 1 5 5z" />
    <template v-else-if="name === 'eye'">
      <path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z" class="aia-i-line" />
      <circle cx="12" cy="12" r="2.8" class="aia-i-line" />
    </template>
    <template v-else-if="name === 'shield'">
      <path d="M12 3l7.5 3v5.5c0 4.5-3.2 8.2-7.5 9.5-4.3-1.3-7.5-5-7.5-9.5V6z" class="aia-i-line" />
      <path d="M9 12l2 2 4-4" class="aia-i-line" />
    </template>
    <template v-else-if="name === 'gauge'">
      <path d="M4.2 17a8.5 8.5 0 1 1 15.6 0" class="aia-i-line" />
      <path d="M12 14l4-4.5" class="aia-i-line" />
    </template>
    <template v-else-if="name === 'search'">
      <circle cx="11" cy="11" r="6.5" class="aia-i-line" />
      <path d="M16 16l4.5 4.5" class="aia-i-line" />
    </template>
    <path
      v-else-if="name === 'like'" class="aia-i-line" :fill="filled ? 'currentColor' : 'none'"
      d="M7.5 10.5v9H5a1.5 1.5 0 0 1-1.5-1.5v-6A1.5 1.5 0 0 1 5 10.5zm0 0l3.6-6.3a1.9 1.9 0 0 1 3.5 1.3l-.7 3.5h4.6a2 2 0 0 1 2 2.4l-1.3 6.5a2 2 0 0 1-2 1.6H7.5"
    />
  </svg>
</template>
