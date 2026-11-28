<!-- Settings → About: version and update check, project links, runtime environment and diagnostics. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Refresh, Link, CopyDocument, FolderOpened, Star, Close } from '@element-plus/icons-vue'
import RadarMark from '@/components/RadarMark.vue'
import { openFolder } from '@/openFolder'
import { Cpu } from '@element-plus/icons-vue'
import SettingsCard from './SettingsCard.vue'
import { settingsMessages } from './messages'
import { settingsApi, type AboutInfo } from './api'
import { checkUpdate, checking, update } from './update'

const { t, locale } = useI18n({ messages: settingsMessages })

const STAR_KEY = 'robot_console.star_dismissed'
const about = ref<AboutInfo | null>(null)
const starHidden = ref(localStorage.getItem(STAR_KEY) === '1')
onMounted(async () => {
  void checkUpdate(false)
  try { about.value = await settingsApi.about() } catch { about.value = null }
})

const repoUrl = computed(() => `https://github.com/${about.value?.repo ?? 'Leonhart999/slam-autonav-console'}`)
const links = computed(() => [
  { label: t('settings.about.links.repo'), href: repoUrl.value },
  { label: t('settings.about.links.changelog'), href: `${repoUrl.value}/releases` },
  { label: t('settings.about.links.demo'), href: 'https://leonhart999.github.io/slam-autonav-console/' },
  { label: t('settings.about.links.pluginDocs'), href: `${repoUrl.value}/blob/main/robot_console/docs/plugins.md` },
])

const checkedAt = computed(() => {
  const c = update.value?.checked_at
  if (c == null) return ''
  const d = typeof c === 'number' ? new Date(c * 1000) : new Date(c)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(locale.value === 'en' ? 'en-GB' : 'zh-CN', { hour12: false })
})

const env = computed(() => {
  const a = about.value
  if (!a) return []
  return [
    [t('settings.about.env.ros'), `${a.ros_distro ?? '—'} · ${a.rmw} · ROS_DOMAIN_ID ${a.domain_id}`],
    [t('settings.about.env.os'), `${a.os}${a.wsl ? ' (WSL2)' : ''} · ${a.kernel}`],
    [t('settings.about.env.python'), `${a.python}${a.venv ? ` · ${a.venv}` : ''}`],
    [t('settings.about.env.evo'), a.evo_ok ? (a.evo_python ?? '') : `${a.evo_python ?? ''} (${t('settings.about.env.evoMissing')})`],
    [t('settings.about.env.plugins'), a.plugins_total == null ? '—'
      : t('settings.about.env.pluginCount', { enabled: a.plugins_enabled ?? 0, total: a.plugins_total })],
    [t('settings.about.env.server'), `${a.host}:${a.port}`],
    [t('settings.about.env.dataRoot'), a.data_root],
    [t('settings.about.env.config'), a.config_files.join(' + ')],
  ] as [string, string][]
})

async function copyDiag() {
  const a = about.value
  const lines = [`robot_console ${a?.version ?? '?'}`, ...env.value.map(([k, v]) => `${k}: ${v}`),
    `browser: ${navigator.userAgent}`]
  try {
    await navigator.clipboard.writeText(lines.join('\n'))
    ElMessage.success(t('settings.about.env.copied'))
  } catch {
    ElMessage.error(t('settings.about.env.copyFailed'))
  }
}
async function openLogs() {
  try {
    const s = await settingsApi.storage()
    await openFolder(s.folders.find((f) => f.key === 'logs')?.path)
  } catch { /* openFolder reports its own errors */ }
}
function hideStar() {
  starHidden.value = true
  try { localStorage.setItem(STAR_KEY, '1') } catch { /* ignore */ }
}
</script>

<template>
  <div>
    <section class="hero-card glass">
      <div class="hero">
        <RadarMark :size="56" />
        <div class="grow">
          <h3>{{ t('settings.about.appName') }}</h3>
          <div class="ver">
            <span class="ver-chip mono">{{ t('settings.about.version', { v: about?.version ?? update?.current ?? '…' }) }}</span>
            <span v-if="about?.ros_distro" class="ver-chip mono soft">ROS 2 {{ about.ros_distro }}</span>
          </div>
        </div>
        <a v-if="update?.newer && update.url" :href="update.url" target="_blank" rel="noopener" class="el-button el-button--primary">
          {{ t('settings.about.update.view', { v: update.latest }) }}
        </a>
        <el-button v-else type="primary" :icon="Refresh" :loading="checking" @click="checkUpdate(true)">
          {{ checking ? t('settings.about.update.checking') : t('settings.about.update.button') }}
        </el-button>
      </div>
      <div v-if="update" class="update" :class="{ newer: update.newer, bad: !!update.error }">
        <span class="status-dot" />
        <span v-if="update.error">{{ t('settings.about.update.failed', { reason: update.error }) }}</span>
        <span v-else-if="update.newer">{{ t('settings.about.update.newer', { v: update.latest }) }}</span>
        <span v-else>{{ t('settings.about.update.upToDate') }} · {{ update.current }}</span>
        <span v-if="checkedAt" class="when">{{ t('settings.about.update.checkedAt', { time: checkedAt }) }}</span>
        <el-button v-if="update.newer || update.error" link :icon="Refresh" :loading="checking" @click="checkUpdate(true)" />
      </div>
      <div class="links">
        <a v-for="l in links" :key="l.href" :href="l.href" target="_blank" rel="noopener" class="link">
          <el-icon><Link /></el-icon>{{ l.label }}
        </a>
      </div>
      <div v-if="!starHidden" class="star">
        <el-icon :size="18" class="star-icon"><Star /></el-icon>
        <span class="grow">{{ t('settings.about.star.text') }}</span>
        <a :href="repoUrl" target="_blank" rel="noopener" class="link">{{ t('settings.about.star.action') }}</a>
        <el-button link :icon="Close" :aria-label="t('settings.about.star.dismiss')" @click="hideStar" />
      </div>
    </section>

    <SettingsCard :title="t('settings.about.env.title')" :icon="Cpu" :hint="t('settings.about.env.sub')">
      <div class="kv">
        <template v-for="[k, v] in env" :key="k">
          <div class="k">{{ k }}</div>
          <div class="v mono">{{ v }}</div>
        </template>
      </div>
    </SettingsCard>
    <div class="actions">
      <el-button :icon="CopyDocument" :disabled="!about" @click="copyDiag">{{ t('settings.about.env.copy') }}</el-button>
      <el-button :icon="FolderOpened" :disabled="!about" @click="openLogs">
        {{ t('settings.about.env.openLogs') }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.hero-card { position: relative; border-radius: var(--radius); overflow: hidden; margin-top: 2px; }
.hero {
  position: relative;
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 24px 24px 22px;
  background:
    radial-gradient(420px 160px at 0% 0%, rgba(var(--accent-rgb), 0.16), transparent 70%),
    radial-gradient(360px 160px at 100% 100%, rgba(99, 102, 241, 0.12), transparent 70%);
}
.hero h3 { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: 0.02em; color: var(--ink); }
.ver { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.ver-chip {
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 12px;
  color: var(--accent);
  background: rgba(var(--accent-rgb), 0.1);
  box-shadow: inset 0 0 0 1px rgba(var(--accent-rgb), 0.18);
}
.ver-chip.soft { color: var(--ink-2); background: rgba(var(--tint-rgb), 0.06); box-shadow: none; }
.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ok);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 18%, transparent);
}
.update.newer .status-dot { background: var(--warn); box-shadow: 0 0 0 3px color-mix(in srgb, var(--warn) 18%, transparent); }
.update.bad .status-dot { background: var(--bad); box-shadow: 0 0 0 3px color-mix(in srgb, var(--bad) 18%, transparent); }
.grow { flex: 1; min-width: 0; }
.update {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 12px;
  padding: 12px 22px;
  border-top: 1px solid var(--hairline);
  font-size: 13px;
  color: var(--ink-2);
}
.update.newer { color: var(--warn); }
.update.bad { color: var(--bad); }
.when { font-size: 12px; color: var(--ink-3); }
.links { display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 22px; border-top: 1px solid var(--hairline); }
.link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  border-radius: 9px;
  border: 1px solid var(--hairline);
  background: rgba(var(--paper-rgb), 0.6);
  color: var(--ink);
  font-size: 13px;
  text-decoration: none;
}
.link { transition: color 0.15s, border-color 0.15s, transform 0.15s; }
.link:hover { color: var(--accent); border-color: rgba(var(--accent-rgb), 0.4); transform: translateY(-1px); }
.star {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 22px;
  border-top: 1px solid var(--hairline);
  background: rgba(var(--accent-rgb), 0.06);
  font-size: 13px;
  color: var(--ink-2);
}
.star-icon { color: #f59e0b; }
.kv { display: grid; grid-template-columns: 180px 1fr; }
.k, .v { padding: 11px 18px; border-top: 1px solid var(--hairline); font-size: 13px; }
.k:nth-child(1), .v:nth-child(2) { border-top: 0; }
.k { color: var(--ink-3); }
.v { font-size: 12.5px; color: var(--ink); word-break: break-all; }
.actions { display: flex; gap: 8px; margin: 14px 4px 0; }
</style>
