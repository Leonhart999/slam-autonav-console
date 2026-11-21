<!-- "Helpful" mark on an answer (kept in this browser only). A press squeezes the thumb, and a light spring
     (stiffness 380, damping 18) lets it bounce back once. -->
<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import Icon from './Icon.vue'
import { LIKE_SPRING, createSpring } from './spring'
import { t } from './sdk'

defineProps<{ liked: boolean }>()
const emit = defineEmits<{ toggle: [] }>()
const icon = ref<HTMLElement | null>(null)
const spring = createSpring(1, LIKE_SPRING, (s) => { if (icon.value) icon.value.style.scale = String(s) })

function press() {
  emit('toggle')
  spring.to(1, { from: 0.72, velocity: 0 })
}

onUnmounted(() => spring.stop())
</script>

<template>
  <button type="button" class="aia-ibtn aia-like" :class="{ on: liked }" :aria-pressed="liked"
          :title="liked ? t('liked') : t('like')" :aria-label="liked ? t('liked') : t('like')" @click="press">
    <span ref="icon" class="aia-like-icon"><Icon name="like" :filled="liked" /></span>
  </button>
</template>
