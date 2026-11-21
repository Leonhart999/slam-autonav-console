// Texts of the shared parts (Python environment notice, job list), merged into each plugin's own messages
// under `common` (vue-i18n syntax: avoid { } @ | $ in texts other than {param})
export const commonMessages = {
  'zh-CN': {
    python: {
      missing: '这个插件要用的 Python 包还没安装。',
      installing: '正在安装 Python 包，通常需要一到几分钟……',
      outdated: '插件更新后需要的 Python 包变了，请重新安装。',
      failed: 'Python 包安装失败：{error}',
      install: '安装 Python 包',
      manage: '去插件页',
      packages: '需要：{list}',
    },
    jobs: {
      title: '任务',
      empty: '还没有任务',
      refresh: '刷新',
      cancel: '取消',
      remove: '删除记录',
      log: '日志',
      logTitle: '任务日志',
      logEmpty: '没有日志',
      close: '关闭',
      expand: '展开结果',
      collapse: '收起',
      created: '提交于 {time}',
      took: '用时 {d}',
      state: {
        queued: '排队中',
        running: '运行中',
        succeeded: '完成',
        failed: '失败',
        cancelled: '已取消',
        interrupted: '已中断',
      },
    },
  },
  en: {
    python: {
      missing: 'The Python packages this plugin uses are not installed yet.',
      installing: 'Installing the Python packages; this usually takes one to a few minutes…',
      outdated: 'The plugin was updated and needs other Python packages: install them again.',
      failed: 'Installing the Python packages failed: {error}',
      install: 'Install Python packages',
      manage: 'Plugins page',
      packages: 'Needs: {list}',
    },
    jobs: {
      title: 'Jobs',
      empty: 'No jobs yet',
      refresh: 'Refresh',
      cancel: 'Cancel',
      remove: 'Remove record',
      log: 'Log',
      logTitle: 'Job log',
      logEmpty: 'No log',
      close: 'Close',
      expand: 'Show result',
      collapse: 'Hide',
      created: 'Submitted {time}',
      took: 'took {d}',
      state: {
        queued: 'Queued',
        running: 'Running',
        succeeded: 'Done',
        failed: 'Failed',
        cancelled: 'Cancelled',
        interrupted: 'Interrupted',
      },
    },
  },
}

/** A plugin's messages with the shared texts under `common` */
export function withCommon<T extends { 'zh-CN': Record<string, unknown>; en: Record<string, unknown> }>(m: T) {
  return {
    'zh-CN': { ...m['zh-CN'], common: commonMessages['zh-CN'] },
    en: { ...m.en, common: commonMessages.en },
  }
}
