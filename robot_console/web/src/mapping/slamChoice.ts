// SLAM choice on the mapping and playback pages. console.yaml slam_profiles may mark a profile as a variant of
// another one (variant_of + options, e.g. FAST-LIO2 LiDAR only with the ground-vehicle constraint =
// options [ground, lidar]). The pages show one algorithm button per base profile and one switch per option;
// the switches pick the profile (stack) whose option set matches.
import { computed, type ComputedRef, type Ref } from 'vue'
import type { SlamOption } from './stream'

/** option switches in display order (keys of console.yaml slam_profiles.<name>.options) */
export const SLAM_SWITCHES = ['lidar', 'ground'] as const

export const baseOf = (o: SlamOption) => o.variant_of ?? o.name
const optionsOf = (o: SlamOption | undefined) => o?.options ?? []
const sameSet = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((x) => b.includes(x))

export interface SlamChoiceInput {
  /** profiles that have a stack */
  all: ComputedRef<SlamOption[]>
  /** where the data comes from: only profiles with this source are offered */
  source: 'sim' | 'bag'
  /** the profile whose stack is running (locks the choice) */
  active: ComputedRef<SlamOption | undefined>
  /** chosen base profile (older saved settings may hold a variant name, that is fine) */
  chosen: Ref<string>
  /** options switched on by the user */
  chosenOpts: Ref<string[]>
  /** can the profile run on the current data (e.g. the bag has the IMU it needs) */
  fits?: (o: SlamOption) => boolean
  /** base profile to fall back to */
  fallback?: ComputedRef<string | undefined>
}

export function useSlamChoice(p: SlamChoiceInput) {
  const fits = (o: SlamOption) => (p.fits ? p.fits(o) : true)
  const usable = computed(() => p.all.value.filter((o) => (o.sources ?? ['sim', 'bag']).includes(p.source)))
  const family = (base: string) => usable.value.filter((o) => baseOf(o) === base)

  // one button per base profile that has a usable member
  const bases = computed(() => {
    const names = [...new Set(usable.value.map(baseOf))]
    return names.map((n) => p.all.value.find((o) => o.name === n)).filter((o): o is SlamOption => !!o)
  })
  const baseFits = (b: SlamOption) => family(b.name).some(fits)

  const selectedBase = computed(() => {
    const a = p.active.value
    if (a) return p.all.value.find((o) => o.name === baseOf(a)) ?? a
    const want = p.all.value.find((o) => o.name === p.chosen.value)
    const wantBase = want ? baseOf(want) : p.fallback?.value
    const list = bases.value
    return list.find((b) => b.name === wantBase && baseFits(b)) ?? list.find(baseFits) ?? list[0]
  })

  // switches offered for the selected base: options that occur in its family
  const switches = computed(() => {
    const fam = selectedBase.value ? family(selectedBase.value.name) : []
    return SLAM_SWITCHES.filter((k) => fam.some((o) => optionsOf(o).includes(k)))
  })
  const pick = (opts: readonly string[]) =>
    selectedBase.value ? family(selectedBase.value.name).find((o) => sameSet(optionsOf(o), opts)) : undefined

  // The chosen options if that combination exists and fits the data; otherwise add the one option that makes it
  // fit (a bag without IMU forces 'lidar'), otherwise drop options until a profile exists.
  const resolved = computed<{ slam: SlamOption | undefined; forced: string[] }>(() => {
    const a = p.active.value
    if (a) return { slam: a, forced: [] }
    const base = selectedBase.value
    if (!base) return { slam: undefined, forced: [] }
    const chosen = p.chosenOpts.value.filter((k) => switches.value.includes(k as never))
    const exact = pick(chosen)
    if (exact && fits(exact)) return { slam: exact, forced: [] }
    for (const k of switches.value) {
      if (chosen.includes(k)) continue
      const o = pick([...chosen, k])
      if (o && fits(o)) return { slam: o, forced: [k] }
    }
    for (const k of chosen) {
      const o = pick(chosen.filter((x) => x !== k))
      if (o && fits(o)) return { slam: o, forced: [] }
    }
    return { slam: exact ?? family(base.name).find(fits) ?? base, forced: [] }
  })
  const selectedSlam = computed(() => resolved.value.slam)
  const forced = computed(() => resolved.value.forced)

  const optionOn = (k: string) => optionsOf(selectedSlam.value).includes(k)
  // a switch can be flipped when nothing runs, it is not forced, and the resulting combination exists and fits
  const optionDisabled = (k: string) => {
    if (p.active.value || forced.value.includes(k)) return true
    const cur = optionsOf(selectedSlam.value)
    const next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]
    const o = pick(next)
    return !o || !fits(o)
  }
  // keeps the user's own choice (a forced option is not stored: the next bag may have an IMU)
  function setOption(k: string, on: boolean) {
    const cur = optionsOf(selectedSlam.value).filter((x) => !forced.value.includes(x))
    p.chosenOpts.value = on ? [...new Set([...cur, k])] : cur.filter((x) => x !== k)
    if (selectedBase.value) p.chosen.value = selectedBase.value.name
  }

  return { bases, baseFits, selectedBase, selectedSlam, switches, forced, optionOn, optionDisabled, setOption }
}

/** saved option list (or the older ground flag of the pages) */
export function savedOptions(list: unknown, ground: unknown): string[] {
  if (Array.isArray(list)) return list.filter((x): x is string => typeof x === 'string')
  return ground === true ? ['ground'] : []
}
