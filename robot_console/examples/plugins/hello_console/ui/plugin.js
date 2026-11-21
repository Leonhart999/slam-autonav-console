// Frontend of the example plugin, written by hand without a build step: it only uses window.RobotConsole
// (plugin interface v1, docs/plugins.md). Real plugins usually write .vue files and build them with Vite into
// one script like this one (see docs/plugins.md, "Building a frontend").
(function () {
  const RC = window.RobotConsole
  const { h, ref, onMounted, defineComponent } = RC.vue
  const { GlassPanel, KpiTile, ElButton, ElAlert } = RC.ui
  const ID = 'hello_console'
  const t = RC.i18n.useT(ID)

  const HelloPage = defineComponent({
    name: 'HelloConsolePage',
    setup() {
      const data = ref(null)
      const error = ref('')
      const loading = ref(false)
      async function load() {
        loading.value = true
        try {
          data.value = await RC.request('GET', `/api/ext/${ID}/summary`)
          error.value = ''
        } catch (e) {
          error.value = RC.errorText(e)
        } finally {
          loading.value = false
        }
      }
      onMounted(load)
      return () => h('div', { class: 'hello-console' }, [
        h(GlassPanel, { title: t('title') }, {
          extra: () => h(ElButton, { size: 'small', loading: loading.value, onClick: load }, () => t('refresh')),
          default: () => [
            error.value ? h(ElAlert, { type: 'error', closable: false, title: error.value }) : null,
            data.value ? h('p', { class: 'hello-console__text' }, data.value.text) : null,
            h('p', { class: 'hello-console__hint' }, t('hint')),
          ],
        }),
        data.value ? h('div', { class: 'hello-console__kpis' }, [
          h(KpiTile, { label: t('stacks'), value: data.value.running.length, total: data.value.stacks, sub: t('stacksSub'), tone: 'ok' }),
          h(KpiTile, { label: t('maps'), value: data.value.maps, sub: t('mapsSub') }),
        ]) : null,
        data.value ? h('div', { class: 'hello-console__token' }, [
          h('span', null, `${t('token')}: `),
          h('span', { class: `chip ${data.value.token_set ? 'success' : 'info'}` }, data.value.token_set ? t('set') : t('unset')),
        ]) : null,
      ])
    },
  })

  RC.register({
    id: ID,
    messages: {
      'zh-CN': {
        title: '示例插件', refresh: '刷新',
        hint: '这段问候语来自插件后端 /api/ext/hello_console/summary，内容由插件设置决定；下面的数字是插件通过 ctx.api 读取的控制台数据。',
        stacks: '运行中的进程', stacksSub: '运行中 / 已配置', maps: '地图', mapsSub: '地图库里的地图',
        token: '示例密钥', tokenSub: '只显示是否已设置', set: '已设置', unset: '未设置',
        errors: { empty_greeting: '问候语是空的：请在插件页的设置里填写' },
      },
      en: {
        title: 'Hello plugin', refresh: 'Refresh',
        hint: 'The greeting comes from the plugin backend /api/ext/hello_console/summary and follows the plugin settings; the numbers below are console data the plugin read through ctx.api.',
        stacks: 'Running processes', stacksSub: 'running / configured', maps: 'Maps', mapsSub: 'maps in the library',
        token: 'Example secret', tokenSub: 'only shows whether it is set', set: 'Set', unset: 'Not set',
        errors: { empty_greeting: 'The greeting is empty: fill it in under Settings on the Plugins page' },
      },
    },
    pages: [{ path: 'main', title: { zh: '示例插件', en: 'Hello plugin' }, component: HelloPage }],
  })
})()
