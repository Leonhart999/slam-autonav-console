<!-- 虚拟摇杆：按住圆盘拖动，松开回中。
     v-model 是摇杆位置 {x, y}，范围 -1..1：x 向右为正，y 向上为正（前进） -->
<script setup lang="ts">
import { ref } from 'vue'

const model = defineModel<{ x: number; y: number }>({ required: true })
const props = withDefaults(defineProps<{ size?: number; disabled?: boolean }>(), { size: 148, disabled: false })

const pad = ref<HTMLDivElement | null>(null)
const dragging = ref(false)
const KNOB = 52

function update(ev: PointerEvent) {
  const el = pad.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const reach = (props.size - KNOB) / 2 // 摇杆头中心能走的最大半径（px）
  let dx = (ev.clientX - (r.left + r.width / 2)) / reach
  let dy = -(ev.clientY - (r.top + r.height / 2)) / reach
  const len = Math.hypot(dx, dy)
  if (len > 1) {
    dx /= len
    dy /= len
  }
  model.value = { x: dx, y: dy }
}

function down(ev: PointerEvent) {
  if (props.disabled) return
  pad.value?.setPointerCapture(ev.pointerId) // 拖出圆盘也继续跟随
  dragging.value = true
  update(ev)
}
function move(ev: PointerEvent) {
  if (dragging.value) update(ev)
}
function up() {
  dragging.value = false
  model.value = { x: 0, y: 0 }
}
</script>

<template>
  <div
    ref="pad" class="pad" :class="{ dragging, disabled }" :style="{ width: `${size}px`, height: `${size}px` }"
    @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="up" @lostpointercapture="up"
  >
    <i class="ring r1" />
    <i class="ring r2" />
    <i class="axis h" />
    <i class="axis v" />
    <span class="mark up">▲</span>
    <span class="mark down">▼</span>
    <span class="mark left">◀</span>
    <span class="mark right">▶</span>
    <div
      class="knob"
      :style="{ transform: `translate(${(model.x * (size - KNOB)) / 2}px, ${(-model.y * (size - KNOB)) / 2}px)` }"
    />
  </div>
</template>

<style scoped>
.pad {
  position: relative;
  flex: none;
  border-radius: 50%;
  touch-action: none; /* 触屏拖动时不滚动页面 */
  user-select: none;
  cursor: grab;
  background: radial-gradient(circle, rgba(255, 255, 255, 0.95) 0%, rgba(236, 244, 250, 0.9) 70%);
  box-shadow: inset 0 0 0 1px rgba(8, 145, 178, 0.22), inset 0 6px 18px -8px rgba(20, 60, 120, 0.25),
    0 8px 20px -14px rgba(8, 145, 178, 0.8);
}
.pad.dragging {
  cursor: grabbing;
  box-shadow: inset 0 0 0 1.5px rgba(8, 145, 178, 0.5), inset 0 6px 18px -8px rgba(20, 60, 120, 0.25),
    0 0 0 6px rgba(6, 182, 212, 0.1);
}
.pad.disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
.ring,
.axis {
  position: absolute;
  pointer-events: none;
}
.ring {
  border-radius: 50%;
  border: 1px dashed rgba(8, 145, 178, 0.25);
}
.r1 {
  inset: 14%;
}
.r2 {
  inset: 32%;
}
.axis {
  background: rgba(8, 145, 178, 0.12);
}
.axis.h {
  left: 10%;
  right: 10%;
  top: 50%;
  height: 1px;
}
.axis.v {
  top: 10%;
  bottom: 10%;
  left: 50%;
  width: 1px;
}
.mark {
  position: absolute;
  font-size: 9px;
  color: rgba(8, 145, 178, 0.45);
  pointer-events: none;
}
.up { top: 5px; left: 50%; transform: translateX(-50%); }
.down { bottom: 5px; left: 50%; transform: translateX(-50%); }
.left { left: 6px; top: 50%; transform: translateY(-50%); }
.right { right: 6px; top: 50%; transform: translateY(-50%); }
.knob {
  position: absolute;
  top: calc(50% - 26px);
  left: calc(50% - 26px);
  width: 52px;
  height: 52px;
  border-radius: 50%;
  pointer-events: none;
  background: radial-gradient(circle at 35% 30%, #67e8f9, #0891b2 55%, #2563eb);
  box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.85), 0 6px 16px -4px rgba(37, 99, 235, 0.65);
  transition: transform 0.12s ease-out;
}
.dragging .knob {
  transition: none; /* 拖动时紧跟手指，松开时才有回中动画 */
  box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.9), 0 0 18px rgba(6, 182, 212, 0.75);
}
</style>
