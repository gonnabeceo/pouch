import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react"
import { t } from "./i18n"

// Режим стримера: суммы и количества за маской, адреса сокращены. Состояние запоминается,
// и читается синхронно до первой отрисовки, чтобы суммы не мелькнули при загрузке.
const KEY = "pouch.streamer"
export const MASK = "••••"
export const HOTKEY_LABEL = "Shift+H"

function read(): boolean {
  try {
    return localStorage.getItem(KEY) === "1"
  } catch {
    return false
  }
}

const StreamerContext = createContext<{ on: boolean; toggle: () => void }>({ on: false, toggle: () => {} })

export function StreamerProvider({ children }: { children: ReactNode }) {
  const [on, setOn] = useState(read)

  const toggle = useCallback(() => {
    setOn((value) => {
      try {
        localStorage.setItem(KEY, value ? "0" : "1")
      } catch {
        /* без запоминания */
      }
      return !value
    })
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      const typing = target?.closest("input, textarea, select, [contenteditable=true]")
      if (typing || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.shiftKey && e.code === "KeyH") {
        e.preventDefault()
        toggle()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [toggle])

  return <StreamerContext.Provider value={{ on, toggle }}>{children}</StreamerContext.Provider>
}

export const useStreamer = () => useContext(StreamerContext)

/** Сумма или количество: в режиме стримера заменяется маской. */
export function Hide({ children }: { children: ReactNode }) {
  const { on } = useStreamer()
  return on ? <span aria-label={t("скрыто")}>{MASK}</span> : <>{children}</>
}
