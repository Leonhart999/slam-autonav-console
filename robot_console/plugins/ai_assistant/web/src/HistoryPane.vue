<!-- Conversation history inside the chat panel: search, groups (pinned / today / past 7 days / earlier), open, pin,
     rename in place, export as Markdown, delete with a second click. Overlay over the conversation when the panel is
     narrow, docked on its left side when it is wide (Assistant.vue decides and passes `docked`). -->
<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { deleteSession, download, exportSessionUrl, listSessions, patchSession, type SessionBrief } from './api'
import { compact, relative, shortModel } from './format'
import { RC, t } from './sdk'
import Icon from './Icon.vue'
import HistActs from './HistActs.vue'
import LiquidGlass from './glass/LiquidGlass.vue'

const props = defineProps<{ current: string; busy: boolean; docked: boolean; reload: number }>()
const emit = defineEmits<{ open: [id: string]; newChat: []; close: []; deleted: [id: string] }>()

const query = ref('')
const items = ref<SessionBrief[]>([])
const total = ref(0)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const errorText = ref('')
const renaming = ref('')
const renameText = ref('')
const confirmDel = ref('')
const exported = ref('')
const searchInput = ref<HTMLInputElement | null>(null)
const renameInput = ref<HTMLInputElement[] | null>(null)
const loadingMore = ref(false)
const typing = ref(false)            // the search text changed and its first page is not loaded yet
let loadedQuery = ''                 // the query the loaded items belong to: "load more" continues this one only
let timer = 0
let delTimer = 0
let controller: AbortController | null = null

async function load() {
  controller?.abort()
  controller = new AbortController()
  const q = query.value.trim()
  if (!items.value.length) state.value = 'loading'
  try {
    const page = await listSessions(q, 200, controller.signal, 0)
    items.value = page.items
    total.value = page.total
    loadedQuery = q
    typing.value = false
    state.value = 'ready'
  } catch (e) {
    if (controller.signal.aborted) return
    errorText.value = RC().errorText(e)
    state.value = 'error'
  }
}

async function loadMore() {
  if (loadingMore.value || typing.value || items.value.length >= total.value) return
  loadingMore.value = true
  const q = loadedQuery
  const ac = controller
  try {
    const page = await listSessions(q, 200, ac?.signal, items.value.length)
    if (ac?.signal.aborted || q !== loadedQuery) return
    const have = new Set(items.value.map((x) => x.id))
    items.value = [...items.value, ...page.items.filter((x) => !have.has(x.id))]
    total.value = page.total
  } catch (e) {
    if (!ac?.signal.aborted) RC().ui.ElMessage.error(RC().errorText(e))
  } finally {
    loadingMore.value = false
  }
}

watch(query, () => {
  typing.value = true
  window.clearTimeout(timer)
  timer = window.setTimeout(load, 250)
})
watch(() => props.reload, () => void load())
void load()
void nextTick(() => { if (!props.docked) searchInput.value?.focus() })

const searching = computed(() => !!query.value.trim())
const groups = computed(() => {
  if (searching.value) return [{ key: 'results', items: items.value }]
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const today = start.getTime() / 1000
  const week = today - 6 * 86400
  const out: Record<string, SessionBrief[]> = { pinned: [], today: [], week: [], earlier: [] }
  for (const s of items.value) {
    if (s.pinned) out.pinned.push(s)
    else if (s.updated >= today) out.today.push(s)
    else if (s.updated >= week) out.week.push(s)
    else out.earlier.push(s)
  }
  return Object.entries(out).filter(([, v]) => v.length).map(([key, v]) => ({ key, items: v }))
})

/** the search hit inside the snippet, wrapped in <mark> (text escaped first) */
function marked(text: string) {
  const esc = text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
  const q = query.value.trim()
  if (!q) return esc
  const qe = q.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
  const i = esc.toLowerCase().indexOf(qe.toLowerCase())
  return i < 0 ? esc : `${esc.slice(0, i)}<mark>${esc.slice(i, i + qe.length)}</mark>${esc.slice(i + qe.length)}`
}

const tokens = (s: SessionBrief) => s.tokens.input + s.tokens.output + s.tokens.cache

function open(s: SessionBrief) {
  if (renaming.value === s.id) return
  emit('open', s.id)
}

async function togglePin(s: SessionBrief) {
  try {
    const next = await patchSession(s.id, { pinned: !s.pinned })
    Object.assign(s, next)
  } catch (e) {
    RC().ui.ElMessage.error(RC().errorText(e))
  }
}

async function startRename(s: SessionBrief) {
  renaming.value = s.id
  renameText.value = s.title
  await nextTick()
  const el = renameInput.value?.[0]
  el?.focus()
  el?.select()
}

async function saveRename(s: SessionBrief) {
  if (renaming.value !== s.id) return
  const title = renameText.value.trim()
  renaming.value = ''
  if (!title || title === s.title) return
  const old = s.title
  s.title = title
  try {
    Object.assign(s, await patchSession(s.id, { title: title.slice(0, 120) }))
  } catch (e) {
    s.title = old
    RC().ui.ElMessage.error(RC().errorText(e))
  }
}

function exportMd(s: SessionBrief) {
  download(exportSessionUrl(s.id, 'md'))
  exported.value = s.id
  window.setTimeout(() => { if (exported.value === s.id) exported.value = '' }, 1200)
}

async function askDelete(s: SessionBrief) {
  if (confirmDel.value !== s.id) {
    confirmDel.value = s.id
    window.clearTimeout(delTimer)
    delTimer = window.setTimeout(() => (confirmDel.value = ''), 3000)
    return
  }
  confirmDel.value = ''
  try {
    await deleteSession(s.id)
    items.value = items.value.filter((x) => x.id !== s.id)
    total.value = Math.max(0, total.value - 1)
    emit('deleted', s.id)
  } catch (e) {
    RC().ui.ElMessage.error(RC().errorText(e))
  }
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && !renaming.value) {
    if (query.value) query.value = ''
    else if (!props.docked) emit('close')
  }
}

onUnmounted(() => {
  window.clearTimeout(timer)
  window.clearTimeout(delTimer)
  controller?.abort()
})
</script>

<template>
  <section class="aia-hist" :class="{ docked }" :aria-label="t('history.title')" @keydown="onKey">
    <div class="aia-hist-top">
      <LiquidGlass class="aia-hist-search-glass">
        <label class="aia-hist-search">
          <Icon name="search" class="aia-hist-sicon" />
          <input ref="searchInput" v-model="query" type="search" :placeholder="t('history.search')" :aria-label="t('history.search')">
        </label>
      </LiquidGlass>
      <button type="button" class="aia-ibtn" :title="t('history.newChat')" :aria-label="t('history.newChat')"
              :disabled="busy" @click="emit('newChat')"><Icon name="plus" /></button>
    </div>

    <div class="aia-hist-list">
      <div v-if="state === 'loading'" class="aia-hist-note">{{ t('history.loading') }}</div>
      <div v-else-if="state === 'error'" class="aia-hist-note error">
        {{ t('history.error') }} <button type="button" class="aia-hist-retry" @click="load">{{ t('history.retry') }}</button>
        <div class="aia-hist-sub">{{ errorText }}</div>
      </div>
      <div v-else-if="!items.length" class="aia-hist-note">{{ searching ? t('history.noMatch') : t('history.empty') }}</div>

      <template v-for="g in groups" :key="g.key">
        <div v-if="g.key !== 'results'" class="aia-hist-group">{{ t(`history.groups.${g.key}`) }}</div>
        <div
          v-for="s in g.items" :key="s.id" class="aia-hist-item"
          :class="{ on: s.id === current, confirming: confirmDel === s.id }"
          role="button" tabindex="0" @click="open(s)" @keydown.enter.self="open(s)"
        >
          <span class="aia-card-hl" aria-hidden="true"><span class="aia-card-edge" /></span>
          <input
            v-if="renaming === s.id" ref="renameInput" v-model="renameText" class="aia-hist-rename" maxlength="120"
            @click.stop @keydown.enter.prevent="saveRename(s)" @keydown.esc.stop.prevent="renaming = ''" @blur="saveRename(s)"
          >
          <span v-else class="aia-hist-title">
            <Icon v-if="s.pinned" name="pin" filled class="aia-hist-pin" />{{ s.title || t('history.untitled') }}
          </span>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <span v-if="s.snippet" class="aia-hist-snippet" v-html="marked(s.snippet)" />
          <span class="aia-hist-meta">
            <span class="aia-hist-metatext">
              {{ relative(s.updated) }}<template v-if="s.model"> · {{ shortModel(s.model) }}</template><template v-if="tokens(s)"> · {{ compact(tokens(s)) }}</template>
            </span>
            <HistActs>
              <template v-if="confirmDel !== s.id">
                <button type="button" class="aia-hist-act" data-lens :title="t(s.pinned ? 'history.unpin' : 'history.pin')" @click="togglePin(s)">
                  <Icon name="pin" :filled="s.pinned" />
                </button>
                <button type="button" class="aia-hist-act" data-lens :title="t('history.rename')" @click="startRename(s)"><Icon name="edit" /></button>
                <button type="button" class="aia-hist-act" data-lens :title="t('history.export')" @click="exportMd(s)">
                  <Icon :name="exported === s.id ? 'check' : 'download'" />
                </button>
              </template>
              <button
                type="button" class="aia-hist-act del" data-lens :class="{ ask: confirmDel === s.id }"
                :disabled="busy && s.id === current" :title="t('history.delete')" @click="askDelete(s)"
              >
                <template v-if="confirmDel === s.id">{{ t('history.confirmDelete') }}</template><Icon v-else name="trash" />
              </button>
            </HistActs>
          </span>
        </div>
      </template>
    </div>
    <div v-if="total > items.length" class="aia-hist-foot">
      <span>{{ t('history.more', { shown: items.length, total }) }}</span>
      <span class="aia-grow" />
      <button type="button" class="aia-hist-more" :disabled="loadingMore || typing" @click="loadMore">{{ t('history.loadMore') }}</button>
    </div>
  </section>
</template>
