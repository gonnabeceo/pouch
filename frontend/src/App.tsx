import { lazy, Suspense, useCallback, useEffect, useState } from "react"
import { EyeIcon, EyeOffIcon, LockIcon, MoonIcon, SunIcon } from "lucide-react"
import { HOTKEY_LABEL, useStreamer } from "@/lib/streamer"
import { setTheme, useTheme } from "@/lib/theme"
import { t, useI18n } from "@/lib/i18n"
import icon from "@/assets/icon.png"
import { api, setLockedHandler, type Status } from "@/lib/api"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Toaster } from "@/components/ui/sonner"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import Login from "@/pages/Login"
import Home from "@/pages/Home"
import Wallets from "@/pages/Wallets"
import Settings from "@/pages/Settings"

// Recharts тяжёлый — грузим «Историю» отдельным файлом только при открытии вкладки.
const History = lazy(() => import("@/pages/History"))

const PAGES = [
  { id: "home", label: "Главная", Component: Home },
  { id: "history", label: "История", Component: History },
  { id: "wallets", label: "Кошельки", Component: Wallets },
  { id: "settings", label: "Настройки", Component: Settings },
] as const

type PageId = (typeof PAGES)[number]["id"]

const pageFromHash = (): PageId => {
  const id = window.location.hash.slice(1)
  return PAGES.some((p) => p.id === id) ? (id as PageId) : "home"
}

const ACTIVITY_PING_MS = 60_000
const STATUS_POLL_MS = 30_000

export default function App() {
  useI18n() // перерисовка при смене языка
  const [status, setStatus] = useState<Status | null>(null)
  const [page, setPage] = useState<PageId>(pageFromHash)

  const refreshStatus = useCallback(() => {
    api.status().then(setStatus).catch(() => setStatus(null))
  }, [])

  useEffect(() => {
    refreshStatus()
    setLockedHandler(() => setStatus((s) => (s ? { ...s, unlocked: false } : s)))
    const onHash = () => setPage(pageFromHash())
    window.addEventListener("hashchange", onHash)
    return () => window.removeEventListener("hashchange", onHash)
  }, [refreshStatus])

  const unlocked = status?.unlocked ?? false

  // Сервер блокируется сам после бездействия; фронт узнаёт об этом опросом статуса,
  // а действия пользователя продлевают сессию.
  useEffect(() => {
    if (!unlocked) return
    let last = Date.now()
    const onActivity = () => {
      if (Date.now() - last < ACTIVITY_PING_MS) return
      last = Date.now()
      api.activity().catch(() => {})
    }
    const events = ["pointerdown", "keydown", "wheel"] as const
    events.forEach((e) => window.addEventListener(e, onActivity, { passive: true }))
    const poll = setInterval(refreshStatus, STATUS_POLL_MS)
    return () => {
      events.forEach((e) => window.removeEventListener(e, onActivity))
      clearInterval(poll)
    }
  }, [unlocked, refreshStatus])

  const lock = async () => {
    await api.lock().catch(() => {})
    refreshStatus()
  }

  if (!status) {
    return <div className="min-h-svh" />
  }

  if (!unlocked) {
    return (
      <>
        <Login status={status} onUnlocked={refreshStatus} />
        <Toaster position="bottom-center" />
      </>
    )
  }

  const { Component } = PAGES.find((p) => p.id === page)!

  return (
    <TooltipProvider>
      <div className="flex min-h-svh flex-col">
        <header className="sticky top-0 z-10 border-b bg-background/80 backdrop-blur">
          {/* На узком экране вкладки уходят второй строкой и прокручиваются по горизонтали. */}
          <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-x-6 px-4 sm:h-14 sm:flex-nowrap sm:px-6">
            <span className="flex h-12 items-center gap-2 text-sm font-semibold tracking-tight sm:h-auto">
              <img src={icon} alt="" className="size-5 rounded-[22%]" />
              Pouch
            </span>
            <nav className="order-last -mx-1 flex w-full gap-1 overflow-x-auto pb-2 sm:order-none sm:mx-0 sm:w-auto sm:pb-0">
              {PAGES.map((p) => (
                <a
                  key={p.id}
                  href={`#${p.id}`}
                  className={cn(
                    "shrink-0 rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground",
                    page === p.id && "bg-muted text-foreground",
                  )}
                >
                  {t(p.label)}
                </a>
              ))}
            </nav>
            <div className="ml-auto flex items-center gap-0.5">
              <LanguageButton />
              <StreamerButton />
              <ThemeButton />
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" onClick={lock} aria-label={t("Заблокировать")}>
                    <LockIcon />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{t("Заблокировать")}</TooltipContent>
              </Tooltip>
            </div>
          </div>
        </header>
        <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-6 sm:px-6 sm:py-10">
          <Suspense fallback={null}>
            <Component />
          </Suspense>
        </main>
        <footer className="mx-auto w-full max-w-4xl px-4 pb-6 text-xs text-muted-foreground sm:px-6">
          {/* Бесплатный API CoinGecko требует указывать источник цен. */}
          {t("Цены")}:{" "}
          <a href="https://www.coingecko.com" target="_blank" rel="noreferrer" className="underline-offset-2 hover:underline">
            CoinGecko
          </a>
        </footer>
      </div>
      <Toaster position="bottom-center" />
    </TooltipProvider>
  )
}

function LanguageButton() {
  const { lang, setLang } = useI18n()
  const next = lang === "ru" ? "en" : "ru"
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="w-9 text-xs font-semibold"
          onClick={() => setLang(next)}
          aria-label={next === "en" ? "Switch to English" : "Переключить на русский"}
        >
          {lang.toUpperCase()}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{next === "en" ? "English" : "Русский"}</TooltipContent>
    </Tooltip>
  )
}

function StreamerButton() {
  const { on, toggle } = useStreamer()
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant={on ? "secondary" : "ghost"}
          size="icon"
          onClick={toggle}
          aria-pressed={on}
          aria-label={t("Режим стримера")}
        >
          {on ? <EyeOffIcon /> : <EyeIcon />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {on ? t("Показать суммы") : t("Режим стримера")} · {HOTKEY_LABEL}
      </TooltipContent>
    </Tooltip>
  )
}

function ThemeButton() {
  const { dark } = useTheme()
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={dark ? t("Светлая тема") : t("Тёмная тема")}
          onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect()
            setTheme(dark ? "light" : "dark", { x: r.left + r.width / 2, y: r.top + r.height / 2 })
          }}
        >
          {dark ? <SunIcon /> : <MoonIcon />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{dark ? t("Светлая тема") : t("Тёмная тема")}</TooltipContent>
    </Tooltip>
  )
}
