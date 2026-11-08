<!-- Saved maps, projection previews, imports and recycle bin. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { fmtDuration } from '@/format'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { useStatusStore } from '@/stores/status'
import { openFolder } from '@/openFolder'
import type { MappingPreset, NavMap } from '@/types'
import { fmtBytes, mapsApi, type IssueLevel, type MapDetail, type MapEntry } from '@/maps/api'
import GlassPanel from '@/components/GlassPanel.vue'
import NavGrid2D from '@/components/NavGrid2D.vue'
import NavScene3D from '@/components/NavScene3D.vue'
import MapRegridDialog from '@/components/MapRegridDialog.vue'
import MapEditDialog from '@/components/MapEditDialog.vue'
import NavigationImportDialog, { type MapImportResult } from '@/components/NavigationImportDialog.vue'
import type { TrashEntry } from '@/maps/api'

const { t } = useI18n({ messages: {
  'zh-CN': { management: { import: '导入地图', regrid: '重新生成 2D', edit: '编辑 2D 地图', remove: '删除地图', trash: '回收站', restore: '恢复', purge: '永久删除', empty: '清空回收站', close: '关闭', confirm: '确认', cancel: '取消', removeAsk: '将地图 {name} 移到回收站？原文件仍可恢复。', purgeAsk: '永久删除这些回收站文件？此操作不可恢复。', deleted: '地图已移到回收站', restored: '地图已恢复', purged: '回收站文件已永久删除', noTrash: '回收站为空', deletedAt: '删除时间', legacy: '旧平铺格式暂不支持删除（图像可能被其他地图共用）' } },
  en: { management: { import: 'Import map', regrid: 'Regenerate 2D', edit: 'Edit 2D map', remove: 'Delete map', trash: 'Recycle bin', restore: 'Restore', purge: 'Delete permanently', empty: 'Empty recycle bin', close: 'Close', confirm: 'Confirm', cancel: 'Cancel', removeAsk: 'Move map {name} to the recycle bin? Its files remain recoverable.', purgeAsk: 'Permanently delete these recycled files? This cannot be undone.', deleted: 'Map moved to recycle bin', restored: 'Map restored', purged: 'Recycled files permanently deleted', noTrash: 'Recycle bin is empty', deletedAt: 'Deleted at', legacy: 'Legacy flat maps cannot be deleted here (images may be shared)' } },
} })
const store = useStatusStore()
const route = useRoute()
const router = useRouter()

// ---------------- List ----------------
const PREF_KEY = 'robot_console.maps_view'
const savedName = (() => {
  try {
    return localStorage.getItem(PREF_KEY) ?? ''
  } catch {
    return ''
  }
})()
const maps = ref<MapEntry[]>([])
const listError = ref('')
const loadingList = ref(false)
const presets = ref<MappingPreset[]>([])
const selected = ref<string>(typeof route.query.map === 'string' ? route.query.map : savedName)

async function loadList() {
  loadingList.value = true
  try {
    maps.value = await mapsApi.list()
    listError.value = ''
    if (!maps.value.some((m) => m.name === selected.value)) selected.value = maps.value[0]?.name ?? ''
  } catch (e) {
    listError.value = errorText(e)
  } finally {
    loadingList.value = false
  }
  try {
    presets.value = await api.mappingPresets()
  } catch {
    // Scene labels fall back to the raw preset name
  }
}
onMounted(loadList)
watch(
  () => store.connected,
  (c) => {
    if (c) loadList()
  },
)
watch(selected, (name) => {
  try {
    localStorage.setItem(PREF_KEY, name)
  } catch {
    // Storage disabled: the choice is just not remembered
  }
  if (route.query.map !== name) router.replace({ query: { ...route.query, map: name || undefined } })
})

function sceneLabel(preset: string | null) {
  if (!preset) return '—'
  const p = presets.value.find((x) => x.name === preset)
  return p ? pick(p.label) : preset
}
const LEVEL_CHIP: Record<IssueLevel, string> = { error: 'danger', warn: 'warning', info: 'info' }
function issueText(code: string, params: Record<string, string | number>) {
  const key = `mapsPage.issue.${code}`
  return t(key, params)
}
function worst(m: MapEntry): IssueLevel | null {
  if (m.issues.some((i) => i.level === 'error')) return 'error'
  if (m.issues.some((i) => i.level === 'warn')) return 'warn'
  return null
}
function fmtPoints(n: number | null) {
  return n == null ? '—' : n.toLocaleString()
}
function gridText(m: MapEntry) {
  return m.grid ? `${m.grid.size_m[0]} × ${m.grid.size_m[1]} m` : '—'
}

// ---------------- Detail + preview assets ----------------
const detail = shallowRef<MapDetail | null>(null)
const detailError = ref('')
const image = shallowRef<ImageBitmap | null>(null)
const cloud = shallowRef<Float32Array | null>(null)
const loading2d = ref(false)
const loading3d = ref(false)
const assetError2d = ref('')
const assetError3d = ref('')
let abort: AbortController | null = null

function setImage(next: ImageBitmap | null) {
  image.value?.close()
  image.value = next
}

async function loadDetail(name: string) {
  abort?.abort()
  setImage(null)
  cloud.value = null
  detail.value = null
  loading2d.value = loading3d.value = false
  detailError.value = assetError2d.value = assetError3d.value = ''
  if (!name) return
  const ctrl = new AbortController()
  abort = ctrl
  let d: MapDetail
  try {
    d = await mapsApi.detail(name)
  } catch (e) {
    if (!ctrl.signal.aborted) detailError.value = errorText(e)
    return
  }
  if (ctrl.signal.aborted) return
  detail.value = d
  // 2D and 3D load in parallel; either can fail on its own
  if (d.has_grid) {
    loading2d.value = true
    mapsApi
      .grid(name, ctrl.signal)
      .then((bmp) => (ctrl.signal.aborted ? bmp.close() : setImage(bmp)))
      .catch((e) => {
        if (!ctrl.signal.aborted) assetError2d.value = errorText(e)
      })
      .finally(() => {
        if (abort === ctrl) loading2d.value = false
      })
  }
  if (d.has_cloud) {
    loading3d.value = true
    mapsApi
      .cloud(name, ctrl.signal)
      .then((c) => {
        if (!ctrl.signal.aborted) cloud.value = c
      })
      .catch((e) => {
        if (!ctrl.signal.aborted) assetError3d.value = errorText(e)
      })
      .finally(() => {
        if (abort === ctrl) loading3d.value = false
      })
  }
}
let detailLoad: Promise<void> = Promise.resolve()
watch(selected, (n) => { detailLoad = loadDetail(n) }, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => {
  abort?.abort()
  setImage(null)
})

// The display components take the navigation page's map type
const map2d = computed<NavMap | null>(() => {
  const d = detail.value
  if (!d || !d.grid) return null
  return { name: d.name, has_cloud: d.has_cloud, has_solid: false, preset: d.preset, grid: d.grid }
})
// A map without a 2D grid still needs a frame for the 3D camera: use the cloud's bounding box
const map3d = computed<NavMap | null>(() => {
  if (map2d.value) return map2d.value
  const d = detail.value
  const c = cloud.value
  if (!d || !c || !c.length) return null
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (let i = 0; i < c.length; i += 3) {
    if (c[i] < x0) x0 = c[i]
    if (c[i] > x1) x1 = c[i]
    if (c[i + 1] < y0) y0 = c[i + 1]
    if (c[i + 1] > y1) y1 = c[i + 1]
  }
  return {
    name: d.name,
    has_cloud: true,
    has_solid: false,
    preset: d.preset,
    grid: { resolution: 1, width: Math.max(1, x1 - x0), height: Math.max(1, y1 - y0), origin: [x0, y0, 0] },
  }
})
const grid2d = ref<InstanceType<typeof NavGrid2D> | null>(null)
const scene3d = ref<InstanceType<typeof NavScene3D> | null>(null)
// NavScene3D frames the view itself only when a grid image arrives; frame cloud-only maps here
watch(map3d, (m, old) => {
  if (m && !image.value && m.name !== old?.name) requestAnimationFrame(() => scene3d.value?.resetView())
})

// ---------------- Actions ----------------
const navBusy = ref(false)
async function useForNavigation() {
  const d = detail.value
  if (!d) return
  navBusy.value = true
  try {
    const s = await api.navigationStatus()
    if ((s.state === 'running' || s.state === 'stopping') && s.map && s.map !== d.name) {
      ElMessage.warning(t('mapsPage.navBusy', { name: s.map }))
    }
  } catch {
    // Status unknown: just go to the navigation page
  } finally {
    navBusy.value = false
  }
  router.push({ path: '/navigation', query: { map: d.name } })
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(t('mapsPage.copied'))
  } catch {
    ElMessage.error(t('mapsPage.copyFailed'))
  }
}

const importOpen = ref(false)
const regridOpen = ref(false)
const editOpen = ref(false)
const trashOpen = ref(false)
const trashEntries = ref<TrashEntry[]>([])
const mutationBusy = ref(false)
async function confirm(text: string) {
  try { await ElMessageBox.confirm(text, t('management.confirm'), { type: 'warning', confirmButtonText: t('management.confirm'), cancelButtonText: t('management.cancel') }); return true }
  catch { return false }
}
async function removed() {
  if (!detail.value || !await confirm(t('management.removeAsk', { name: detail.value.name }))) return
  mutationBusy.value = true
  try { await mapsApi.remove(detail.value.name); ElMessage.success(t('management.deleted')); await loadList() }
  catch (e) { ElMessage.error(errorText(e)) } finally { mutationBusy.value = false }
}
async function loadTrash() {
  try { trashEntries.value = await mapsApi.trash() } catch (e) { ElMessage.error(errorText(e)) }
}
async function openTrash() { trashOpen.value = true; await loadTrash() }
async function restore(id: string) {
  mutationBusy.value = true
  try { await mapsApi.restore(id); ElMessage.success(t('management.restored')); await loadTrash(); await loadList() }
  catch (e) { ElMessage.error(errorText(e)) } finally { mutationBusy.value = false }
}
async function purge(id?: string) {
  if (!await confirm(t('management.purgeAsk'))) return
  mutationBusy.value = true
  try { await mapsApi.purge(id); ElMessage.success(t('management.purged')); await loadTrash() }
  catch (e) { ElMessage.error(errorText(e)) } finally { mutationBusy.value = false }
}
async function selectNew(name: string) {
  await loadList()
  if (selected.value !== name) selected.value = name
  else detailLoad = loadDetail(name)
  await detailLoad
}
async function imported(m: MapImportResult) {
  await selectNew(m.name)
  if (!m.grid) regridOpen.value = true
}

function fileRole(role: string | null) {
  return role ? t(`mapsPage.role.${role}`) : '—'
}
</script>

<template>
  <div class="maps-page">
    <!-- Map list -->
    <GlassPanel :title="t('mapsPage.list')" flush>
      <template #extra>
        <el-button size="small" @click="importOpen=true">{{ t('management.import') }}</el-button>
        <el-button size="small" @click="openTrash">{{ t('management.trash') }}</el-button>
        <span class="count">{{ t('mapsPage.count', { n: maps.length }) }}</span>
        <el-button size="small" circle :title="t('openFolder.maps')" :aria-label="t('openFolder.maps')" @click="openFolder(store.paths.maps)">
          <el-icon><FolderOpened /></el-icon>
        </el-button>
        <el-button size="small" circle :title="t('mapsPage.refresh')" :loading="loadingList" @click="loadList">
          <el-icon><Refresh /></el-icon>
        </el-button>
      </template>
      <p v-if="listError" class="warn pad">{{ listError }}</p>
      <el-table
        :data="maps"
        size="small"
        max-height="300"
        highlight-current-row
        :row-key="(r: MapEntry) => r.name"
        :current-row-key="selected"
        :row-class-name="({ row }: { row: MapEntry }) => (row.name === selected ? 'is-selected' : '')"
        :empty-text="loadingList ? t('mapsPage.loading') : t('mapsPage.empty')"
        @row-click="(r: MapEntry) => (selected = r.name)"
      >
        <!-- Long names (versions saved from regrid / edit get suffixes) end in an ellipsis with the full name as
             tooltip instead of wrapping; the full name is also in the detail panel -->
        <el-table-column :label="t('mapsPage.col.name')" min-width="178" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="name mono">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('mapsPage.col.created')" width="152">
          <template #default="{ row }"><span class="num">{{ row.created }}</span></template>
        </el-table-column>
        <el-table-column :label="t('mapsPage.col.algorithm')" width="88">
          <template #default="{ row }">{{ row.algorithm ?? (row.source === 'upload' ? t('mapsPage.imported') : '—') }}</template>
        </el-table-column>
        <el-table-column :label="t('mapsPage.col.scene')" min-width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ sceneLabel(row.preset) }}</template>
        </el-table-column>
        <el-table-column :label="t('mapsPage.col.points')" width="95" align="right">
          <template #default="{ row }"><span class="num">{{ fmtPoints(row.points) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('mapsPage.col.resolution')" width="90" align="right">
          <template #default="{ row }"><span class="num">{{ row.grid ? `${row.grid.resolution} m` : '—' }}</span></template>
        </el-table-column>
        <el-table-column :label="t('mapsPage.col.extent')" width="140" align="right">
          <template #default="{ row }"><span class="num">{{ gridText(row) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('mapsPage.col.state')" min-width="112">
          <template #default="{ row }">
            <div class="chips">
              <span v-if="!worst(row)" class="chip success">{{ t('mapsPage.complete') }}</span>
              <span
                v-for="i in row.issues.filter((x: { level: IssueLevel }) => x.level !== 'info')"
                :key="i.code"
                class="chip"
                :class="LEVEL_CHIP[i.level as IssueLevel]"
              >
                {{ t(`mapsPage.short.${i.code}`) }}
              </span>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </GlassPanel>

    <NavigationImportDialog v-model="importOpen" :disabled="false" allow-cloud-only @completed="imported" />
    <MapRegridDialog v-model="regridOpen" :map="detail" :cloud="cloud" @saved="selectNew" />
    <MapEditDialog v-model="editOpen" :map="detail" @saved="selectNew" />
    <el-dialog v-model="trashOpen" :title="t('management.trash')" width="min(850px, 95vw)">
      <el-table :data="trashEntries" :empty-text="t('management.noTrash')">
        <el-table-column prop="name" :label="t('mapsPage.col.name')" />
        <el-table-column prop="deleted" :label="t('management.deletedAt')" />
        <el-table-column :label="t('mapsPage.col.size')"><template #default="{row}">{{ fmtBytes(row.size) }}</template></el-table-column>
        <el-table-column width="240"><template #default="{row}">
          <el-button size="small" :disabled="mutationBusy" @click="restore(row.id)">{{ t('management.restore') }}</el-button>
          <el-button size="small" type="danger" :disabled="mutationBusy" @click="purge(row.id)">{{ t('management.purge') }}</el-button>
        </template></el-table-column>
      </el-table>
      <template #footer><el-button type="danger" :disabled="mutationBusy || !trashEntries.length" @click="purge()">{{ t('management.empty') }}</el-button><el-button @click="trashOpen=false">{{ t('management.close') }}</el-button></template>
    </el-dialog>
    <p v-if="detailError" class="warn">{{ detailError }}</p>

    <template v-if="detail">
      <!-- Header: name, completeness notes, shortcut -->
      <section class="head glass">
        <div class="title-row">
          <h2 class="mono">{{ detail.name }}</h2>
          <span class="chip" :class="detail.has_grid ? 'success' : 'danger'">2D</span>
          <span class="chip" :class="detail.has_cloud ? 'success' : 'warning'">3D</span>
          <span class="chip" :class="detail.preset ? 'primary' : 'warning'">
            {{ t('mapsPage.col.scene') }}: {{ sceneLabel(detail.preset) }}
          </span>
          <div class="actions">
          <el-button :disabled="!detail.has_cloud" @click="regridOpen=true">{{ t('management.regrid') }}</el-button>
          <el-button :disabled="!detail.has_grid" @click="editOpen=true">{{ t('management.edit') }}</el-button>
          <el-button type="danger" plain :disabled="detail.kind==='flat' || mutationBusy" :title="detail.kind==='flat' ? t('management.legacy') : ''" @click="removed">{{ t('management.remove') }}</el-button>
          <el-button
            type="primary"
            :disabled="!detail.navigable"
            :loading="navBusy"
            :title="detail.navigable ? '' : t('mapsPage.notNavigable')"
            @click="useForNavigation"
          >
            <el-icon><Position /></el-icon>
            <span>{{ t('mapsPage.useForNav') }}</span>
          </el-button>
          </div>
        </div>
        <ul v-if="detail.issues.length" class="issues">
          <li v-for="i in detail.issues" :key="i.code" :class="i.level">
            <el-icon v-if="i.level === 'error'"><CircleCloseFilled /></el-icon>
            <el-icon v-else-if="i.level === 'warn'"><WarningFilled /></el-icon>
            <el-icon v-else><InfoFilled /></el-icon>
            <span>{{ issueText(i.code, i.params) }}</span>
          </li>
        </ul>
        <p v-else class="ok-line">
          <el-icon><CircleCheckFilled /></el-icon>{{ t('mapsPage.allGood') }}
        </p>
      </section>

      <!-- Side-by-side preview -->
      <div class="views">
        <section class="viewport">
          <NavGrid2D ref="grid2d" :map="map2d" :image="image" :status="null" tool="pan" />
          <div class="hud tl"><span class="title">{{ t('mapsPage.view2d') }}</span></div>
          <div class="hud tr">
            <el-button size="small" circle :title="t('navigation.fit')" :disabled="!map2d" @click="grid2d?.fit()">
              <el-icon><Aim /></el-icon>
            </el-button>
          </div>
          <div v-if="loading2d" class="empty">{{ t('mapsPage.loading') }}</div>
          <div v-else-if="!detail.has_grid || assetError2d" class="empty">
            {{ assetError2d || t('mapsPage.no2d') }}
          </div>
        </section>
        <section class="viewport">
          <NavScene3D
            ref="scene3d"
            :map="map3d"
            :image="image"
            :cloud="cloud"
            :solid="null"
            model="cloud"
            :status="null"
            :show-cloud="true"
            :show-grid="true"
          />
          <div class="hud tl"><span class="title">{{ t('mapsPage.view3d') }}</span></div>
          <div class="hud tr">
            <el-button size="small" circle :title="t('navigation.fit')" :disabled="!map3d" @click="scene3d?.resetView()">
              <el-icon><Aim /></el-icon>
            </el-button>
          </div>
          <div v-if="cloud" class="hud bl note">
            {{ t('mapsPage.previewPoints', { n: (cloud.length / 3).toLocaleString() }) }}
          </div>
          <div v-if="loading3d" class="empty">{{ t('mapsPage.loading') }}</div>
          <div v-else-if="!detail.has_cloud || assetError3d" class="empty">
            {{ assetError3d || t('mapsPage.no3d') }}
          </div>
        </section>
      </div>

      <!-- Facts and files -->
      <div class="bottom">
        <GlassPanel :title="t('mapsPage.info')">
          <dl class="facts">
            <dt>{{ t('mapsPage.col.created') }}</dt>
            <dd class="num">{{ detail.created }}</dd>
            <dt>{{ t('mapsPage.col.algorithm') }}</dt>
            <dd>{{ detail.algorithm ?? (detail.source === 'upload' ? t('mapsPage.imported') : '—') }}</dd>
            <dt>{{ t('mapsPage.col.scene') }}</dt>
            <dd>{{ sceneLabel(detail.preset) }}</dd>
            <dt>{{ t('mapsPage.f.points') }}</dt>
            <dd class="num">{{ fmtPoints(detail.points) }}</dd>
            <dt>{{ t('mapsPage.f.voxels') }}</dt>
            <dd class="num">
              {{ detail.voxels == null ? '—' : `${detail.voxels.toLocaleString()}` }}
              <template v-if="detail.voxel_size"> @ {{ detail.voxel_size }} m</template>
            </dd>
            <dt>{{ t('mapsPage.f.grid') }}</dt>
            <dd class="num">
              <template v-if="detail.grid">
                {{ detail.grid.width }} × {{ detail.grid.height }} px · {{ detail.grid.resolution }} m ·
                {{ gridText(detail) }}
              </template>
              <template v-else>—</template>
            </dd>
            <dt>{{ t('mapsPage.f.origin') }}</dt>
            <dd class="num">{{ detail.grid ? detail.grid.origin.map((v) => v.toFixed(2)).join(', ') : '—' }}</dd>
            <dt>{{ t('mapsPage.f.duration') }}</dt>
            <dd class="num">{{ detail.duration == null ? '—' : fmtDuration(detail.duration) }}</dd>
            <dt>{{ t('mapsPage.f.length') }}</dt>
            <dd class="num">{{ detail.length == null ? '—' : `${detail.length.toFixed(1)} m` }}</dd>
            <dt>{{ t('mapsPage.f.frame') }}</dt>
            <dd class="mono">{{ detail.frame ?? '—' }}</dd>
            <dt>{{ t('mapsPage.f.run') }}</dt>
            <dd class="mono">{{ detail.run ?? '—' }}</dd>
          </dl>
          <details v-if="detail.sim_cmd || detail.slam_cmd" class="cmds">
            <summary>{{ t('mapsPage.f.commands') }}</summary>
            <pre v-if="detail.sim_cmd" class="mono">{{ detail.sim_cmd }}</pre>
            <pre v-if="detail.slam_cmd" class="mono">{{ detail.slam_cmd }}</pre>
          </details>
        </GlassPanel>

        <GlassPanel :title="t('mapsPage.files')" flush>
          <template #extra>
            <span class="count">{{ fmtBytes(detail.total_size) }}</span>
            <el-button size="small" @click="openFolder(detail.location)">
              <el-icon><FolderOpened /></el-icon><span>{{ t('openFolder.button') }}</span>
            </el-button>
          </template>
          <div class="paths">
            <div class="path-row">
              <span class="label">WSL</span>
              <code class="mono">{{ detail.location }}</code>
              <el-button size="small" text :title="t('mapsPage.copy')" @click="copy(detail.location)">
                <el-icon><CopyDocument /></el-icon>
              </el-button>
            </div>
            <div class="path-row">
              <span class="label">Windows</span>
              <code class="mono">{{ detail.location_win }}</code>
              <el-button size="small" text :title="t('mapsPage.copy')" @click="copy(detail.location_win)">
                <el-icon><CopyDocument /></el-icon>
              </el-button>
            </div>
            <p class="muted">{{ t('mapsPage.pathHint') }}</p>
          </div>
          <el-table :data="detail.files" size="small">
            <el-table-column :label="t('mapsPage.col.file')" min-width="140">
              <template #default="{ row }">
                <el-icon class="ficon"><Folder v-if="row.dir" /><Document v-else /></el-icon>
                <span class="mono">{{ row.name }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('mapsPage.col.role')" min-width="170">
              <template #default="{ row }">
                {{ fileRole(row.role) }}
                <span v-if="row.dir && row.count != null" class="muted-inline">
                  · {{ t('mapsPage.nFiles', { n: row.count }) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column :label="t('mapsPage.col.size')" width="90" align="right">
              <template #default="{ row }"><span class="num">{{ fmtBytes(row.size) }}</span></template>
            </el-table-column>
            <!-- figures/, data/: open the sub-folder directly -->
            <el-table-column width="44" align="center">
              <template #default="{ row }">
                <el-button
                  v-if="row.dir" size="small" text circle :title="t('openFolder.sub', { name: row.name })"
                  :aria-label="t('openFolder.sub', { name: row.name })" @click="openFolder(`${detail.location}/${row.name}`)"
                >
                  <el-icon><FolderOpened /></el-icon>
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </GlassPanel>
      </div>
    </template>
    <div v-else-if="!listError && !detailError && !loadingList && !maps.length" class="glass empty-page">
      {{ t('mapsPage.empty') }}
    </div>
  </div>
</template>

<style scoped>
.maps-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 100%;
}
.count {
  margin-right: 8px;
  font-size: 12px;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}
.pad {
  padding: 0 16px;
}
.name {
  font-weight: 600;
  color: var(--ink);
}
.num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.mono {
  font-family: var(--font-mono);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.chips .chip {
  height: 20px;
  padding: 0 7px;
  font-size: 11.5px;
}
:deep(.el-table__row) {
  cursor: pointer;
}
:deep(.el-table__row.is-selected > td) {
  background: rgba(8, 145, 178, 0.08) !important;
}

.head {
  padding: 14px 18px;
  border-radius: var(--radius);
}
.title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.title-row h2 {
  margin: 0 6px 0 0;
  font-size: 17px;
  font-weight: 650;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  margin-left: auto;
}
.actions .el-button + .el-button {
  margin-left: 0;
}
.title-row .el-button .el-icon {
  margin-right: 6px;
}
.issues {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
  font-size: 13px;
}
.issues li {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  line-height: 1.55;
}
.issues .el-icon {
  flex: none;
  margin-top: 3px;
}
.issues .error {
  color: var(--bad);
}
.issues .warn {
  color: var(--warn);
}
.issues .info {
  color: var(--ink-3);
}
.ok-line {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--ok);
}

.views {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  height: 460px;
}
.viewport {
  position: relative;
  min-height: 320px;
  border-radius: var(--radius);
  overflow: hidden;
  background:
    radial-gradient(ellipse at 50% 35%, rgba(255, 255, 255, 0.92), rgba(255, 255, 255, 0.55) 70%),
    var(--glass);
  box-shadow: var(--shadow);
}
.hud {
  position: absolute;
  z-index: 3;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
}
.tl {
  top: 12px;
  left: 14px;
  padding: 4px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.tl .title {
  font-weight: 650;
  color: var(--ink);
}
.tr {
  top: 12px;
  right: 14px;
}
.bl {
  left: 14px;
  bottom: 12px;
  padding: 5px 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.78);
  backdrop-filter: blur(12px);
  box-shadow: inset 0 0 0 1px var(--hairline);
  color: var(--ink-3);
}
.empty {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  text-align: center;
  color: var(--ink-3);
  pointer-events: none;
}

.bottom {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
  gap: 14px;
  align-items: start;
}
.facts {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 16px;
  margin: 0;
  font-size: 12.5px;
}
.facts dt {
  color: var(--ink-3);
}
.facts dd {
  margin: 0;
  text-align: right;
  word-break: break-all;
}
.cmds {
  margin-top: 12px;
  font-size: 12px;
  color: var(--ink-3);
}
.cmds summary {
  cursor: pointer;
}
.cmds pre {
  margin: 6px 0 0;
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(15, 30, 60, 0.04);
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 11.5px;
  color: var(--ink-2);
}
.paths {
  padding: 4px 16px 10px;
}
.path-row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.path-row .label {
  color: var(--ink-3);
}
.path-row code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
}
.ficon {
  margin-right: 6px;
  vertical-align: -2px;
  color: var(--ink-3);
}
.muted,
.warn {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}
.warn {
  color: var(--warn);
}
.muted-inline {
  color: var(--ink-3);
}
.empty-page {
  padding: 40px;
  border-radius: var(--radius);
  text-align: center;
  color: var(--ink-3);
}

@media (max-width: 1280px) {
  .views {
    grid-template-columns: minmax(0, 1fr);
    height: auto;
    grid-auto-rows: 380px;
  }
  .bottom {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
