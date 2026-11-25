// Entry of the AI assistant frontend: built into ../ui/plugin.js by the console's web toolchain
// (cd web && npm run build:plugins). vue and element-plus come from window.RobotConsole at run time.
import Assistant from './Assistant.vue'
import ManagePage from './ManagePage.vue'
import { messages } from './messages'
import { PLUGIN_ID } from './sdk'

window.RobotConsole.register({
  id: PLUGIN_ID,
  messages,
  header: [Assistant],
  pages: [{ path: 'providers', title: { zh: 'AI 模型服务', en: 'AI model services' }, component: ManagePage }],
})
