// Chart colors of the evaluation page (same values as SERIES in robot_console/eval_worker.py).
// Categorical slots are assigned in this fixed order and stay with a run while it is selected.
// SERIES and AXIS follow the active appearance (light, dark, or a UI theme plugin): both are reactive,
// so chart options and swatches that read them inside a computed or a template are rebuilt when the look changes.
// Exported figures keep the light set.
import { reactive, watch } from 'vue'
import { chartPalette } from '@/theme'

export const SERIES: string[] = reactive([...chartPalette.value.series])
export const REFERENCE = '#7a879d'   // ground truth / RMSE reference lines (neutral, not a series color)
/** tipBg / tipInk: tooltip box; paper: fill of hollow markers */
export const AXIS = reactive({ ...chartPalette.value.axis })
watch(chartPalette, (p) => {
  SERIES.splice(0, SERIES.length, ...p.series)
  Object.assign(AXIS, p.axis)
}, { flush: 'sync' })
export const MONO = "'Cascadia Mono', 'JetBrains Mono', Consolas, monospace"
