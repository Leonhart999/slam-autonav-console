// Entry of the bag converter frontend: built into ../ui/plugin.js by the console's web toolchain
// (cd web && npm run build:plugins). vue and element-plus come from window.RobotConsole at run time.
import Page from './Page.vue'
import { messages } from './messages'

window.RobotConsole.register({
  id: 'bag_convert',
  messages,
  pages: [{ path: 'main', title: { zh: '数据包转换', en: 'Bag converter' }, component: Page }],
})
