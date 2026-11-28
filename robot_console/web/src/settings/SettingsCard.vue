<!-- A titled group of setting rows: icon chip + title + where the values are kept (scope badge), then the card. -->
<script setup lang="ts">
import type { Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { Document, Monitor } from '@element-plus/icons-vue'
import { settingsMessages } from './messages'

defineProps<{ title: string; icon?: Component; scope?: 'browser' | 'console' | 'consoleMixed'; hint?: string }>()
const { t } = useI18n({ messages: settingsMessages })
</script>

<template>
  <section class="group">
    <header class="head">
      <span v-if="icon" class="ico"><el-icon :size="15"><component :is="icon" /></el-icon></span>
      <h2>{{ title }}</h2>
      <span v-if="scope" class="scope" :class="scope === 'browser' ? 'local' : 'yaml'">
        <el-icon :size="11"><component :is="scope === 'browser' ? Monitor : Document" /></el-icon>
        {{ t(`settings.scope.${scope}`) }}
      </span>
      <slot name="extra" />
    </header>
    <p v-if="hint" class="hint">{{ hint }}</p>
    <div class="card glass"><slot /></div>
  </section>
</template>

<style scoped>
.group { margin-top: 26px; }
.group:first-child { margin-top: 2px; }
.head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin: 0 4px 10px;
}
.ico {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  color: var(--accent);
  background: linear-gradient(135deg, rgba(var(--accent-rgb), 0.18), rgba(var(--accent-rgb), 0.06));
  box-shadow: inset 0 0 0 1px rgba(var(--accent-rgb), 0.18);
}
.head h2 {
  margin: 0;
  font-size: 15.5px;
  font-weight: 650;
  letter-spacing: 0.01em;
  color: var(--ink);
}
.scope {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  padding: 2px 9px 2px 7px;
  border-radius: 999px;
}
.scope.local { color: var(--ink-3); background: rgba(var(--tint-rgb), 0.06); }
.scope.yaml { color: var(--accent); background: rgba(var(--accent-rgb), 0.1); }
.hint {
  margin: -2px 4px 10px 40px;
  font-size: 12px;
  color: var(--ink-3);
}
.card {
  position: relative;
  border-radius: var(--radius);
  overflow: hidden;
}
/* thin light edge along the top, like the other panels of the console */
.card::before {
  content: '';
  position: absolute;
  inset: 0 0 auto;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(var(--accent-rgb), 0.35), transparent);
  pointer-events: none;
}
html.density-compact .group:not(:first-child) { margin-top: 18px; }
</style>
