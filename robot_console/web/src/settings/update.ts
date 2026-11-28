// Update check shared by the About section and the side menu (dot on the Settings button when a newer release
// exists). The backend asks GitHub Releases and caches the answer for 6 hours, so calling it at start-up is cheap.
import { ref } from 'vue'
import { isReadOnlyPreview } from '@/http'
import { settingsApi, type UpdateInfo } from './api'

export const update = ref<UpdateInfo | null>(null)
export const checking = ref(false)

export async function checkUpdate(force = false) {
  if (checking.value || isReadOnlyPreview()) return
  checking.value = true
  try {
    update.value = await settingsApi.update(force)
  } catch {
    // an older backend without the route, or the console is down: no dot, the About page shows its own error
  } finally {
    checking.value = false
  }
}
