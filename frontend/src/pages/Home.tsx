import { useCallback, useEffect, useRef, useState } from "react"
import { ArrowDownIcon, ArrowUpIcon, ChevronRightIcon, DownloadIcon, RefreshCwIcon, TriangleAlertIcon } from "lucide-react"
import { toast } from "sonner"
import {
  api,
  ApiError,
  networkLabel,
  shortAddress,
  type HlPosition,
  type Holding,
  type HoldingKind,
  type PortfolioSet,
  type PortfolioView,
  type WalletView,
} from "@/lib/api"
import { formatAmount, formatDate, formatPrice, formatRub, formatUsd, formatWhen } from "@/lib/format"
import { t, tServer, useI18n } from "@/lib/i18n"
import { readSelected, saveSelected } from "@/lib/selection"
import { Hide, MASK, useStreamer } from "@/lib/streamer"
import { cn } from "@/lib/utils"
import AssetExportDialog from "@/components/AssetExportDialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

// Сортировка кошельков: по сумме или по дате добавления; повторный клик меняет направление.
type WalletSort = { by: "value" | "added"; desc: boolean }
const SORT_KEY = "pouch.walletSort"
const DEFAULT_SORT: WalletSort = { by: "value", desc: true }
const AUTO_CHECK_MS = 60_000

function readSort(): WalletSort {
  try {
    const saved = JSON.parse(localStorage.getItem(SORT_KEY) ?? "null")
    return saved?.by === "value" || saved?.by === "added" ? { by: saved.by, desc: !!saved.desc } : DEFAULT_SORT
  } catch {
    return DEFAULT_SORT
  }
}

function sortWallets(wallets: WalletView[], sort: WalletSort): WalletView[] {
  // Сервер отдаёт кошельки в порядке добавления — индекс и есть дата.
  const indexed = wallets.map((w, i) => ({ w, i }))
  indexed.sort((a, b) => {
    if (sort.by === "added") return sort.desc ? b.i - a.i : a.i - b.i
    // Кошельки без суммы (ошибка, ещё не обновлялись) — всегда в конце.
    const av = a.w.value_usd, bv = b.w.value_usd
    if (av === null || bv === null) return av === bv ? a.i - b.i : av === null ? 1 : -1
    return sort.desc ? bv - av || a.i - b.i : av - bv || a.i - b.i
  })
  return indexed.map(({ w }) => w)
}

function SortControl({ sort, onChange }: { sort: WalletSort; onChange: (s: WalletSort) => void }) {
  const options: [WalletSort["by"], string][] = [
    ["value", t("По сумме")],
    ["added", t("По дате")],
  ]
  return (
    <div className="inline-flex rounded-lg border p-0.5 text-xs" role="group" aria-label={t("Сортировка кошельков")}>
      {options.map(([by, label]) => {
        const active = sort.by === by
        const Arrow = sort.desc ? ArrowDownIcon : ArrowUpIcon
        return (
          <button
            key={by}
            type="button"
            aria-pressed={active}
            title={active ? (sort.desc ? t("Сначала большие / новые") : t("Сначала маленькие / старые")) : undefined}
            onClick={() => onChange(active ? { by, desc: !sort.desc } : { by, desc: by === "value" })}
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2 py-1 transition-colors",
              active ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {label}
            {active && <Arrow className="size-3" aria-hidden />}
          </button>
        )
      })}
    </div>
  )
}

// Встроенное портфолио приходит с сервера по-русски — показываем на языке интерфейса.
const portfolioName = (p: { id: number | null; name: string }) => (p.id === null ? t("Все кошельки") : p.name)

export default function Home() {
  useI18n()
  const [view, setView] = useState<PortfolioView | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [portfolios, setPortfolios] = useState<PortfolioSet[]>([])
  const [exportOpen, setExportOpen] = useState(false)
  const [sort, setSort] = useState<WalletSort>(readSort)
  const changeSort = (next: WalletSort) => {
    setSort(next)
    try {
      localStorage.setItem(SORT_KEY, JSON.stringify(next))
    } catch {
      /* без запоминания */
    }
  }
  // Выбор держим в ref: «Обновить» и таймер берут текущий выбор, не пересоздаваясь.
  const selectedRef = useRef<number | null>(readSelected())

  // Кэш и обновление запрашиваются параллельно и могут прийти в любом порядке.
  // Применяем ответ, только если он новее уже показанного и относится к текущему портфолио.
  const seqRef = useRef(0)
  const appliedRef = useRef(0)

  const select = (id: number | null) => {
    selectedRef.current = id
    saveSelected(id)
  }

  // open — открытие страницы, auto — автообновление: обновляются только кошельки, которым пора.
  const load = useCallback(async (mode: "cached" | "refresh" | "open" | "auto") => {
    const seq = ++seqRef.current
    const get = (pid: number | null) =>
      mode === "cached"
        ? api.portfolio(pid)
        : api.refresh(pid, { onlyDue: mode !== "refresh", background: mode === "auto" })
    let result: PortfolioView
    try {
      result = await get(selectedRef.current)
    } catch (e) {
      // Выбранное портфолио могли удалить в настройках — возвращаемся к «Все кошельки».
      if (!(e instanceof ApiError && e.status === 404 && selectedRef.current !== null)) throw e
      select(null)
      result = await get(null)
    }
    if (seq < appliedRef.current) return
    if (result.portfolio.id !== selectedRef.current) {
      // Пока шло обновление, выбрали другое портфолио: данные на сервере свежие, берём их для текущего.
      if (mode !== "cached") void load("cached")
      return
    }
    appliedRef.current = seq
    setView(result)
  }, [])

  const refreshingRef = useRef(false)
  const refresh = useCallback(
    async (mode: "refresh" | "open" | "auto" = "refresh") => {
      if (refreshingRef.current) return
      refreshingRef.current = true
      // Когда обновлять нечего, сервер отвечает сразу: спиннер показываем, только если запрос затянулся.
      const spinner = setTimeout(() => setRefreshing(true), 300)
      try {
        await load(mode)
      } catch (e) {
        // Ошибка фонового обновления видна по статусам кошельков, тост не нужен.
        if (mode !== "auto") toast.error((e as Error).message)
      } finally {
        clearTimeout(spinner)
        refreshingRef.current = false
        setRefreshing(false)
      }
    },
    [load],
  )

  // Автообновление: раз в минуту спрашиваем сервер, а он сам решает по интервалу из настроек,
  // каким кошелькам пора. Так интервал соблюдается при любом числе вкладок и заходов на страницу.
  useEffect(() => {
    const timer = setInterval(() => refresh("auto"), AUTO_CHECK_MS)
    return () => clearInterval(timer)
  }, [refresh])

  // Сразу показываем последнее известное из базы, затем обновляем то, что устарело.
  useEffect(() => {
    api.portfolios().then(setPortfolios).catch(() => {})
    load("cached").catch(() => {})
    refresh("open")
  }, [load, refresh])

  const choose = (id: number | null) => {
    select(id)
    load("cached").catch((e) => toast.error(e.message))
  }

  if (!view) return null

  const problems = view.wallets.filter((w) => w.status === "stale" || w.status === "error" || w.warning)
  const empty = view.wallets.length === 0

  return (
    <div className="space-y-10 sm:space-y-12">
      <section className="space-y-1">
        {portfolios.length > 0 && (
          <nav className="-mx-1 mb-4 flex gap-1 overflow-x-auto px-1 pb-1" aria-label={t("Портфолио")}>
            {[{ id: null, name: "" }, ...portfolios].map((p) => (
              <button
                key={p.id ?? "all"}
                type="button"
                onClick={() => choose(p.id)}
                aria-pressed={view.portfolio.id === p.id}
                className={cn(
                  "shrink-0 rounded-full border px-3 py-1 text-sm transition-colors",
                  view.portfolio.id === p.id
                    ? "border-foreground bg-foreground text-background"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {portfolioName(p)}
              </button>
            ))}
          </nav>
        )}
        <p className="text-sm text-muted-foreground">{portfolioName(view.portfolio)}</p>
        <p className="text-4xl font-semibold tracking-tight sm:text-5xl">
          <Hide>{formatUsd(view.total_usd)}</Hide>
        </p>
        <p className="text-lg text-muted-foreground">
          {view.total_rub !== null ? <Hide>{formatRub(view.total_rub)}</Hide> : "— ₽"}
        </p>
        <div className="flex flex-wrap items-center gap-2 pt-3 text-xs text-muted-foreground">
          <span>
            {view.updated_at ? t("Обновлено {time}", { time: formatWhen(view.updated_at) }) : t("Ещё не обновлялось")}
          </span>
          <Button variant="ghost" size="xs" onClick={() => refresh()} disabled={refreshing}>
            <RefreshCwIcon className={cn(refreshing && "animate-spin")} />
            {refreshing ? t("Обновляю…") : t("Обновить")}
          </Button>
        </div>
        {view.incomplete && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
            <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" />
            <div className="space-y-0.5">
              {problems.length > 0 && (
                <p>
                  {problems.length === 1
                    ? t("Итог неполный: 1 кошелёк обновился не полностью.")
                    : t("Итог неполный: кошельков обновилось не полностью: {n}.", { n: problems.length })}
                  {problems.some((w) => w.status === "stale" || w.warning) &&
                    " " + t("Где возможно, показано последнее известное значение.")}
                </p>
              )}
              {view.prices_error && <p>{t("Цены не обновились, использованы последние известные.")}</p>}
            </div>
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-medium">{t("Кошельки")}</h2>
          {view.wallets.length > 1 && <SortControl sort={sort} onChange={changeSort} />}
        </div>
        {empty ? (
          <div className="rounded-xl border border-dashed p-8 text-center">
            <p className="text-sm text-muted-foreground">
              {view.portfolio.id === null ? t("Пока нет кошельков") : t("В этом портфолио нет видимых кошельков")}
            </p>
            <Button asChild variant="outline" size="sm" className="mt-4">
              <a href={view.portfolio.id === null ? "#wallets" : "#settings"}>
                {view.portfolio.id === null ? t("Добавить кошелёк") : t("Изменить состав")}
              </a>
            </Button>
          </div>
        ) : (
          <ul className="divide-y rounded-xl border">
            {sortWallets(view.wallets, sort).map((w) => (
              <WalletRow key={w.id} wallet={w} />
            ))}
          </ul>
        )}
      </section>

      {view.wallets
        .filter((w) => w.network === "hyperliquid" && w.details)
        .map((w) => (
          <HyperliquidBlock key={w.id} wallet={w} />
        ))}

      {view.assets.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">{t("Активы")}</h2>
            <Button variant="ghost" size="xs" onClick={() => setExportOpen(true)}>
              <DownloadIcon />
              {t("Экспорт")}
            </Button>
          </div>
          <ul className="divide-y rounded-xl border">
            {view.assets.map((a) => (
              <li key={`${a.symbol}-${a.name}`} className="flex items-center gap-3 px-4 py-2.5 text-sm sm:gap-4">
                <span className="w-16 shrink-0 truncate font-medium sm:w-20">{tServer(a.symbol)}</span>
                <span className="min-w-0 flex-1 truncate text-muted-foreground tabular-nums">
                  <Hide>{formatAmount(a.amount)}</Hide>
                </span>
                {view.total_usd > 0 && (
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {((a.value_usd / view.total_usd) * 100).toFixed(1)}%
                  </span>
                )}
                <Value value={a.value_usd} className="w-24 sm:w-28" />
              </li>
            ))}
          </ul>
        </section>
      )}
      <AssetExportDialog
        open={exportOpen}
        onOpenChange={setExportOpen}
        portfolio={{ id: view.portfolio.id, name: portfolioName(view.portfolio) }}
      />
    </div>
  )
}

const KIND_LABEL: Record<HoldingKind, string> = {
  free: "",
  staked: "стейк",
  deposit: "депозит",
  loan: "долг",
  reward: "награда",
  locked: "заблокировано",
  investment: "инвестиция",
}

function WalletRow({ wallet: w }: { wallet: WalletView }) {
  const [open, setOpen] = useState(false)
  const { on: streamer } = useStreamer()
  const expandable = w.holdings.length > 0 || w.dust_count > 0
  const free = w.holdings.filter((h) => h.kind === "free" && !h.protocol)
  // Группы: протокол DeFi или аккаунт Hyperliquid; нативный стейкинг без протокола — отдельно.
  const groups = new Map<string, Holding[]>()
  for (const h of w.holdings.filter((h) => h.kind !== "free" || h.protocol)) {
    const key = h.protocol ?? "Стейкинг"
    groups.set(key, [...(groups.get(key) ?? []), h])
  }
  const notes = [
    w.dust_count > 0 && t("пыль: {n} на {sum}", { n: w.dust_count, sum: streamer ? MASK : formatUsd(w.dust_usd) }),
    w.hidden_tokens > 0 && t("спам и неизвестные токены: {n}", { n: w.hidden_tokens }),
  ].filter(Boolean) as string[]

  return (
    <li>
      <button
        type="button"
        disabled={!expandable}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-3 text-left enabled:hover:bg-muted/50 sm:gap-3 sm:px-4"
      >
        <ChevronRightIcon
          className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90", !expandable && "invisible")}
        />
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-sm font-medium">
            <StatusDot wallet={w} />
            <span className="truncate">{w.name}</span>
          </p>
          <p className="truncate font-mono text-xs text-muted-foreground">
            {networkLabel(w.network)} · {shortAddress(w.address)}
          </p>
          {/* На узком экране метка статуса уходит под название, чтобы не сжимать сумму. */}
          <div className="mt-1 sm:hidden">
            <StatusBadge wallet={w} />
          </div>
        </div>
        <div className="hidden sm:block">
          <StatusBadge wallet={w} />
        </div>
        <Value value={w.value_usd} className="sm:w-28" />
      </button>

      {open && (
        <div className="space-y-3 border-t bg-muted/30 px-3 py-2 sm:px-4">
          {free.length > 0 && <HoldingsTable holdings={free} />}
          {[...groups].map(([protocol, items]) => {
            const net = items.reduce((sum, h) => sum + (h.value_usd ?? 0), 0)
            return (
              <div key={protocol}>
                <div className="flex items-center justify-between border-b border-dashed pb-1 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">{tServer(protocol)}</span>
                  <span className="tabular-nums">
                    <Hide>{formatUsd(net)}</Hide>
                  </span>
                </div>
                <HoldingsTable holdings={items} />
              </div>
            )
          })}
          {notes.length > 0 && (
            <p className="pb-1 text-xs text-muted-foreground">
              {notes.join(" · ").replace(/^./, (c) => c.toUpperCase())}. {t("В итог не входят.")}
            </p>
          )}
        </div>
      )}
    </li>
  )
}

function HoldingsTable({ holdings }: { holdings: Holding[] }) {
  return (
    <table className="w-full text-sm">
      <tbody>
        {holdings.map((h, i) => (
          <tr key={i} className="h-9">
            <td className="w-24 font-medium sm:w-32">
              {tServer(h.symbol)}
              {KIND_LABEL[h.kind] && (
                <span className={cn("ml-1.5 text-xs font-normal text-muted-foreground", h.kind === "loan" && "text-destructive")}>
                  {t(KIND_LABEL[h.kind])}
                </span>
              )}
            </td>
            <td className="text-muted-foreground tabular-nums">
              {/* На телефоне длинное количество обрезается, чтобы не выталкивать сумму за край. */}
              <span className="inline-block max-w-[8.5rem] truncate align-bottom sm:max-w-none">
                <Hide>{formatAmount(h.amount)}</Hide>
              </span>
              {h.chain && <span className="ml-2 hidden text-xs sm:inline">{h.chain}</span>}
            </td>
            <td className="hidden text-right text-xs text-muted-foreground tabular-nums sm:table-cell">
              {h.price_usd !== null ? formatPrice(h.price_usd) : ""}
            </td>
            <td className={cn("w-24 text-right tabular-nums sm:w-28", h.kind === "loan" && "text-destructive")}>
              {h.value_usd !== null ? (
                <Hide>{formatUsd(h.value_usd)}</Hide>
              ) : (
                <span className="text-xs text-muted-foreground">{t("нет цены")}</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const MODE_LABEL: Record<string, string> = {
  unifiedAccount: "unified",
  portfolioMargin: "portfolio margin",
}

function HyperliquidBlock({ wallet: w }: { wallet: WalletView }) {
  const accounts = w.details!.accounts
  const accountValue = (label: string) =>
    w.holdings.filter((h) => h.protocol === label).reduce((sum, h) => sum + (h.value_usd ?? 0), 0)

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-medium">
        Hyperliquid <span className="font-normal text-muted-foreground">· {w.name}</span>
      </h2>
      <div className="divide-y rounded-xl border">
        {accounts.map((a) => (
          <div key={a.address} className="space-y-3 px-3 py-3 sm:px-4">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-sm font-medium">{tServer(a.label)}</span>
              <span className="font-mono text-xs text-muted-foreground">{shortAddress(a.address)}</span>
              <Badge variant="outline">{MODE_LABEL[a.mode] ?? t("стандартный")}</Badge>
              <span className="ml-auto text-sm tabular-nums">
                <Hide>{formatUsd(accountValue(a.label))}</Hide>
              </span>
            </div>
            {a.perp_value !== null && Math.abs(Number(a.perp_value)) >= 0.01 && (
              <p className="text-xs text-muted-foreground">
                {t("Account value перпов")}:{" "}
                <span className="tabular-nums">
                  <Hide>{formatUsd(Number(a.perp_value))}</Hide>
                </span>
              </p>
            )}
            {a.positions.length > 0 && <PositionsTable positions={a.positions} />}
            {a.vaults.length > 0 && (
              <ul className="space-y-1 text-xs text-muted-foreground">
                {a.vaults.map((v) => (
                  <li key={v.address} className="flex flex-wrap gap-x-2">
                    <span className="text-foreground">{v.name}</span>
                    <span className="tabular-nums">
                      <Hide>{formatUsd(Number(v.equity))}</Hide>
                    </span>
                    {v.locked_until && v.locked_until > Date.now() && (
                      <span>
                        {t("заблокировано до {date}", {
                          date: formatDate(new Date(v.locked_until).toISOString(), {
                            day: "numeric",
                            month: "short",
                            year: "numeric",
                          }),
                        })}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function PositionsTable({ positions }: { positions: HlPosition[] }) {
  const num = (v: string | null) => (v === null ? null : Number(v))
  return (
    <div className="-mx-3 overflow-x-auto px-3 sm:mx-0 sm:px-0">
      <table className="w-full min-w-[34rem] text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="py-1 font-normal">{t("Монета")}</th>
            <th className="font-normal">{t("Сторона")}</th>
            <th className="text-right font-normal">{t("Размер")}</th>
            <th className="text-right font-normal">{t("Вход")}</th>
            <th className="text-right font-normal">PnL</th>
            <th className="text-right font-normal">{t("Плечо")}</th>
            <th className="text-right font-normal">{t("Ликвидация")}</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const pnl = num(p.unrealized_pnl)
            const liq = num(p.liquidation_px)
            return (
              <tr key={`${p.dex}:${p.coin}`} className="h-8 tabular-nums">
                <td className="font-medium">{p.coin}</td>
                <td className={p.side === "long" ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}>
                  {p.side}
                </td>
                <td className="text-right">
                  <Hide>{formatAmount(p.size)}</Hide>
                </td>
                <td className="text-right text-muted-foreground">{p.entry_px ? formatPrice(Number(p.entry_px)) : "—"}</td>
                <td className={cn("text-right", pnl !== null && (pnl >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"))}>
                  {pnl !== null ? <Hide>{formatUsd(pnl)}</Hide> : "—"}
                </td>
                <td className="text-right text-muted-foreground">
                  {p.leverage ? `${p.leverage}×` : "—"}
                  {p.leverage_type === "isolated" && <span className="ml-1 text-xs">iso</span>}
                </td>
                <td className="text-right text-muted-foreground">{liq ? formatPrice(liq) : "—"}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const DOT: Partial<Record<WalletView["status"], [string, string]>> = {
  fresh: ["bg-emerald-500", "свежий"],
  stale: ["bg-amber-500", "устарел"],
  error: ["bg-red-500", "ошибка"],
}

function StatusDot({ wallet: w }: { wallet: WalletView }) {
  const dot = DOT[w.status]
  if (!dot) return null
  const [color, label] = dot
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn("inline-block size-1.5 shrink-0 rounded-full", color)} aria-label={t(label)} />
      </TooltipTrigger>
      <TooltipContent>
        {t(label)}
        {w.fetched_at && ` · ${t("данные от {time}", { time: formatWhen(w.fetched_at) })}`}
      </TooltipContent>
    </Tooltip>
  )
}

function StatusBadge({ wallet: w }: { wallet: WalletView }) {
  if (w.status === "fresh" && w.warning) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span>
            <Badge variant="outline">{t("частично")}</Badge>
          </span>
        </TooltipTrigger>
        <TooltipContent>{tServer(w.warning)}</TooltipContent>
      </Tooltip>
    )
  }
  if (w.status === "fresh") return null
  const label = { stale: "устарело", error: "ошибка", pending: "ожидает" }[w.status]
  const badge = (
    <Badge variant={w.status === "error" ? "destructive" : w.status === "stale" ? "outline" : "secondary"}>{t(label)}</Badge>
  )
  if (!w.error) return badge
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span>{badge}</span>
      </TooltipTrigger>
      <TooltipContent>
        {tServer(w.error)}
        {w.fetched_at && ` · ${t("данные от {time}", { time: formatWhen(w.fetched_at) })}`}
      </TooltipContent>
    </Tooltip>
  )
}

function Value({ value, className }: { value: number | null; className?: string }) {
  return (
    <span className={cn("shrink-0 text-right text-sm tabular-nums", value === null && "text-muted-foreground", className)}>
      {value !== null ? <Hide>{formatUsd(value)}</Hide> : "—"}
    </span>
  )
}
