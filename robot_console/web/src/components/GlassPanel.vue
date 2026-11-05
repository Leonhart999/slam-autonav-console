<!-- 磨砂玻璃面板：标题行（左边标题，右边 extra 插槽）+ 内容。左上、右下有 HUD 风格的角标
     flush：内容不留内边距（放表格时用） -->
<script setup lang="ts">
defineProps<{ title?: string; flush?: boolean }>()
</script>

<template>
  <section class="panel glass">
    <header v-if="title || $slots.extra" class="head">
      <h3 class="title"><i class="tick" />{{ title }}</h3>
      <div class="extra"><slot name="extra" /></div>
    </header>
    <div class="body" :class="{ flush }"><slot /></div>
  </section>
</template>

<style scoped>
.panel {
  position: relative;
  min-width: 0;
  border-radius: var(--radius);
}
/* 角标：左上、右下各一个 L 形短线 */
.panel::before,
.panel::after {
  content: '';
  position: absolute;
  width: 14px;
  height: 14px;
  pointer-events: none;
  border-color: var(--accent);
  border-style: solid;
  opacity: 0.55;
}
.panel::before {
  top: 7px;
  left: 7px;
  border-width: 1.5px 0 0 1.5px;
  border-top-left-radius: 6px;
}
.panel::after {
  right: 7px;
  bottom: 7px;
  border-width: 0 1.5px 1.5px 0;
  border-bottom-right-radius: 6px;
}
.head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px 10px 20px;
}
.title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.tick {
  width: 3px;
  height: 14px;
  border-radius: 2px;
  background: var(--accent-grad);
  box-shadow: 0 0 8px rgba(6, 182, 212, 0.6);
}
.extra {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}
.body {
  padding: 4px 18px 18px;
}
.body.flush {
  padding: 0 8px 10px;
}
</style>
