<!-- Playback page, step ①: the rosbag2 folders found under playback.bag_dirs as radio cards.
     Shows what each bag holds (point cloud / IMU / ground truth) so the SLAM choice in step ② makes sense.
     Each card has a delete button (the bag moves to the trash of its folder); the dashed card at the end opens
     the upload dialog; the bar shows how much the trash holds and empties it. The page does the API calls. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { fmtBytes, fmtDuration } from '@/format'
import type { BagInfo } from '@/mapping/stream'

const props = defineProps<{
  bags: BagInfo[]
  roots: string[]
  loading: boolean
  locked: boolean // a playback is running: the bag cannot change
  compact?: boolean // only the chosen bag is listed (SLAM or playback running): no bar, upload or delete
  trashSize?: number // bytes held by the bag folders' trash
  deleting?: string // path of the bag being deleted
  connected?: boolean
}>()
const selected = defineModel<string>({ required: true })
defineEmits<{ refresh: []; upload: []; delete: [bag: BagInfo]; emptyTrash: [] }>()
const { t } = useI18n()

const scans = (b: BagInfo) => b.topics.filter((x) => b.points.includes(x.name)).reduce((n, x) => Math.max(n, x.count), 0)
const editable = computed(() => !props.compact && props.connected !== false)
</script>

<template>
  <div class="bags">
    <div v-if="!compact" class="bar">
      <span class="k">{{ t('playback.bag.found', { n: bags.length }) }}</span>
      <span class="actions">
        <el-button
          v-if="trashSize" size="small" text :title="t('playback.bag.emptyTrash')" :disabled="!editable"
          @click="$emit('emptyTrash')"
        >
          <el-icon><Delete /></el-icon>{{ t('playback.bag.trash', { size: fmtBytes(trashSize) }) }}
        </el-button>
        <el-button size="small" text :loading="loading" @click="$emit('refresh')">
          <el-icon v-if="!loading"><Refresh /></el-icon>{{ t('playback.bag.refresh') }}
        </el-button>
      </span>
    </div>
    <div class="list" role="radiogroup" :aria-label="t('playback.bag.title')">
      <div v-for="b in bags" :key="b.path" class="bag-row">
        <button
          type="button" class="bag" :class="{ on: selected === b.path, deletable: editable }"
          role="radio" :aria-checked="selected === b.path"
          :disabled="!b.supported || (locked && selected !== b.path)" @click="selected = b.path"
        >
          <span class="radio" />
          <span class="text">
            <span class="name mono" :title="b.path">{{ b.name }}</span>
            <span class="meta">{{ fmtDuration(b.duration) }} · {{ fmtBytes(b.size) }} · {{ t('playback.bag.scans', { n: scans(b) }) }}</span>
            <span class="tags">
              <span class="tag" :class="{ off: !b.points.length }">{{ b.points.length ? b.points[0] : t('playback.bag.noCloud') }}</span>
              <span class="tag" :class="{ off: !b.imu.length }">{{ b.imu.length ? 'IMU' : t('playback.bag.noImu') }}</span>
              <span v-if="b.has_gt" class="tag">{{ t('playback.bag.gt') }}</span>
              <span v-if="!b.supported" class="tag bad">{{ t('playback.bag.storage', { storage: b.storage }) }}</span>
            </span>
          </span>
        </button>
        <el-button
          v-if="editable" class="bag-del" circle text size="small"
          :title="t('playback.bag.delete')" :aria-label="`${t('playback.bag.delete')} ${b.name}`"
          :loading="deleting === b.path" :disabled="!!deleting" @click="$emit('delete', b)"
        >
          <el-icon v-if="deleting !== b.path"><Delete /></el-icon>
        </el-button>
      </div>
      <p v-if="!bags.length && !loading" class="empty">{{ t('playback.bag.empty', { dirs: roots.join(t('estop.listSep')) }) }}</p>
    </div>
    <button v-if="editable" type="button" class="bag upload" @click="$emit('upload')">
      <el-icon class="upload-icon" :size="15"><Upload /></el-icon>
      <span class="text">
        <span class="name">{{ t('playback.bag.upload') }}</span>
        <span class="meta">{{ t('playback.bag.uploadDesc') }}</span>
      </span>
    </button>
  </div>
</template>

<style scoped>
.bags {
  margin-bottom: 10px;
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.k {
  color: var(--ink-3);
  font-size: 12px;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 280px;
  overflow: auto;
  padding: 2px;
}
.bag {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  box-sizing: border-box;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 10px;
  font: inherit;
  text-align: left;
  color: inherit;
  background: rgba(255, 255, 255, 0.55);
  box-shadow: inset 0 0 0 1px var(--hairline);
  cursor: pointer;
  transition: background 0.2s, box-shadow 0.2s;
}
.bag:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.9);
}
.bag.on {
  background: #fff;
  box-shadow: inset 0 0 0 1.5px rgba(8, 145, 178, 0.55), 0 6px 16px -10px rgba(8, 145, 178, 0.7);
}
.bag:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.bag:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.radio {
  flex: none;
  width: 14px;
  height: 14px;
  margin-top: 2px;
  border-radius: 50%;
  box-shadow: inset 0 0 0 1.5px var(--idle);
  transition: box-shadow 0.2s;
}
.bag.on .radio {
  box-shadow: inset 0 0 0 4px var(--accent);
}
.text {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}
.name {
  font-size: 12.5px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta {
  font-size: 11.5px;
  color: var(--ink-3);
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.tag {
  padding: 0 6px;
  border-radius: 5px;
  font-size: 11px;
  line-height: 18px;
  color: var(--accent);
  background: rgba(8, 145, 178, 0.08);
}
.tag.off {
  color: var(--ink-3);
  background: var(--hairline);
}
.tag.bad {
  color: var(--bad);
  background: color-mix(in srgb, var(--bad) 10%, transparent);
}
.empty {
  margin: 4px 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
  word-break: break-all;
}
.actions {
  display: inline-flex;
  align-items: center;
}
.actions .el-button + .el-button {
  margin-left: 2px;
}
/* delete button in the card's top-right corner (outside the card button, no nesting) */
.bag-row {
  position: relative;
}
.bag.deletable {
  padding-right: 34px;
}
.bag-del {
  position: absolute;
  top: 5px;
  right: 5px;
  color: var(--ink-3);
}
.bag-del:hover:not(.is-disabled) {
  color: var(--bad);
}
.bag.upload {
  margin-top: 6px;
  background: transparent;
  box-shadow: none;
  border: 1px dashed rgba(122, 135, 157, 0.5);
}
.bag.upload:hover {
  background: rgba(255, 255, 255, 0.7);
}
.bag.upload .name {
  font-family: inherit;
  color: var(--ink-2);
}
.upload-icon {
  flex: none;
  margin-top: 1px;
  color: var(--ink-3);
}
</style>
