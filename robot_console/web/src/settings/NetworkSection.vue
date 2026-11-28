<!-- Settings → Network & connection: console service, ROS 2 environment (read-only), proxies and mirrors. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Connection } from '@element-plus/icons-vue'
import { errorText } from '@/messages'
import { Cpu, Monitor } from '@element-plus/icons-vue'
import SettingsCard from './SettingsCard.vue'
import SettingRow from './SettingRow.vue'
import ConfigRow from './ConfigRow.vue'
import { settingsMessages } from './messages'
import { draft, payload } from './store'
import { settingsApi, type AboutInfo, type NetTestResult } from './api'

const { t } = useI18n({ messages: settingsMessages })
const m = (k: string) => `settings.network.${k}`

const about = ref<AboutInfo | null>(null)
onMounted(async () => {
  try { about.value = await settingsApi.about() } catch { about.value = null }
})
const host = computed(() => String(payload.value?.read_only['server.host'] ?? about.value?.host ?? '127.0.0.1'))

const testing = ref(false)
const results = ref<NetTestResult[]>([])
async function test() {
  testing.value = true
  results.value = []
  const str = (k: string) => (typeof draft[k] === 'string' ? draft[k] as string : '')
  const urls = Array.isArray(draft['plugins.market.index_urls']) ? draft['plugins.market.index_urls'] as string[] : []
  try {
    const r = await settingsApi.networkTest({
      // same fallbacks as the backend: the market falls back to the pip proxy; pip uses only its own
      proxy: str('plugins.market.proxy') || str('plugins.pip.proxy'),
      pip_proxy: str('plugins.pip.proxy'),
      pip_index_url: str('plugins.pip.index_url'),
      ...(urls[0] ? { market_index_url: String(urls[0]) } : {}),
    })
    results.value = r.results
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div>
    <SettingsCard :title="t('settings.network.cards.service')" :icon="Monitor" scope="consoleMixed">
      <SettingRow :label="t(m('host.label'))" :sub="t(m('host.sub'))" :tip="t(m('host.tip'))" read-only>
        <span class="mono val">{{ host }}</span>
      </SettingRow>
      <ConfigRow k="server.port" :msg="m('port')" kind="number" :min="1024" :max="65535" :precision="0" />
      <ConfigRow k="server.status_rate" :msg="m('statusRate')" kind="number" :min="0.2" :max="20" :step="0.5" unit="Hz" />
    </SettingsCard>

    <SettingsCard :title="t('settings.network.cards.ros')" :icon="Cpu" :hint="t('settings.network.cards.rosSub')">
      <SettingRow :label="t(m('domainId.label'))" :sub="t(m('domainId.sub'))" :tip="t(m('domainId.tip'))" read-only>
        <span class="mono val">{{ about?.domain_id ?? '—' }}</span>
      </SettingRow>
      <SettingRow :label="t(m('rmw.label'))" :sub="t(m('rmw.sub'))" :tip="t(m('rmw.tip'))" read-only>
        <span class="mono val">{{ about?.rmw ?? '—' }}</span>
      </SettingRow>
    </SettingsCard>

    <SettingsCard :title="t('settings.network.cards.proxy')" :icon="Connection" scope="console">
      <ConfigRow k="plugins.market.proxy" :msg="m('marketProxy')" placeholder="http://127.0.0.1:7890" />
      <ConfigRow k="plugins.pip.proxy" :msg="m('pipProxy')" placeholder="http://127.0.0.1:7890" />
      <ConfigRow k="plugins.pip.index_url" :msg="m('pipIndex')" placeholder="https://pypi.tuna.tsinghua.edu.cn/simple" />
      <ConfigRow k="plugins.market.index_urls" :msg="m('marketIndex')" kind="list" stacked />
      <SettingRow :label="t(m('test.label'))" :sub="t(m('test.sub'))" :tip="t(m('test.tip'))">
        <div class="results">
          <span v-for="r in results" :key="r.target" class="res" :class="r.ok ? 'ok' : 'bad'" :title="r.error ?? r.url">
            {{ t(m(`test.${r.target}`)) }} {{ r.ok ? t(m('test.ms'), { ms: r.ms ?? 0 }) : t(m('test.fail')) }}
          </span>
        </div>
        <el-button :icon="Connection" :loading="testing" @click="test">{{ t(m('test.button')) }}</el-button>
      </SettingRow>
    </SettingsCard>
  </div>
</template>

<style scoped>
.val { font-size: 12.5px; color: var(--ink-2); }
.results { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 4px 12px; }
.res { font-size: 12px; white-space: nowrap; }
.res.ok { color: var(--ok); }
.res.bad { color: var(--bad); }
</style>
