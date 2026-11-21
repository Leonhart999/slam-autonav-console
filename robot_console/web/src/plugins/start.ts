// Start plugin frontends after the page is mounted. The SDK (with its extra Element Plus components) is only
// loaded when at least one active plugin has a frontend, so the console without plugins stays as small as before.
import { pluginApi } from './api'
import { loadPluginFrontends, registry } from './host'

export async function startPlugins() {
  try {
    const list = await pluginApi.list()
    if (list.plugins.some((p) => p.state === 'active' && p.frontend)) {
      const { installSdk } = await import('./sdk')
      installSdk()
    }
    await loadPluginFrontends(list.plugins)
  } catch {
    registry.loading = false      // backend offline: plugin pages say they are unavailable
  }
}
