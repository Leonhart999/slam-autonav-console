<!-- Access picker under the input (like the approval modes of Codex / the permission modes of Claude Code): what the
     assistant may do for the next questions. Kept in this browser (localStorage) and sent with every question; the
     backend enforces it. -->
<script setup lang="ts">
import { ref } from 'vue'
import { ACCESS_LEVELS, type Access } from './api'
import { t } from './sdk'
import { usePanelMenu } from './popover'
import Icon from './Icon.vue'
import SwapText from './SwapText.vue'

const access = defineModel<Access>({ required: true })
const ICONS: Record<Access, 'chat' | 'eye' | 'shield' | 'bolt'> = { chat: 'chat', read: 'eye', ask: 'shield', auto: 'bolt' }

const root = ref<HTMLElement | null>(null)
const { open, menuStyle } = usePanelMenu(root, 300)
const dir = ref(1)

function pick(a: Access) {
  dir.value = ACCESS_LEVELS.indexOf(a) >= ACCESS_LEVELS.indexOf(access.value) ? 1 : -1
  access.value = a
  open.value = false
}
</script>

<template>
  <div ref="root" class="aia-pick">
    <button type="button" class="aia-chipbtn" :class="[{ open }, `acc-${access}`]" :title="t('access.title')"
            :aria-expanded="open" @click="open = !open">
      <Icon :name="ICONS[access]" /><SwapText class="aia-chip-text" :text="t(`access.${access}.name`)" :dir="dir" />
    </button>
    <transition name="aia-pop">
      <div v-if="open" class="aia-menu up aia-access-menu" role="menu" :style="menuStyle">
        <div class="aia-menu-cap">{{ t('access.title') }}</div>
        <button v-for="a in ACCESS_LEVELS" :key="a" type="button" role="menuitemradio" class="aia-acc"
                :class="[{ on: a === access }, `acc-${a}`]" :aria-checked="a === access" @click="pick(a)">
          <Icon :name="ICONS[a]" class="aia-acc-icon" />
          <span class="aia-acc-text">
            <span class="aia-acc-name">{{ t(`access.${a}.name`) }}</span>
            <span class="aia-acc-desc">{{ t(`access.${a}.desc`) }}</span>
          </span>
          <Icon v-if="a === access" name="check" class="aia-menu-check" />
        </button>
      </div>
    </transition>
  </div>
</template>
