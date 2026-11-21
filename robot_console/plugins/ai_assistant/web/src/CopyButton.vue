<!-- Copies an answer's Markdown. The icon turns into a check mark for 2.5 s, then goes back by itself. -->
<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import Icon from './Icon.vue'
import { t } from './sdk'

const props = defineProps<{ text: string }>()
const CHECK_MS = 2500
const done = ref(false)
let timer = 0

async function writeClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    return
  } catch {
    // no async clipboard (plain http on another host): fall back to a hidden textarea
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.cssText = 'position:fixed;opacity:0;pointer-events:none'
  document.body.append(area)
  area.select()
  const ok = document.execCommand('copy')
  area.remove()
  if (!ok) throw new Error('copy failed')
}

async function copy() {
  try {
    await writeClipboard(props.text)
  } catch {
    return
  }
  done.value = true
  window.clearTimeout(timer)
  timer = window.setTimeout(() => (done.value = false), CHECK_MS)
}

onUnmounted(() => window.clearTimeout(timer))
</script>

<template>
  <button type="button" class="aia-ibtn aia-copy" :class="{ done }" :title="done ? t('copied') : t('copy')"
          :aria-label="done ? t('copied') : t('copy')" @click="copy">
    <span class="aia-swap">
      <Icon name="copy" class="aia-swap-a" />
      <Icon name="check" class="aia-swap-b" />
    </span>
  </button>
</template>
