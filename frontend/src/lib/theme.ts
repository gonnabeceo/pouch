import { useSyncExternalStore } from "react"

export type ThemePref = "system" | "light" | "dark"

const KEY = "pouch.theme"
const CHANGE = "pouch:theme"
const media = window.matchMedia("(prefers-color-scheme: dark)")

function readPref(): ThemePref {
  try {
    const value = localStorage.getItem(KEY)
    return value === "light" || value === "dark" ? value : "system"
  } catch {
    return "system"
  }
}

const isDark = (pref: ThemePref) => (pref === "system" ? media.matches : pref === "dark")

/**
 * Переключает класс .dark. Если задана точка (кнопка переключателя), новая тема
 * раскрывается кругом из неё через View Transitions API, иначе — плавная смена кадра.
 */
function apply(pref: ThemePref, origin?: { x: number; y: number }) {
  const root = document.documentElement
  const dark = isDark(pref)
  if (root.classList.contains("dark") === dark) return
  const flip = () => root.classList.toggle("dark", dark)
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches
  if (!document.startViewTransition || reduced) return flip()

  root.classList.toggle("theme-reveal", !!origin)
  const transition = document.startViewTransition(flip)
  transition.finished.finally(() => root.classList.remove("theme-reveal"))
  if (!origin) return
  transition.ready.then(() => {
    const radius = Math.hypot(Math.max(origin.x, innerWidth - origin.x), Math.max(origin.y, innerHeight - origin.y))
    root.animate(
      { clipPath: [`circle(0px at ${origin.x}px ${origin.y}px)`, `circle(${radius}px at ${origin.x}px ${origin.y}px)`] },
      { duration: 500, easing: "cubic-bezier(0.4, 0, 0.2, 1)", pseudoElement: "::view-transition-new(root)" },
    )
  })
}

export function initTheme() {
  apply(readPref())
  media.addEventListener("change", () => apply(readPref()))
}

export function setTheme(pref: ThemePref, origin?: { x: number; y: number }) {
  try {
    if (pref === "system") localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, pref)
  } catch {
    /* без запоминания тема всё равно сменится */
  }
  apply(pref, origin)
  window.dispatchEvent(new Event(CHANGE))
}

const subscribe = (cb: () => void) => {
  window.addEventListener(CHANGE, cb)
  media.addEventListener("change", cb)
  return () => {
    window.removeEventListener(CHANGE, cb)
    media.removeEventListener("change", cb)
  }
}

export function useTheme() {
  const pref = useSyncExternalStore(subscribe, readPref)
  return { pref, dark: isDark(pref) }
}
