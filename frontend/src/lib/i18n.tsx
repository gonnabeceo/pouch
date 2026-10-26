import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react"
import { EN, SERVER_PATTERNS } from "./i18n.en"

// Строки интерфейса пишутся по-русски прямо в коде и оборачиваются в t().
// Английский — словарь «русская строка → перевод» в i18n.en.ts; нет перевода — показываем оригинал.

export type Lang = "ru" | "en"

const KEY = "pouch.lang"

function detect(): Lang {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === "ru" || saved === "en") return saved
  } catch {
    /* язык из браузера */
  }
  return navigator.language?.toLowerCase().startsWith("ru") ? "ru" : "en"
}

let current: Lang = detect()

export const getLang = () => current
export const locale = () => (current === "ru" ? "ru-RU" : "en-US")

export function t(text: string, vars?: Record<string, string | number>): string {
  let out = current === "en" ? (EN[text] ?? text) : text
  if (vars) for (const [k, v] of Object.entries(vars)) out = out.replaceAll(`{${k}}`, String(v))
  return out
}

/** Сообщения и метки с сервера приходят по-русски: точное совпадение из словаря или шаблон. */
export function tServer(message: string): string {
  if (current !== "en" || !message) return message
  if (EN[message]) return EN[message]
  for (const [pattern, replace] of SERVER_PATTERNS) {
    const m = pattern.exec(message)
    if (m) return replace(m, tServer)
  }
  return message
}

const I18nContext = createContext<{ lang: Lang; setLang: (lang: Lang) => void }>({
  lang: current,
  setLang: () => {},
})

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(current)

  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])

  const setLang = useCallback((next: Lang) => {
    current = next
    try {
      localStorage.setItem(KEY, next)
    } catch {
      /* без запоминания */
    }
    setLangState(next)
  }, [])

  return <I18nContext.Provider value={{ lang, setLang }}>{children}</I18nContext.Provider>
}

/** Подписка компонента на смену языка: после setLang всё дерево перерисуется с новыми строками. */
export const useI18n = () => useContext(I18nContext)
