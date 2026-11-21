// Entry of the dynamic object removal frontend: built into ../ui/plugin.js by the console's web toolchain
// (cd web && npm run build:plugins). vue and element-plus come from window.RobotConsole at run time.
import Page from './Page.vue'
import { messages } from './messages'

window.RobotConsole.register({
  id: 'dynamic_removal',
  messages,
  pages: [{ path: 'main', title: { zh: '动态物体去除', en: 'Dynamic removal' }, component: Page }],
})
