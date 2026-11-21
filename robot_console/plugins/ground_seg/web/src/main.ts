// Entry of the ground segmentation frontend: built into ../ui/plugin.js by the console's web toolchain
// (cd web && npm run build:plugins). vue and element-plus come from window.RobotConsole at run time.
import Page from './Page.vue'
import { messages } from './messages'

window.RobotConsole.register({
  id: 'ground_seg',
  messages,
  pages: [{ path: 'main', title: { zh: '地面分割', en: 'Ground segmentation' }, component: Page }],
})
