// Entry of the eye_care_ui extension plugin: registers the anti-blue-light theme,
// header quick-toggle chip, and tuning control page.
import HeaderToggle from './HeaderToggle.vue'
import Page from './Page.vue'
import { messages } from './messages'
import { PLUGIN_ID, THEME_ID } from './sdk'
import { EYE_CARE_CHARTS, EYE_CARE_SCENE, EYE_CARE_TOKENS, initEyeCareRuntime } from './themeDef'

window.RobotConsole.register({
  id: PLUGIN_ID,
  messages,
  themes: [
    {
      id: THEME_ID,
      label: { zh: '护眼防蓝光', en: 'Eye Care' },
      base: 'light',
      pageColor: '#efe6d1',
      tokens: EYE_CARE_TOKENS,
      scene: EYE_CARE_SCENE,
      charts: EYE_CARE_CHARTS,
    },
  ],
  header: [HeaderToggle],
  pages: [{ path: 'main', title: { zh: '护眼防蓝光', en: 'Eye Care' }, component: Page }],
})

initEyeCareRuntime()
