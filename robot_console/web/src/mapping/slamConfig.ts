// Layered SLAM configuration (robot_console/slam_config.py): what the "Parameters" dialog of the SLAM step shows
// and the number of user changes shown on its button.
import { ref, watch } from 'vue'
import { api } from '@/api'
import type { Bilingual } from '@/types'

export type SlamParamValue = number | boolean | string | (number | boolean | string)[]

export interface SlamTunable {
  type: 'bool' | 'int' | 'float'
  label: Bilingual
  help: Bilingual
  min?: number
  max?: number
}

export interface SlamParamRow {
  key: string
  /** value of the validated base configuration */
  base: SlamParamValue
  /** value before the user's change: the base, or what the data source / option switches / console set */
  auto: SlamParamValue
  /** layer of the automatic value: base, input:<kind>, derived:<rule>, option:<name>, console */
  source: string
  /** value a derivation rule gives from the measured sensor facts while another value is in force (the rule is
   *  not enabled yet, or an option switch set the parameter); null when there is none */
  suggest: { value: SlamParamValue; rule: string } | null
  /** the user's value, null when unchanged */
  user: SlamParamValue | null
  value: SlamParamValue
  /** null: shown read only */
  tunable: SlamTunable | null
}

export interface SlamValidated {
  input: string
  options?: string[]
  data: Bilingual
  result: Bilingual
}

/** What the console measured of a sensor profile from a bag (sensor_profiles.measure_facts) */
export interface SensorFacts {
  rings: number | null
  points_per_scan: number | null
  scan_rate: number | null
  imu_rate: number | null
  bag: string
  measured: string
}

export interface SlamConfigInfo {
  stack: string
  name: string
  algorithm: string
  input: 'sim' | 'adapter'
  sensor: string | null
  /** what the user's values are stored for: sim or sensor:<profile> */
  scope: string
  options: string[]
  /** null: the simulated robot, or the sensor could not be measured */
  facts: SensorFacts | null
  validated: SlamValidated | null
  modified: boolean
  user_count: number
  params: SlamParamRow[]
}

/** Number of user changes of the selected SLAM on the current data source (0 when it has no parameters). */
export function useSlamConfigCount(stack: () => string | null, bag: () => string | null, connected: () => boolean) {
  const count = ref(0)
  let seq = 0
  async function reload() {
    const s = stack()
    const mine = ++seq
    if (!s || !connected()) {
      count.value = 0
      return
    }
    try {
      const info = await api.slamConfig(s, bag())
      if (mine === seq) count.value = info.user_count
    } catch {
      if (mine === seq) count.value = 0 // e.g. the bag's sensor profile has no configuration for this SLAM
    }
  }
  watch([stack, bag, connected], reload, { immediate: true })
  return { count, reload }
}
