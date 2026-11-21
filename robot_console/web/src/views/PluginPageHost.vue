<!-- Shows a page a plugin registered (/#/ext/<id>/<page>). Errors inside the plugin's component are caught here,
     so a broken plugin shows an error card instead of a blank console. -->
<script setup lang="ts">
import { computed, onErrorCaptured, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import GlassPanel from '@/components/GlassPanel.vue'
import { pick } from '@/i18n'
import { registry } from '@/plugins/host'

const { t } = useI18n()
const route = useRoute()
const pluginId = computed(() => String(route.params.id ?? ''))
const page = computed(() => registry.pages.find((p) => p.pluginId === pluginId.value && p.path === route.params.page))
const crashed = ref<string | null>(null)

onErrorCaptured((err) => {
  crashed.value = err instanceof Error ? err.message : String(err)
  console.error(`plugin ${pluginId.value}:`, err)
  return false
})
watch(() => route.path, () => (crashed.value = null))
</script>

<template>
  <div class="plugin-page">
    <component :is="page.component" v-if="page && !crashed" />
    <GlassPanel v-else :title="page ? pick(page.title) : pluginId">
      <el-empty
        :image-size="72"
        :description="crashed ? t('pluginsPage.host.crashed', { error: crashed })
          : registry.loading ? t('pluginsPage.host.loading') : t('pluginsPage.host.unavailable')"
      >
        <router-link v-if="!registry.loading" to="/plugins">
          <el-button type="primary">{{ t('pluginsPage.host.manage') }}</el-button>
        </router-link>
      </el-empty>
    </GlassPanel>
  </div>
</template>

<style scoped>
.plugin-page {
  min-height: 0;
}
</style>
