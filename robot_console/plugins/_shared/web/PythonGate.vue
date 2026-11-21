<!-- Notice shown on a plugin page while its Python packages are not usable (not installed, installing, outdated,
     failed), with an Install button. Installing uses the console's plugin management route. -->
<script setup lang="ts">
import { ref } from 'vue'
import { ElAlert, ElButton, ElMessage } from 'element-plus'
import { useKit, type PythonStatus } from './kit'

const props = defineProps<{ python: PythonStatus | null }>()
const emit = defineEmits<{ changed: [] }>()
const kit = useKit()
const busy = ref(false)

async function install() {
  busy.value = true
  try {
    await kit.installPython()
    emit('changed')
  } catch (e) {
    ElMessage.error(kit.errorText(e))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <ElAlert
    v-if="props.python && props.python.state !== 'ready'" :closable="false" show-icon
    :type="props.python.state === 'failed' ? 'error' : 'warning'" class="pk-python"
  >
    <template #title>
      {{ kit.t(`common.python.${props.python.state}`, { error: props.python.error ?? '' }) }}
    </template>
    <div class="pk-python-body">
      <span class="mono pk-reqs">{{ kit.t('common.python.packages', { list: props.python.requirements.join('  ') }) }}</span>
      <div class="pk-python-actions">
        <ElButton
          v-if="props.python.state !== 'installing'" size="small" type="primary" :loading="busy" @click="install"
        >
          {{ kit.t('common.python.install') }}
        </ElButton>
        <a href="#/plugins"><ElButton size="small">{{ kit.t('common.python.manage') }}</ElButton></a>
      </div>
    </div>
  </ElAlert>
</template>

<style scoped>
.pk-python {
  margin-bottom: 12px;
}
.pk-python-body {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
  margin-top: 4px;
}
.pk-reqs {
  font-size: 12px;
  color: var(--ink-2);
  overflow-wrap: anywhere;
}
.pk-python-actions {
  display: flex;
  gap: 8px;
}
</style>
