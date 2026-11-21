<!-- Settings of one plugin, generated from the `settings` list of its plugin.yaml.
     Secrets are never sent to the page: the field is empty, its placeholder says whether a value is set; typing a
     new value replaces it, "Clear" removes it. Values apply without a restart (the plugin reads them on use). -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { pluginApi } from '@/plugins/api'
import type { PluginInfo, SettingSpec, SettingValue } from '@/plugins/types'

const props = defineProps<{ plugin: PluginInfo | null }>()
const open = defineModel<boolean>({ default: false })
const { t } = useI18n()

const loading = ref(false)
const saving = ref(false)
const failed = ref('')
const values = ref<Record<string, SettingValue>>({})
const secretSet = ref<Record<string, boolean>>({})
const clear = ref<Set<string>>(new Set())
const specs = computed<SettingSpec[]>(() => props.plugin?.settings ?? [])
const title = computed(() => (props.plugin?.manifest ? pick(props.plugin.manifest.name) : props.plugin?.id ?? ''))

async function load() {
  if (!props.plugin) return
  loading.value = true
  failed.value = ''
  clear.value = new Set()
  try {
    const r = await pluginApi.settings(props.plugin.id)
    const v: Record<string, SettingValue> = { ...r.values }
    for (const s of specs.value) if (s.type === 'secret') v[s.key] = ''
    values.value = v
    secretSet.value = r.secrets
  } catch (e) {
    failed.value = errorText(e)
  } finally {
    loading.value = false
  }
}
watch(open, (o) => { if (o) void load() })

function toggleClear(key: string) {
  const next = new Set(clear.value)
  if (next.has(key)) next.delete(key)
  else { next.add(key); values.value[key] = '' }
  clear.value = next
}

async function save() {
  if (!props.plugin) return
  saving.value = true
  try {
    const r = await pluginApi.saveSettings(props.plugin.id, values.value, [...clear.value])
    secretSet.value = r.secrets
    ElMessage.success(t('pluginsPage.settings.saved'))
    open.value = false
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    saving.value = false
  }
}

const text = (key: string) => String(values.value[key] ?? '')

function secretPlaceholder(key: string) {
  if (clear.value.has(key)) return t('pluginsPage.settings.secretWillClear')
  return secretSet.value[key] ? t('pluginsPage.settings.secretSet') : t('pluginsPage.settings.secretUnset')
}
</script>

<template>
  <el-dialog
    v-model="open" :title="t('pluginsPage.settings.title', { name: title })"
    width="min(560px, calc(100vw - 32px))" append-to-body destroy-on-close
  >
    <div v-loading="loading" class="plugin-settings">
      <el-alert v-if="failed" type="error" :closable="false" :title="failed" />
      <el-form v-else label-position="top" @submit.prevent>
        <el-form-item v-for="s in specs" :key="s.key" :label="pick(s.label)">
          <el-input
            v-if="s.type === 'string'" :model-value="text(s.key)" :maxlength="s.max_length" clearable
            @update:model-value="(v: string) => (values[s.key] = v)"
          />
          <el-input
            v-else-if="s.type === 'text'" :model-value="text(s.key)" type="textarea"
            :autosize="{ minRows: 3, maxRows: 10 }" :maxlength="s.max_length"
            @update:model-value="(v: string) => (values[s.key] = v)"
          />
          <el-input-number
            v-else-if="s.type === 'number' || s.type === 'integer'" :model-value="Number(values[s.key])"
            :min="s.min" :max="s.max" :step="s.step ?? 1" :precision="s.type === 'integer' ? 0 : undefined"
            controls-position="right" @update:model-value="(v: number | undefined | null) => (values[s.key] = v ?? s.min ?? 0)"
          />
          <el-switch
            v-else-if="s.type === 'boolean'" :model-value="values[s.key] === true"
            @update:model-value="(v: string | number | boolean) => (values[s.key] = v === true)"
          />
          <el-select v-else-if="s.type === 'select'" v-model="values[s.key]" class="select">
            <el-option v-for="o in s.options" :key="String(o.value)" :value="o.value" :label="pick(o.label)" />
          </el-select>
          <div v-else-if="s.type === 'secret'" class="secret">
            <el-input
              :model-value="text(s.key)" type="password" show-password autocomplete="new-password"
              :maxlength="s.max_length" :placeholder="secretPlaceholder(s.key)" :disabled="clear.has(s.key)"
              @update:model-value="(v: string) => (values[s.key] = v)"
            />
            <el-button v-if="secretSet[s.key]" :type="clear.has(s.key) ? 'warning' : 'default'" @click="toggleClear(s.key)">
              {{ clear.has(s.key) ? t('pluginsPage.settings.keep') : t('pluginsPage.settings.clear') }}
            </el-button>
          </div>
          <div v-if="pick(s.help)" class="help">{{ pick(s.help) }}</div>
        </el-form-item>
        <div v-if="!specs.length" class="help">{{ t('pluginsPage.settings.none') }}</div>
      </el-form>
    </div>
    <template #footer>
      <el-button @click="open = false">{{ t('pluginsPage.settings.cancel') }}</el-button>
      <el-button type="primary" :loading="saving" :disabled="loading || !!failed || !specs.length" @click="save">
        {{ t('pluginsPage.settings.save') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.plugin-settings {
  min-height: 80px;
}
.select {
  width: 100%;
}
.secret {
  display: flex;
  gap: 8px;
  width: 100%;
}
.help {
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink-3);
}
</style>
