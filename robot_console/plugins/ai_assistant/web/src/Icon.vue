<!-- Inline 1em icons. They sit in the text line and are lowered by 0.125em, so they line up with the text next to them
     (a box centred on the line looks too high next to Latin and CJK glyphs). -->
<script setup lang="ts">
defineProps<{
  name: 'spark' | 'close' | 'chevron' | 'info' | 'copy' | 'check' | 'like' | 'plus' | 'bolt' | 'download' | 'trash' | 'gear'
    | 'chat' | 'eye' | 'shield' | 'gauge' | 'search' | 'history' | 'pin' | 'edit' | 'refresh' | 'key' | 'upload'
    | 'link' | 'power' | 'external' | 'drop' | 'sliders' | 'server' | 'keyboard' | 'window' | 'wrench' | 'back' | 'save'
    | 'reset' | 'compose'
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
      <path class="aia-i-line"
            d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.6.79 1 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
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
    <template v-else-if="name === 'history'">
      <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 9" class="aia-i-line" />
      <path d="M4.5 4.5V9H9M12 8v4.5l3 2" class="aia-i-line" />
    </template>
    <path v-else-if="name === 'pin'" class="aia-i-line" :fill="filled ? 'currentColor' : 'none'"
          d="M9 4h6l-1 6 3.5 3v1.5h-11V13L10 10zM12 14.5V20" />
    <path v-else-if="name === 'edit'" class="aia-i-line" d="M4.5 19.5l1-4L15.5 5.5a2.1 2.1 0 0 1 3 3l-10 10zM13.5 7.5l3 3" />
    <template v-else-if="name === 'refresh'">
      <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3L19.5 9" class="aia-i-line" />
      <path d="M19.5 4.5V9H15" class="aia-i-line" />
    </template>
    <template v-else-if="name === 'key'">
      <circle cx="8" cy="15" r="4" class="aia-i-line" />
      <path d="M11 12l8.5-8.5M16.5 6.5l2.5 2.5M14 9l2 2" class="aia-i-line" />
    </template>
    <path v-else-if="name === 'upload'" d="M12 16V5m0 0L7.5 9.5M12 5l4.5 4.5M5 19.5h14" class="aia-i-line" />
    <path v-else-if="name === 'link'" class="aia-i-line"
          d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
    <template v-else-if="name === 'power'">
      <path d="M12 3.5v8" class="aia-i-line" />
      <path d="M7 6.5a7.5 7.5 0 1 0 10 0" class="aia-i-line" />
    </template>
    <path v-else-if="name === 'drop'" d="M12 3.5c3.6 4.1 6 7.4 6 10.5a6 6 0 0 1-12 0c0-3.1 2.4-6.4 6-10.5zM9 14.5a3 3 0 0 0 3 3" class="aia-i-line" />
    <template v-else-if="name === 'sliders'">
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" class="aia-i-line" />
      <circle cx="15" cy="7" r="2" class="aia-i-line" />
      <circle cx="9" cy="17" r="2" class="aia-i-line" />
    </template>
    <template v-else-if="name === 'server'">
      <rect x="4" y="4" width="16" height="7" rx="2" class="aia-i-line" />
      <rect x="4" y="13" width="16" height="7" rx="2" class="aia-i-line" />
      <path d="M8 7.5h.01M8 16.5h.01" class="aia-i-line" />
    </template>
    <template v-else-if="name === 'keyboard'">
      <rect x="3" y="6" width="18" height="12" rx="2.5" class="aia-i-line" />
      <path d="M7 10h.01M10.5 10h.01M14 10h.01M17 10h.01M7.5 14h9" class="aia-i-line" />
    </template>
    <template v-else-if="name === 'window'">
      <rect x="3.5" y="5" width="17" height="14" rx="2.5" class="aia-i-line" />
      <path d="M7 8.5h.01M9.5 8.5h.01" class="aia-i-line" />
    </template>
    <path v-else-if="name === 'wrench'" class="aia-i-line"
          d="M15.5 4.2a4.5 4.5 0 0 0-5.6 5.9L4.2 15.8a2 2 0 0 0 2.9 2.9l5.7-5.7a4.5 4.5 0 0 0 5.9-5.6l-2.6 2.6-2.4-.6-.6-2.4z" />
    <path v-else-if="name === 'back'" d="M15 5l-7 7 7 7" class="aia-i-line" />
    <template v-else-if="name === 'save'">
      <path d="M5 4.5h11l3.5 3.5v11a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1v-13.5a1 1 0 0 1 .5-1z" class="aia-i-line" />
      <path d="M8 4.5v4h7v-4M8 20v-6h8v6" class="aia-i-line" />
    </template>
    <template v-else-if="name === 'reset'">
      <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 9" class="aia-i-line" />
      <path d="M4.5 4.5V9H9" class="aia-i-line" />
    </template>
    <template v-else-if="name === 'compose'">
      <path d="M11 4.5H6A1.5 1.5 0 0 0 4.5 6v12A1.5 1.5 0 0 0 6 19.5h12a1.5 1.5 0 0 0 1.5-1.5v-5" class="aia-i-line" />
      <path d="M17.5 3.5a2.1 2.1 0 0 1 3 3L13 14l-4 1 1-4z" class="aia-i-line" />
    </template>
    <path v-else-if="name === 'external'" d="M13.5 4.5h6v6M19.5 4.5L11 13M17 14v4.5a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1H10" class="aia-i-line" />
    <path
      v-else-if="name === 'like'" class="aia-i-line" :fill="filled ? 'currentColor' : 'none'"
      d="M7.5 10.5v9H5a1.5 1.5 0 0 1-1.5-1.5v-6A1.5 1.5 0 0 1 5 10.5zm0 0l3.6-6.3a1.9 1.9 0 0 1 3.5 1.3l-.7 3.5h4.6a2 2 0 0 1 2 2.4l-1.3 6.5a2 2 0 0 1-2 1.6H7.5"
    />
  </svg>
</template>
