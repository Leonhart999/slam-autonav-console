// Chart colors of the evaluation page (same values as SERIES in robot_console/eval_worker.py).
// Categorical slots are assigned in this fixed order and stay with a run while it is selected.
// SERIES and AXIS follow the light / dark look: both are reactive, so chart options and swatches that read
// them inside a computed or a template are rebuilt when the look changes. Exported figures keep the light set.
import { reactive, watch } from 'vue'
import { isDark } from '@/theme'

const LIGHT = {
  series: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
  axis: { line: '#cbd3df', split: '#e6ebf2', label: '#7a879d', ink: '#4a5872',
    tipBg: 'rgba(255,255,255,0.96)', tipInk: '#0b1526', paper: '#fff' },
}
// Same hues slot by slot, lifted for a dark plot area
const DARK = {
  series: ['#5b9df0', '#f08a5d', '#34c99a', '#f2b632', '#f08fb6', '#4cc04c', '#9084f2', '#f06b6b'],
  axis: { line: '#3a4762', split: '#232e45', label: '#8795ae', ink: '#b4c0d4',
    tipBg: 'rgba(22,31,51,0.96)', tipInk: '#e8eef8', paper: '#131b2d' },
}
const current = () => (isDark.value ? DARK : LIGHT)

export const SERIES: string[] = reactive([...current().series])
export const REFERENCE = '#7a879d'   // ground truth / RMSE reference lines (neutral, not a series color)
/** tipBg / tipInk: tooltip box; paper: fill of hollow markers */
export const AXIS = reactive({ ...current().axis })
watch(isDark, () => {
  SERIES.splice(0, SERIES.length, ...current().series)
  Object.assign(AXIS, current().axis)
}, { flush: 'sync' })
export const MONO = "'Cascadia Mono', 'JetBrains Mono', Consolas, monospace"
