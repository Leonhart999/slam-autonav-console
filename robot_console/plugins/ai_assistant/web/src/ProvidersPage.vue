<!-- "AI model services" page: the list of model service profiles (like cc-switch's provider list: avatar, note,
     address, which one is in use, drag to reorder) and the editor of the selected one. -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElButton, ElMessage, ElMessageBox } from 'element-plus'
import {
  activateProvider, createProvider, deleteProvider, getProviders, orderProviders, updateProvider,
  type Listing, type Profile, type ProfileInput,
} from './api'
import { PRESETS, avatarColor, blankProfile, initial, type Preset } from './presets'
import { RC, t } from './sdk'
import Icon from './Icon.vue'
import ProviderEditor from './ProviderEditor.vue'

const { GlassPanel } = RC().ui
const listing = ref<Listing>({ active: '', profiles: [] })
const loaded = ref(false)
const loadError = ref('')
const selected = ref('')            // profile id, or 'new'
const draft = ref<ProfileInput | null>(null)
const choosing = ref(false)         // preset picker shown
const editorDirty = ref(false)
const editor = ref<InstanceType<typeof ProviderEditor> | null>(null)
const dragId = ref('')
const overId = ref('')

const current = computed<Profile | null>(() => listing.value.profiles.find((p) => p.id === selected.value) ?? null)
const host = (p: Profile) => {
  const u = p.base_url || (p.format === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1')
  return u.replace(/^https?:\/\//, '').replace(/\/.*$/, '')
}
const presetHost = (p: Preset) => (p.base_url || 'api.anthropic.com').replace(/^https?:\/\//, '')
const modelLabel = (p: Profile) => p.models.find((m) => m.id === p.model)?.label || p.model
  || (p.format === 'anthropic' ? 'claude-opus-5-5' : '')

async function refresh(keep?: string) {
  try {
    listing.value = await getProviders()
    loadError.value = ''
  } catch (e) {
    loadError.value = RC().errorText(e)
    return
  } finally {
    loaded.value = true
  }
  const ids = listing.value.profiles.map((p) => p.id)
  if (keep && ids.includes(keep)) selected.value = keep
  else if (!ids.includes(selected.value) && selected.value !== 'new') selected.value = listing.value.active || ids[0] || ''
  if (!ids.length && selected.value !== 'new') choosing.value = true
}

async function confirmLeave() {
  if (!editorDirty.value || (selected.value !== 'new' && !current.value)) return true
  try {
    await ElMessageBox.confirm(t('page.discard'), t('page.discardTitle'),
      { type: 'warning', confirmButtonText: t('page.discardOk'), cancelButtonText: t('editor.cancel') })
    return true
  } catch {
    return false
  }
}

async function select(id: string) {
  if (id === selected.value || !(await confirmLeave())) return
  choosing.value = false
  draft.value = null
  selected.value = id
}

async function startNew() {
  if (!(await confirmLeave())) return
  choosing.value = true
}

function fromPreset(p: Preset) {
  draft.value = blankProfile(p, p.key === 'custom' ? '' : t(`presets.${p.key}`))
  selected.value = 'new'
  choosing.value = false
}

function cancelNew() {
  draft.value = null
  selected.value = listing.value.active || listing.value.profiles[0]?.id || ''
  choosing.value = !listing.value.profiles.length
}

async function onSave(input: ProfileInput, id: string | null) {
  try {
    const saved = id ? await updateProvider(id, input) : await createProvider(input)
    editor.value?.saved()
    draft.value = null
    await refresh(saved.id)
    window.dispatchEvent(new Event('aia:providers-changed'))
    ElMessage.success(t(id ? 'page.saved' : 'page.created', { name: saved.name }))
  } catch (e) {
    editor.value?.failed()
    ElMessage.error(RC().errorText(e))
  }
}

async function onRemove(id: string) {
  try {
    listing.value = await deleteProvider(id)
    selected.value = listing.value.active || listing.value.profiles[0]?.id || ''
    if (!listing.value.profiles.length) choosing.value = true
    window.dispatchEvent(new Event('aia:providers-changed'))
  } catch (e) {
    ElMessage.error(RC().errorText(e))
  }
}

async function onActivate(id: string) {
  try {
    await activateProvider(id)
    await refresh(selected.value)
    window.dispatchEvent(new Event('aia:providers-changed'))
    ElMessage.success(t('page.activated', { name: listing.value.profiles.find((p) => p.id === id)?.name ?? '' }))
  } catch (e) {
    ElMessage.error(RC().errorText(e))
  }
}

// drag to reorder (the order is the order of the assistant's switcher)
function onDragStart(id: string, e: DragEvent) {
  dragId.value = id
  e.dataTransfer?.setData('text/plain', id)
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
}
async function onDrop(target: string) {
  const from = dragId.value
  dragId.value = overId.value = ''
  if (!from || from === target) return
  const ids = listing.value.profiles.map((p) => p.id)
  ids.splice(ids.indexOf(from), 1)
  ids.splice(ids.indexOf(target), 0, from)
  const byId = new Map(listing.value.profiles.map((p) => [p.id, p]))
  listing.value = { ...listing.value, profiles: ids.map((i) => byId.get(i)!) }
  try {
    listing.value = await orderProviders(ids)
    window.dispatchEvent(new Event('aia:providers-changed'))
  } catch (e) {
    ElMessage.error(RC().errorText(e))
    void refresh(selected.value)
  }
}

onMounted(() => refresh())
window.addEventListener('aia:providers-changed', onExternalChange)
onUnmounted(() => window.removeEventListener('aia:providers-changed', onExternalChange))
/** Another tab (OAuth "use in the assistant") changed the profiles: reload, keeping the selection and the editor */
function onExternalChange() {
  if (!editorDirty.value) void refresh(selected.value)
}
defineExpose({ confirmLeave })
</script>

<template>
  <div class="aip">
    <GlassPanel :title="t('page.listTitle')" class="aip-list-panel">
      <p class="aip-help">{{ t('page.intro') }}</p>
      <div v-if="loadError" class="aip-error">{{ loadError }}</div>
      <div class="aip-list">
        <div
          v-for="p in listing.profiles" :key="p.id" class="aip-item"
          :class="{ on: p.id === selected, active: p.id === listing.active, over: overId === p.id && dragId !== p.id, dragging: dragId === p.id }"
          role="button" tabindex="0" draggable="true"
          @click="select(p.id)" @keydown.enter="select(p.id)"
          @dragstart="onDragStart(p.id, $event)" @dragend="dragId = overId = ''"
          @dragover.prevent="overId = p.id" @drop.prevent="onDrop(p.id)"
        >
          <span class="aip-avatar" :style="{ background: avatarColor(p.color, p.name) }">{{ initial(p.name) }}</span>
          <span class="aip-item-text">
            <span class="aip-item-name">{{ p.name }}<span v-if="p.note" class="aip-item-note">{{ p.note }}</span></span>
            <span class="aip-item-meta mono">{{ host(p) }}<template v-if="modelLabel(p)"> · {{ modelLabel(p) }}</template></span>
          </span>
          <span v-if="p.id === listing.active" class="aip-tag on">{{ t('list.inUse') }}</span>
          <el-button v-else size="small" text class="aip-item-use" @click.stop="onActivate(p.id)">{{ t('list.use') }}</el-button>
        </div>
        <div v-if="loaded && !listing.profiles.length" class="aip-empty">{{ t('list.empty') }}</div>
      </div>
      <el-button class="aip-new" @click="startNew"><Icon name="plus" />{{ t('list.add') }}</el-button>
      <p class="aip-help">
        {{ t('page.general') }} <a class="aip-link" href="#/plugins">{{ t('page.generalLink') }}</a>
      </p>
    </GlassPanel>

    <GlassPanel :title="choosing ? t('presets.title') : selected === 'new' ? t('page.newTitle') : current?.name ?? ''" class="aip-edit-panel">
      <div v-if="choosing" class="aip-presets">
        <p class="aip-help">{{ t('presets.help') }}</p>
        <div class="aip-preset-grid">
          <button v-for="p in PRESETS" :key="p.key" type="button" class="aip-preset" @click="fromPreset(p)">
            <span class="aip-avatar" :style="{ background: p.key === 'custom' ? undefined : avatarColor(p.color, p.name) }"
                  :class="{ custom: p.key === 'custom' }">
              <Icon v-if="p.key === 'custom'" name="plus" /><template v-else>{{ initial(t(`presets.${p.key}`)) }}</template>
            </span>
            <span class="aip-preset-text">
              <span class="aip-item-name">{{ t(`presets.${p.key}`) }}</span>
              <span class="aip-item-meta mono">{{ p.key === 'custom' ? t('presets.customMeta') : presetHost(p) }}</span>
            </span>
          </button>
        </div>
        <div v-if="listing.profiles.length" class="aip-actions">
          <span class="aip-grow" /><el-button @click="choosing = false">{{ t('editor.cancel') }}</el-button>
        </div>
      </div>
      <ProviderEditor
        v-else-if="current || draft" ref="editor"
        :profile="selected === 'new' ? null : current" :draft="selected === 'new' ? draft : null"
        :active="!!current && current.id === listing.active"
        @save="onSave" @remove="onRemove" @activate="onActivate" @cancel="cancelNew" @dirty="editorDirty = $event"
      />
      <div v-else-if="loaded" class="aip-empty">{{ t('list.pick') }}</div>
    </GlassPanel>
  </div>
</template>

<style>
/* AI model services page (aip-). Same rules as the chat panel: no outlines, four background levels a few percent
   apart (l1 rows, l2 inputs and cards, l3 hover / selected), focus as a double ring of shadow, 1em icons lowered by
   0.125em, tabular numbers, 40 lower font weights in the dark look. */
.aip, .aip-dialog, .aip-epop {
  --aip-l1: rgba(var(--tint-rgb), 0.028);
  --aip-l2: rgba(var(--tint-rgb), 0.05);
  --aip-l3: rgba(var(--tint-rgb), 0.08);
  font-variant-numeric: tabular-nums;
}
.dark .aip, .dark .aip-dialog, .dark .aip-epop { font-weight: 360; }
.dark .aip .el-button, .dark .aip-dialog .el-button { font-weight: 460; }
.aip {
  display: grid;
  grid-template-columns: minmax(280px, 340px) minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}
@media (max-width: 1100px) { .aip { grid-template-columns: minmax(0, 1fr); } }
.aip .aia-i, .aip-dialog .aia-i { margin-right: 5px; }
.aip .aip-ibtn .aia-i { margin-right: 0; }
.aip-help { margin: 2px 0 0; font-size: 12px; line-height: 1.6; color: var(--ink-3); text-wrap: pretty; }
.aip-error { font-size: 12px; color: var(--bad); }
.aip-dim { color: var(--ink-3); }
.aip-grow { flex: 1; }
.aip-row { display: flex; align-items: center; gap: 8px; }
.aip-empty { padding: 18px 4px; font-size: 13px; color: var(--ink-3); }
.aip-link {
  border: 0;
  padding: 0;
  background: none;
  font: inherit;
  font-size: 12.5px;
  color: var(--accent);
  cursor: pointer;
  text-decoration: none;
}
.aip-link:hover { text-decoration: underline; }
.aip-tag {
  flex: none;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11.5px;
  color: var(--ink-2);
  background: var(--aip-l2);
  white-space: nowrap;
}
.aip-tag.on { color: color-mix(in srgb, var(--accent) 80%, var(--deepen)); background: rgba(var(--accent-rgb), 0.12); }

/* ---- list ---- */
.aip-list { display: flex; flex-direction: column; gap: 4px; margin: 10px 0; }
.aip-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 12px;
  background: var(--aip-l1);
  cursor: pointer;
  transition: background-color 0.15s ease, opacity 0.15s ease, box-shadow 0.15s ease;
}
.aip-item:hover { background: var(--aip-l2); }
.aip-item.on { background: var(--aip-l3); }
.aip-item.over { box-shadow: 0 -2px 0 0 rgba(var(--accent-rgb), 0.55); }
.aip-item.dragging { opacity: 0.45; }
.aip-item:focus-visible { outline: none; box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.22); }
.aip-item-text, .aip-preset-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.aip-item-name {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.dark .aip-item-name { font-weight: 560; }
.aip-item-note { font-size: 12px; font-weight: 400; color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; }
.dark .aip-item-note { font-weight: 360; }
.aip-item-meta { font-size: 11.5px; color: var(--ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.aip-item-use { opacity: 0; transition: opacity 0.15s ease; }
.aip-item:hover .aip-item-use, .aip-item.on .aip-item-use { opacity: 1; }
.aip-new { width: 100%; }
.aip-avatar {
  flex: none;
  display: inline-grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 10px;
  padding: 0;
  color: hsl(0 0% 100%);
  font: inherit;
  font-size: 15px;
  font-weight: 600;
  box-shadow: inset 0 0 0 1px hsl(0 0% 100% / 0.12);
}
.aip-avatar.big { width: 40px; height: 40px; font-size: 17px; cursor: pointer; }
.aip-avatar.custom { color: var(--ink-2); background: var(--aip-l3); box-shadow: none; }

/* ---- presets ---- */
.aip-preset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; margin: 12px 0; }
.aip-preset {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border: 0;
  border-radius: 12px;
  background: var(--aip-l1);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease;
}
.aip-preset:hover { background: var(--aip-l3); }

/* ---- editor ---- */
.aip-editor { display: flex; flex-direction: column; gap: 16px; }
.aip-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 13px;
  color: var(--ink-2);
  background: rgba(var(--accent-rgb), 0.08);
}
.aip-banner.idle { background: var(--aip-l2); }
.aip-grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }
.aip-field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.aip-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.dark .aip-label { font-weight: 560; }
.aip-label b { color: var(--bad); font-weight: inherit; margin-left: -4px; }
.aip-label .el-button + .el-button { margin-left: 0; }
.aip-swatches { display: flex; flex-wrap: wrap; gap: 6px; padding: 2px 0 2px 2px; }
.aip-swatch {
  width: 22px;
  height: 22px;
  padding: 0;
  border: 0;
  border-radius: 7px;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  color: var(--ink-2);
  transition: box-shadow 0.15s ease;
}
.aip-swatch.auto { background: var(--aip-l3); }
.aip-swatch.on { box-shadow: 0 0 0 2px var(--card-solid), 0 0 0 4px rgba(var(--accent-rgb), 0.6); }
.aip-ibtn {
  flex: none;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--ink-3);
  font: inherit;
  font-size: 14px;
  line-height: 28px;
  text-align: center;
  text-decoration: none;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.aip-ibtn:hover { background: var(--aip-l2); color: var(--ink); }

/* grid folds: 0fr -> 1fr */
.aip-fold { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.26s cubic-bezier(0.2, 0.8, 0.2, 1); margin-top: -16px; }
.aip-fold.open { grid-template-rows: 1fr; margin-top: 0; }
.aip-adv .aip-fold { margin-top: 0; }
.aip-fold-inner { min-height: 0; overflow: hidden; }
.aip-card { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; border-radius: 12px; background: var(--aip-l1); }
.aip-card-head { display: flex; align-items: center; font-size: 13px; font-weight: 600; }
.dark .aip-card-head { font-weight: 560; }
.aip-ep { display: flex; align-items: center; gap: 8px; padding: 4px 6px 4px 10px; border-radius: 9px; background: var(--aip-l2); }
.aip-ep.current { background: rgba(var(--accent-rgb), 0.08); }
.aip-ep-url { flex: 1; min-width: 0; font-size: 12px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.aip-ms { min-width: 64px; text-align: right; font-size: 12px; }
.aip-ms.fast { color: var(--ok); }
.aip-ms.mid { color: var(--warn); }
.aip-ms.slow, .aip-ms.down { color: var(--bad); }

.aip-models { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
.aip-mrow {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) minmax(0, 1fr) 132px 112px 44px 28px;
  align-items: center;
  gap: 8px;
  padding: 4px 4px 4px 6px;
  border-radius: 10px;
  transition: background-color 0.15s ease, opacity 0.15s ease;
}
.aip-mrow:not(.head):hover { background: var(--aip-l1); }
.aip-mrow.dragging { opacity: 0.45; }
.aip-mrow.head { font-size: 12px; color: var(--ink-3); padding-bottom: 0; }
.aip-mrow.head > span:nth-child(6) { text-align: center; }
.aip-grip { cursor: grab; color: var(--ink-3); text-align: center; user-select: none; }
.aip-radio {
  justify-self: center;
  width: 16px;
  height: 16px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: var(--aip-l3);
  cursor: pointer;
  transition: box-shadow 0.18s ease, background-color 0.18s ease;
}
.aip-radio.on { background: var(--accent); box-shadow: inset 0 0 0 4px var(--card-solid), 0 0 0 3px rgba(var(--accent-rgb), 0.18); }
.aip-radio:disabled { cursor: not-allowed; opacity: 0.5; }

.aip-adv { border-radius: 14px; background: var(--aip-l1); }
.aip-adv-head {
  display: block;
  width: 100%;
  padding: 12px 14px;
  border: 0;
  background: none;
  color: var(--ink);
  font: inherit;
  font-size: 13.5px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
}
.dark .aip-adv-head { font-weight: 560; }
.aip-caret { transition: rotate 0.24s cubic-bezier(0.2, 0.8, 0.2, 1); }
.aip-caret.open { rotate: 90deg; }
.aip-adv-body { display: flex; flex-direction: column; gap: 16px; padding: 0 14px 14px; }
.aip-switch-row { display: flex; align-items: center; gap: 16px; }
.aip-switch-row > div { flex: 1; }
.aip-ua { width: 150px; flex: none; }
.aip .mono textarea { font-family: var(--font-mono); font-size: 12px; }

.aip-test { display: flex; align-items: center; gap: 6px; padding: 9px 12px; border-radius: 10px; font-size: 13px;
  color: var(--ok); background: color-mix(in srgb, var(--ok) 9%, transparent); overflow-wrap: anywhere; }
.aip-test.bad { color: var(--bad); background: color-mix(in srgb, var(--bad) 8%, transparent); }
.aip-actions { display: flex; align-items: center; gap: 8px; padding-top: 4px; }
.aip-actions .el-button + .el-button { margin-left: 0; }

/* ---- fetch dialog ---- */
.aip-fetch { display: flex; flex-direction: column; gap: 10px; }
.aip-fetch-bar { display: flex; align-items: center; }
.aip-fetch-list { max-height: 46vh; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
.aip-fetch-row { display: flex; align-items: center; gap: 10px; padding: 3px 8px; border-radius: 8px; cursor: pointer; font-size: 13px; }
.aip-fetch-row:hover { background: var(--aip-l2); }
.aip-fetch-row.have { cursor: default; opacity: 0.7; }
.aip-fetch-id { font-size: 12.5px; }

/* ---- inputs: no outline, double ring of shadow, deeper when focused ---- */
.aip .el-input__wrapper, .aip .el-select__wrapper, .aip .el-textarea__inner,
.aip-dialog .el-input__wrapper, .aip-epick-btn, .aip-epop-search, .aip-epop .el-select__wrapper {
  background: var(--aip-l2);
  box-shadow: 0 0 0 1px rgba(var(--tint-rgb), 0.06), 0 0 0 4px rgba(var(--tint-rgb), 0.022);
  transition: box-shadow 0.18s ease, background-color 0.18s ease;
}
.aip .el-input__wrapper:hover, .aip .el-select__wrapper:hover, .aip .el-textarea__inner:hover,
.aip-dialog .el-input__wrapper:hover, .aip-epick-btn:hover, .aip-epop-search:hover, .aip-epop .el-select__wrapper:hover {
  box-shadow: 0 0 0 1px rgba(var(--tint-rgb), 0.1), 0 0 0 4px rgba(var(--tint-rgb), 0.035);
}
.aip .el-input__wrapper.is-focus, .aip .el-select__wrapper.is-focused, .aip .el-textarea__inner:focus,
.aip-dialog .el-input__wrapper.is-focus, .aip-epick-btn.open, .aip-epop-search:focus-within,
.aip-epop .el-select__wrapper.is-focused {
  outline: none;
  background: transparent;
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.2), 0 0 0 7px rgba(var(--accent-rgb), 0.08);
}

/* ---- effort picker per model row (cc-switch style) ---- */
.aip-epick-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 24px;
  padding: 0 7px;
  border: 0;
  border-radius: 6px;
  color: var(--ink);
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.aip-epick-btn.unset { color: var(--ink-3); }
.aip-price-btn { justify-content: center; }
/* price popover of a model row (PricePicker.vue) */
.aip-price { width: 270px; gap: 8px; padding: 10px 12px; }
.aip-price-head { display: flex; align-items: center; justify-content: space-between; font-size: 12.5px; font-weight: 600; }
.dark .aip-price-head { font-weight: 560; }
.aip-price-row { display: grid; grid-template-columns: minmax(0, 1fr) 112px; align-items: center; gap: 10px; font-size: 12.5px; color: var(--ink-2); }
.aip-price-row .el-input-number { width: 112px; }
.aip-price-row .el-input__wrapper { box-shadow: none; background: rgba(var(--tint-rgb), 0.05); }
.aip-price-row .el-input__inner { text-align: right; }
.aip-price-unit { margin: 0; font-size: 11px; color: var(--ink-3); }
.aip-price-foot { display: flex; align-items: center; gap: 6px; }
.aip-price .el-radio-group { padding: 2px; border-radius: 8px; background: rgba(var(--tint-rgb), 0.05); }
.aip-price .el-radio-button__inner { border: 0 !important; border-radius: 6px !important; padding: 4px 9px; background: transparent; box-shadow: none !important; color: var(--ink-2); }
.aip-price .el-radio-button.is-active .el-radio-button__inner { color: var(--ink); background: var(--card-solid); box-shadow: 0 1px 3px rgba(var(--shade-rgb), 0.12) !important; }
.aip-epick-text { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.aip-epick-caret { flex: none; color: var(--ink-3); font-size: 11px; rotate: 90deg; transition: rotate 0.18s ease; }
.aip-epick-btn.open .aip-epick-caret { rotate: -90deg; }

.aip-epop {
  position: fixed;
  z-index: 2100;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border-radius: 12px;
  color: var(--ink);
  background: hsl(220 30% 97%);
  box-shadow: 0 18px 40px -10px hsl(220 45% 22% / 0.28), 0 4px 12px -4px hsl(220 45% 22% / 0.14);
}
.dark .aip-epop {
  background: hsl(222 30% 13%);
  box-shadow: 0 18px 40px -10px hsl(224 60% 3% / 0.72), 0 4px 12px -4px hsl(224 60% 3% / 0.5);
}
.aip-epop-search {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 9px;
  border-radius: 8px;
}
.aip-epop-sicon { flex: none; color: var(--ink-3); font-size: 13px; }
.aip-epop-input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: 12.5px;
}
.aip-epop-input::placeholder { color: var(--ink-3); }
.aip-epop-list {
  max-height: 224px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.aip-epop-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 5px 8px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--ink-2);
  font: inherit;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.12s ease, color 0.12s ease;
}
.aip-epop-item:hover { background: var(--aip-l3); color: var(--ink); }
.aip-epop-item.on { color: var(--ink); background: rgba(var(--accent-rgb), 0.09); }
.aip-epop-check { display: inline-grid; place-items: center; width: 14px; height: 14px; color: var(--accent); font-size: 13px; }
.aip-epop-empty { padding: 8px; text-align: center; font-size: 12px; color: var(--ink-3); }
.aip-epop-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 6px;
  box-shadow: inset 0 1px 0 rgba(var(--tint-rgb), 0.08);
}
.aip-epop-flabel { flex: none; font-size: 12px; color: var(--ink-2); }
.aip-epop-def { flex: 1; min-width: 0; }
.aip-epop-enter-active, .aip-epop-leave-active { transition: opacity 0.14s ease, translate 0.14s ease; }
.aip-epop-enter-from, .aip-epop-leave-to { opacity: 0; translate: 0 -4px; }
.aip .el-textarea__inner { border: 0; outline: none; }
/* buttons and the format switch: told apart by fill, not by a frame (heights are fixed, nothing moves) */
.aip .el-button, .aip-dialog .el-button { border: 0; }
.aip .el-button:not(.is-text):not(.is-link):not(.el-button--primary),
.aip-dialog .el-button:not(.is-text):not(.is-link):not(.el-button--primary) { background: var(--aip-l2); }
.aip .el-button:not(.is-text):not(.is-link):not(.el-button--primary):hover,
.aip-dialog .el-button:not(.is-text):not(.is-link):not(.el-button--primary):hover { background: var(--aip-l3); }
.aip .el-radio-group { align-self: flex-start; padding: 3px; border-radius: 10px; background: var(--aip-l2); gap: 2px; }
.aip .el-radio-button__inner {
  border: 0 !important;
  border-radius: 8px !important;
  background: transparent;
  box-shadow: none !important;
  color: var(--ink-2);
}
.aip .el-radio-button.is-active .el-radio-button__inner {
  color: var(--ink);
  background: var(--card-solid);
  box-shadow: 0 1px 3px rgba(var(--shade-rgb), 0.12) !important;
}
.aip .el-switch__core { border: 0; }
</style>
