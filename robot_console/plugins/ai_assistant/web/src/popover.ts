// Menus that open upwards from a chip under the input (ModelMenu.vue, AccessMenu.vue). The panel clips its content,
// so a menu is sized and moved to stay inside it: never wider than the panel, never taller than the room between the
// chip and the panel's top edge (the menu scrolls instead), shifted left when it would cross the right edge. The
// placement follows the panel while it is resized. Outside clicks and Escape close the menu.
import { nextTick, onUnmounted, ref, watch, type Ref } from 'vue'

const MARGIN = 8 // from the panel's edges
const GAP = 8 // between the chip and the menu

export function usePanelMenu(root: Ref<HTMLElement | null>, prefWidth: number) {
  const open = ref(false)
  const menuStyle = ref<Record<string, string>>({})
  let observer: ResizeObserver | null = null

  function place() {
    const chip = root.value
    const panel = chip?.closest<HTMLElement>('.aia-panel')
    if (!chip || !panel) return
    const p = panel.getBoundingClientRect()
    const c = chip.getBoundingClientRect()
    const width = Math.max(160, Math.min(prefWidth, p.width - 2 * MARGIN))
    const left = Math.max(p.left + MARGIN, Math.min(c.left, p.right - MARGIN - width))
    const maxHeight = Math.max(96, c.top - p.top - MARGIN - GAP)
    menuStyle.value = {
      width: `${Math.round(width)}px`,
      left: `${Math.round(left - c.left)}px`,
      maxHeight: `${Math.floor(maxHeight)}px`,
      // grow out of the chip that opened it
      transformOrigin: `${Math.round(c.left - left + Math.min(c.width, 40) / 2)}px 100%`,
    }
  }

  function onDoc(e: PointerEvent) {
    if (root.value && !root.value.contains(e.target as Node)) open.value = false
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') open.value = false
  }

  function release() {
    observer?.disconnect()
    observer = null
    document.removeEventListener('pointerdown', onDoc, true)
    document.removeEventListener('keydown', onKey, true)
  }

  watch(open, (o) => {
    release()
    if (!o) return
    place()
    document.addEventListener('pointerdown', onDoc, true)
    document.addEventListener('keydown', onKey, true)
    const panel = root.value?.closest<HTMLElement>('.aia-panel')
    if (panel && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => place())
      observer.observe(panel)
    }
    void nextTick(place)
  })
  // watchers stop with the component, so the listeners are removed here as well
  onUnmounted(release)

  return { open, menuStyle, place }
}
