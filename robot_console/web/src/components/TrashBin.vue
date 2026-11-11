<!-- Recycle bin used by every page that deletes things (maps, run records, bags): one trigger button and one
     dialog with the same columns, actions and confirmations. The page loads the entries and does the API calls;
     this component only confirms permanent deletions and emits.
     Entries: name (mono), optional detail (type, group size, original folder), deleted time, size;
     row actions restore / delete permanently; footer: total, open folder, empty. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { fmtBytes } from '@/format'
import { openFolder } from '@/openFolder'

export interface TrashItem {
  id: string
  name: string
  detail?: string
  deleted: string // ISO time (UTC) or "YYYY-MM-DD HH:MM:SS" local time
  size: number
  restorable: boolean
}

const props = defineProps<{
  title: string // dialog title, e.g. "Map recycle bin"
  entries: TrashItem[]
  loading?: boolean
  busy?: string // id being restored / deleted, '*' while emptying
  dirs: string[] // trash folders on disk (open folder); more than one gives a menu
  disabled?: boolean // trigger and actions off (e.g. backend disconnected)
}>()
const open = defineModel<boolean>({ default: false })
const emit = defineEmits<{ open: []; restore: [id: string]; purge: [id: string]; empty: [] }>()
const { t } = useI18n()

const total = computed(() => props.entries.reduce((n, e) => n + e.size, 0))
const size = (n: number) => (n > 0 ? fmtBytes(n) : '0 B') // fmtBytes never goes below 1 kB
const hasDetail = computed(() => props.entries.some((e) => e.detail))
const locked = computed(() => props.disabled || !!props.busy)

const pad = (n: number) => String(n).padStart(2, '0')
function fmtDeleted(s: string) {
  if (!s.includes('T')) return s
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function show() {
  open.value = true
  emit('open')
}

async function confirmed(text: string, ok: string) {
  try {
    await ElMessageBox.confirm(text, props.title, {
      type: 'warning', confirmButtonText: ok, cancelButtonText: t('trashBin.cancel'),
      confirmButtonClass: 'el-button--danger',
    })
    return true
  } catch {
    return false
  }
}
async function purge(e: TrashItem) {
  if (await confirmed(t('trashBin.purgeConfirm', { name: e.name, size: size(e.size) }), t('trashBin.purge'))) emit('purge', e.id)
}
async function empty() {
  if (await confirmed(t('trashBin.emptyConfirm', { n: props.entries.length, size: size(total.value) }), t('trashBin.emptyAll'))) emit('empty')
}
</script>

<template>
  <el-button size="small" :disabled="disabled" :title="t('trashBin.openHint')" @click="show">
    <el-icon><Delete /></el-icon><span>{{ t('trashBin.button') }}</span>
  </el-button>

  <el-dialog v-model="open" :title="title" width="min(860px, 94vw)" append-to-body>
    <el-table
      v-loading="loading" :data="entries" size="small" max-height="420" :row-key="(e: TrashItem) => e.id"
      :empty-text="t('trashBin.empty')"
    >
      <el-table-column :label="t('trashBin.col.name')" min-width="200" show-overflow-tooltip>
        <template #default="{ row }"><span class="mono">{{ row.name }}</span></template>
      </el-table-column>
      <el-table-column v-if="hasDetail" :label="t('trashBin.col.detail')" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ row.detail ?? '' }}</template>
      </el-table-column>
      <el-table-column :label="t('trashBin.col.deleted')" width="156">
        <template #default="{ row }"><span class="num">{{ fmtDeleted(row.deleted) }}</span></template>
      </el-table-column>
      <el-table-column :label="t('trashBin.col.size')" width="88" align="right">
        <template #default="{ row }"><span class="num">{{ size(row.size) }}</span></template>
      </el-table-column>
      <el-table-column width="180" align="right">
        <template #default="{ row }">
          <el-button
            v-if="row.restorable" size="small" link type="primary" :disabled="locked"
            :loading="busy === row.id" @click="emit('restore', row.id)"
          >{{ t('trashBin.restore') }}</el-button>
          <el-button size="small" link type="danger" :disabled="locked" @click="purge(row)">{{ t('trashBin.purge') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <template #footer>
      <div class="foot">
        <span class="count">{{ t('trashBin.total', { n: entries.length, size: size(total) }) }}</span>
        <el-button v-if="dirs.length <= 1" size="small" :disabled="!dirs.length" :title="dirs[0]" @click="openFolder(dirs[0])">
          <el-icon><FolderOpened /></el-icon><span>{{ t('trashBin.openFolder') }}</span>
        </el-button>
        <el-dropdown v-else trigger="click" @command="openFolder">
          <el-button size="small">
            <el-icon><FolderOpened /></el-icon><span>{{ t('trashBin.openFolder') }}</span>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-for="d in dirs" :key="d" :command="d"><span class="mono">{{ d }}</span></el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button
          size="small" type="danger" plain :disabled="!entries.length || locked" :loading="busy === '*'" @click="empty"
        >{{ t('trashBin.emptyAll') }}</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.foot {
  display: flex;
  align-items: center;
  gap: 8px;
}
.foot .count {
  margin-right: auto;
  color: var(--ink-3);
  font-size: 12px;
}
.foot .el-dropdown {
  margin: 0 4px;
}
.el-button .el-icon + span {
  margin-left: 6px;
}
.num {
  font-variant-numeric: tabular-nums;
}
</style>
