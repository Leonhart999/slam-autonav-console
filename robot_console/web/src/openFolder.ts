// "Open folder" buttons of the Maps and Evaluation pages: the backend opens the folder in Windows Explorer
// (WSL) or the Linux file manager; only folders inside the console's data folders are allowed.
import { ElMessage } from 'element-plus'
import { api } from './api'
import { i18n } from './i18n'
import { errorText } from './messages'

export async function openFolder(path: string | null | undefined) {
  if (!path) return
  try {
    const r = await api.openFolder(path)
    ElMessage.success(i18n.global.t('openFolder.opened', { path: r.opened || r.path }))
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}
