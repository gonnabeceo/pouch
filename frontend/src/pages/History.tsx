import { useEffect, useMemo, useState, type ReactNode } from "react"
import { ArrowDownRightIcon, ArrowUpRightIcon, MinusIcon } from "lucide-react"
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts"
import {
  api,
  networkLabel,
  type HistoryPoint,
  type PortfolioSet,
  type SimulatedHistory,
  type SnapshotHistory,
} from "@/lib/api"
import { locale, t, tServer, useI18n } from "@/lib/i18n"
import { readSelected, saveSelected } from "@/lib/selection"
import { MASK, useStreamer } from "@/lib/streamer"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

type Mode = "snapshots" | "simulated"
type Currency = "usd" | "rub"

const RANGES: [number, string][] = [
  [1, "24 ч"],
  [7, "7 д"],
  [30, "30 д"],
  [90, "90 д"],
  [0, "Всё"],
]
const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)"]
const OTHER = "Прочее"
const PREFS_KEY = "pouch.history"

type Prefs = { days: number; mode: Mode; currency: Currency }

function readPrefs(): Prefs {
  try {
    return { days: 30, mode: "snapshots", currency: "usd", ...JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}") }
  } catch {
    return { days: 30, mode: "snapshots", currency: "usd" }
  }
}

// --- форматирование ---

function money(value: number, currency: Currency, compact = false) {
  return new Intl.NumberFormat(currency === "usd" ? "en-US" : locale(), {
    style: "currency",
    currency: currency === "usd" ? "USD" : "RUB",
    notation: compact ? "compact" : "standard",
    maximumFractionDigits: compact ? 1 : currency === "usd" ? 2 : 0,
  }).format(value)
}

// Форматтеры дат создаются на лету: язык интерфейса можно сменить без перезагрузки.
const dateTime = { format: (d: Date) => new Intl.DateTimeFormat(locale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(d) }
const dayMonth = { format: (d: Date) => new Intl.DateTimeFormat(locale(), { day: "numeric", month: "short" }).format(d) }
const hourMinute = { format: (d: Date) => new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" }).format(d) }

const tickTime = (days: number) => (ts: number) => (days === 1 ? hourMinute : dayMonth).format(new Date(ts))

// --- страница ---

export default function History() {
  useI18n()
  const { on: streamer } = useStreamer()
  const [prefs, setPrefs] = useState<Prefs>(readPrefs)
  const [portfolios, setPortfolios] = useState<PortfolioSet[]>([])
  const [selected, setSelected] = useState<number | null>(readSelected)
  const [real, setReal] = useState<SnapshotHistory | null>(null)
  const [simulated, setSimulated] = useState<SimulatedHistory | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const update = (patch: Partial<Prefs>) =>
    setPrefs((p) => {
      const next = { ...p, ...patch }
      try {
        localStorage.setItem(PREFS_KEY, JSON.stringify(next))
      } catch {
        /* без запоминания */
      }
      return next
    })

  useEffect(() => {
    api.portfolios().then(setPortfolios).catch(() => {})
  }, [])

  // Реальная история нужна в обоих режимах: из неё кошельки и структура по активам.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    const requests: Promise<unknown>[] = [
      api.history(selected, prefs.days).then((h) => !cancelled && setReal(h)),
    ]
    if (prefs.mode === "simulated") {
      requests.push(
        api.simulatedHistory(selected, prefs.days).then((h) => {
          if (!cancelled) setSimulated(h)
          return h
        }),
      )
    }
    // Цены догружаются в фоне: пока есть очередь, перезапрашиваем раз в 8 секунд.
    let poll: ReturnType<typeof setTimeout> | undefined
    const pollSimulated = () => {
      poll = setTimeout(() => {
        api
          .simulatedHistory(selected, prefs.days)
          .then((h) => {
            if (cancelled) return
            setSimulated(h)
            if (h.pending.length) pollSimulated()
          })
          .catch(() => {})
      }, 8000)
    }
    if (prefs.mode === "simulated") {
      requests[1] = (requests[1] as Promise<{ pending: string[] }>).then((h) => {
        if (!cancelled && h.pending.length) pollSimulated()
      })
    }
    Promise.all(requests)
      .catch((e) => {
        if (cancelled) return
        // Портфолио удалили в настройках — возвращаемся ко всем кошелькам.
        if (e?.status === 404 && selected !== null) {
          saveSelected(null)
          setSelected(null)
        } else setError(e.message)
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
      clearTimeout(poll)
    }
  }, [selected, prefs.days, prefs.mode])

  const choose = (id: number | null) => {
    saveSelected(id)
    setSelected(id)
  }

  const source = prefs.mode === "simulated" ? simulated : real
  const rate = real?.rub_rate ?? null
  const currency: Currency = prefs.currency === "rub" && rate ? "rub" : "usd"
  const toCurrency = (usd: number, pointRate?: number | null) => (currency === "rub" ? usd * (pointRate ?? rate ?? 0) : usd)

  const series = useMemo(
    () =>
      (source?.points ?? []).map((p: HistoryPoint) => ({
        ts: new Date(p.t).getTime(),
        value: toCurrency(p.total_usd, prefs.mode === "snapshots" ? p.usd_rub : rate),
        live: !!p.live,
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [source, currency, rate, prefs.mode],
  )

  const enough = series.length >= 2
  const first = enough ? series[0].value : null
  const last = series.length ? series[series.length - 1].value : null
  const values = series.map((p) => p.value)

  return (
    <div className="space-y-8 sm:space-y-10">
      <div className="space-y-4">
        <h1 className="text-xl font-semibold tracking-tight">{t("История")}</h1>
        {/* Фильтры — одной строкой над всем, что они меняют. */}
        <div className="flex flex-wrap items-center gap-2">
          {portfolios.length > 0 && (
            <Segmented
              value={String(selected)}
              onChange={(v) => choose(v === "null" ? null : Number(v))}
              options={[["null", t("Все кошельки")], ...portfolios.map((p) => [String(p.id), p.name] as [string, string])]}
            />
          )}
          <Segmented
            value={String(prefs.days)}
            onChange={(v) => update({ days: Number(v) })}
            options={RANGES.map(([d, label]) => [String(d), t(label)] as [string, string])}
          />
          <Segmented
            value={prefs.mode}
            onChange={(v) => update({ mode: v as Mode })}
            options={[
              ["snapshots", t("Снапшоты")],
              ["simulated", t("Текущий состав")],
            ]}
          />
          <Segmented
            value={currency}
            onChange={(v) => update({ currency: v as Currency })}
            options={[
              ["usd", "USD"],
              ["rub", "RUB"],
            ]}
          />
        </div>
        <ModeNote mode={prefs.mode} real={real} simulated={simulated} loading={loading} />
        {error && <p className="text-sm text-destructive">{error}</p>}
      </div>

      <div className={cn("space-y-8 transition-opacity sm:space-y-10", loading && "opacity-60")}>
        <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label={t("Сейчас")} value={last !== null ? money(last, currency) : "—"} />
          <ChangeTile first={first} last={last} currency={currency} days={prefs.days} />
          <StatTile label={t("Максимум")} value={enough ? money(Math.max(...values), currency) : "—"} />
          <StatTile label={t("Минимум")} value={enough ? money(Math.min(...values), currency) : "—"} />
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-medium">
            {prefs.mode === "simulated" ? t("Текущий состав по историческим ценам") : t("Итог по снапшотам")}
          </h2>
          {enough ? (
            <TotalChart data={series} currency={currency} days={prefs.days} streamer={streamer} />
          ) : (
            <EmptyHistory
              real={real}
              loading={loading}
              onSimulate={prefs.mode === "snapshots" ? () => update({ mode: "simulated" }) : undefined}
            />
          )}
        </section>

        {real && real.assets.rows.length >= 2 && (
          <AssetsSection real={real} currency={currency} days={prefs.days} streamer={streamer} toCurrency={toCurrency} />
        )}

        {real && real.allocation.length > 0 && (
          <AllocationSection real={real} currency={currency} streamer={streamer} toCurrency={toCurrency} />
        )}

        {real && real.wallets.length > 0 && (
          <WalletsSection real={real} currency={currency} streamer={streamer} toCurrency={toCurrency} />
        )}
      </div>
    </div>
  )
}

// --- элементы управления ---

function Segmented({ value, options, onChange }: { value: string; options: [string, string][]; onChange: (v: string) => void }) {
  return (
    <div className="inline-flex max-w-full overflow-x-auto rounded-lg border p-0.5">
      {options.map(([id, label]) => (
        <button
          key={id}
          type="button"
          onClick={() => onChange(id)}
          aria-pressed={value === id}
          className={cn(
            "shrink-0 rounded-md px-2.5 py-1 text-sm transition-colors",
            value === id ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

function ModeNote({ mode, real, simulated, loading }: {
  mode: Mode
  real: SnapshotHistory | null
  simulated: SimulatedHistory | null
  loading: boolean
}) {
  if (mode === "simulated") {
    if (!simulated) return loading ? <p className="text-sm text-muted-foreground">{t("Загружаю историю цен из CoinGecko…")}</p> : null
    return (
      <div className="space-y-1 text-sm text-muted-foreground">
        <p>
          {t("Моделирование, а не реальная история: сегодняшние количества × цены прошлых дней. Так видно, как рынок двигал нынешний портфель.")}{" "}
          {t("Учтено {pct}% стоимости", { pct: simulated.coverage_pct.toFixed(0) })}
          {simulated.assets_stable.length > 0 && t(", стейблкоины по $1")}
          {simulated.assets_constant.length > 0 && t("; по текущей цене без истории: {list}", { list: shortList(simulated.assets_constant) })}.
        </p>
        {simulated.pending.length > 0 && (
          <p className="flex items-center gap-2">
            <span className="size-1.5 animate-pulse rounded-full bg-[var(--series-1)]" aria-hidden />
            {t("Догружаю историю цен из CoinGecko: {list}. Без ключа CoinGecko это может занять пару минут, график дорисуется сам.", { list: simulated.pending.join(", ") })}
          </p>
        )}
      </div>
    )
  }
  if (!real) return null
  return (
    <p className="text-sm text-muted-foreground">
      {t("Реальные снапшоты: раз в час, пока Pouch открыт и обновление прошло полностью. Считаются по текущему составу портфолио, так что скрытые кошельки не искажают изменение.")}
      {real.first_snapshot_at && " " + t("Первый снапшот — {date}.", { date: dateTime.format(new Date(real.first_snapshot_at)) })}
    </p>
  )
}

const shortList = (items: string[], limit = 5) =>
  items.length > limit
    ? t("{list} и ещё {n}", { list: items.slice(0, limit).join(", "), n: items.length - limit })
    : items.join(", ")

// --- плитки ---

function StatTile({ label, value, children }: { label: string; value: string; children?: ReactNode }) {
  const { on } = useStreamer()
  return (
    <div className="min-w-0 space-y-1 rounded-xl border px-3 py-3 sm:px-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-base font-semibold tracking-tight break-words sm:text-lg">{on && value !== "—" ? MASK : value}</p>
      {children}
    </div>
  )
}

function ChangeTile({ first, last, currency, days }: { first: number | null; last: number | null; currency: Currency; days: number }) {
  const { on } = useStreamer()
  const period = RANGES.find(([d]) => d === days)?.[1] ?? ""
  if (first === null || last === null) return <StatTile label={t("Изменение")} value="—" />
  const diff = last - first
  const pct = first ? (diff / Math.abs(first)) * 100 : 0
  const up = diff > 0
  const flat = Math.abs(pct) < 0.005
  const Icon = flat ? MinusIcon : up ? ArrowUpRightIcon : ArrowDownRightIcon
  return (
    <div className="min-w-0 space-y-1 rounded-xl border px-3 py-3 sm:px-4">
      <p className="text-xs text-muted-foreground">{t("Изменение")} · {days ? t(period) : t("всё время")}</p>
      <p
        className="flex items-center gap-1 text-base font-semibold tracking-tight sm:text-lg"
        style={{ color: flat ? undefined : up ? "var(--delta-up)" : "var(--delta-down)" }}
      >
        <Icon className="size-4" aria-hidden />
        <span>
          {up ? "+" : ""}
          {pct.toFixed(2)}%
        </span>
        <span className="sr-only">{flat ? t("без изменений") : up ? t("рост") : t("снижение")}</span>
      </p>
      <p className="text-xs text-muted-foreground">
        {on ? MASK : `${diff >= 0 ? "+" : "−"}${money(Math.abs(diff), currency)}`}
      </p>
    </div>
  )
}

function EmptyHistory({ real, loading, onSimulate }: { real: SnapshotHistory | null; loading: boolean; onSimulate?: () => void }) {
  if (loading && !real) return <div className="h-72 rounded-xl border border-dashed" />
  return (
    <div className="flex h-72 flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 text-center">
      <p className="max-w-md text-sm text-muted-foreground">
        {real?.snapshot_count
          ? t("За этот период пока одна точка. Снапшоты сохраняются раз в час, пока Pouch открыт.")
          : t("Снапшотов пока нет. Первый появится после полного успешного обновления, дальше — раз в час, пока Pouch открыт.")}
      </p>
      {onSimulate && (
        <Button variant="outline" size="sm" onClick={onSimulate}>
          {t("Показать текущий состав по историческим ценам")}
        </Button>
      )}
    </div>
  )
}

// --- графики ---

const axisTick = { fill: "var(--chart-ink-muted)", fontSize: 11 }

function TotalChart({ data, currency, days, streamer }: {
  data: { ts: number; value: number; live: boolean }[]
  currency: Currency
  days: number
  streamer: boolean
}) {
  const lastIndex = data.length - 1
  return (
    <div className="rounded-xl border px-2 pt-4 pb-2">
      <AreaChart width={undefined} height={typeof window !== "undefined" && window.innerWidth < 640 ? 220 : 280} data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }} responsive style={{ width: "100%" }}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis
          dataKey="ts"
          type="number"
          scale="time"
          domain={["dataMin", "dataMax"]}
          tickFormatter={tickTime(days || 365)}
          tick={axisTick}
          tickLine={false}
          axisLine={{ stroke: "var(--chart-axis)" }}
          minTickGap={48}
        />
        <YAxis
          domain={["auto", "auto"]}
          tickFormatter={(v: number) => (streamer ? "" : money(v, currency, true))}
          tick={axisTick}
          tickLine={false}
          axisLine={false}
          width={streamer ? 8 : 64}
        />
        <Tooltip
          cursor={{ stroke: "var(--chart-axis)", strokeWidth: 1 }}
          isAnimationActive={false}
          content={({ active, payload }) => {
            const point = active && payload?.[0]?.payload
            if (!point) return null
            return (
              <ChartTooltip title={point.live ? t("Сейчас") : dateTime.format(new Date(point.ts))}>
                <TooltipRow color="var(--series-1)" value={streamer ? MASK : money(point.value, currency)} label={t("Итог")} />
              </ChartTooltip>
            )
          }}
        />
        <Area
          type="monotone"
          dataKey="value"
          stroke="var(--series-1)"
          strokeWidth={2}
          fill="var(--series-1)"
          fillOpacity={0.1}
          baseValue="dataMin"
          isAnimationActive={false}
          activeDot={{ r: 4, fill: "var(--series-1)", stroke: "var(--background)", strokeWidth: 2 }}
          dot={(props: { cx?: number; cy?: number; index?: number }) =>
            props.index === lastIndex ? (
              <circle key="end" cx={props.cx} cy={props.cy} r={4} fill="var(--series-1)" stroke="var(--background)" strokeWidth={2} />
            ) : (
              <g key={props.index} />
            )
          }
        />
      </AreaChart>
    </div>
  )
}

function ChartTooltip({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-w-40 space-y-1.5 rounded-lg border bg-popover px-3 py-2 text-popover-foreground shadow-md">
      <p className="text-xs text-muted-foreground">{title}</p>
      {children}
    </div>
  )
}

function TooltipRow({ color, value, label }: { color: string; value: string; label: string }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="h-0.5 w-3 shrink-0 rounded-full" style={{ background: color }} aria-hidden />
      <span className="font-semibold tabular-nums">{value}</span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  )
}

function Legend({ items }: { items: { key: string; color: string }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {items.map((i) => (
        <li key={i.key} className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-[3px]" style={{ background: i.color }} aria-hidden />
          {tServer(i.key)}
        </li>
      ))}
    </ul>
  )
}

type SectionProps = {
  real: SnapshotHistory
  currency: Currency
  streamer: boolean
  toCurrency: (usd: number, rate?: number | null) => number
}

function colorFor(top: string[], key: string) {
  const i = top.indexOf(key)
  return i >= 0 ? SERIES[i] : "var(--series-other)"
}

function AssetsSection({ real, currency, days, streamer, toCurrency }: SectionProps & { days: number }) {
  const keys = [...real.assets.top, OTHER]
  const rate = new Map(real.points.map((p) => [p.t, p.usd_rub]))
  const rows = real.assets.rows.map((r) => ({
    ts: new Date(r.t).getTime(),
    ...Object.fromEntries(keys.map((k) => [k, toCurrency(r.values[k] ?? 0, rate.get(r.t))])),
  }))
  const legend = keys.map((key) => ({ key, color: colorFor(real.assets.top, key) }))
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium">{t("Структура по активам")}</h2>
        <Legend items={legend} />
      </div>
      <div className="rounded-xl border px-2 pt-4 pb-2">
        <BarChart height={240} data={rows} margin={{ top: 8, right: 24, bottom: 0, left: 8 }} responsive style={{ width: "100%" }}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis
            dataKey="ts"
            tickFormatter={tickTime(days || 365)}
            tick={axisTick}
            tickLine={false}
            axisLine={{ stroke: "var(--chart-axis)" }}
            minTickGap={24}
          />
          <YAxis
            tickFormatter={(v: number) => (streamer ? "" : money(v, currency, true))}
            tick={axisTick}
            tickLine={false}
            axisLine={false}
            width={streamer ? 8 : 64}
          />
          <Tooltip
            cursor={{ fill: "var(--chart-grid)", opacity: 0.5 }}
            isAnimationActive={false}
            content={({ active, payload }) => {
              const row = active && payload?.[0]?.payload
              if (!row) return null
              return (
                <ChartTooltip title={dateTime.format(new Date(row.ts))}>
                  {[...keys].reverse().map((k) => (
                    <TooltipRow key={k} color={colorFor(real.assets.top, k)} label={tServer(k)}
                      value={streamer ? MASK : money(row[k], currency)} />
                  ))}
                </ChartTooltip>
              )
            }}
          />
          {keys.map((k, i) => (
            <Bar
              key={k}
              dataKey={k}
              stackId="assets"
              fill={colorFor(real.assets.top, k)}
              stroke="var(--background)"
              strokeWidth={2}
              maxBarSize={24}
              radius={i === keys.length - 1 ? [4, 4, 0, 0] : 0}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </div>
    </section>
  )
}

function AllocationSection({ real, currency, streamer, toCurrency }: SectionProps) {
  const total = real.allocation.reduce((s, a) => s + a.value_usd, 0)
  const top = real.assets.top
  const rows = [
    ...real.allocation.filter((a) => top.includes(a.symbol)),
    ...(real.allocation.some((a) => !top.includes(a.symbol))
      ? [{ symbol: OTHER, value_usd: real.allocation.filter((a) => !top.includes(a.symbol)).reduce((s, a) => s + a.value_usd, 0) }]
      : []),
  ]
  // Изменение по активу — между первой и последней точкой структуры.
  const firstRow = real.assets.rows[0]?.values
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-medium">{t("Доли сейчас")}</h2>
      <div className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full" role="img" aria-label={t("Доли активов")}>
        {rows.map((r) => (
          <div key={r.symbol} title={`${tServer(r.symbol)} ${((r.value_usd / total) * 100).toFixed(1)}%`}
            style={{ width: `${(r.value_usd / total) * 100}%`, background: colorFor(top, r.symbol) }} />
        ))}
      </div>
      {/* Таблица — обязательная пара к графикам: светлые цвета палитры ниже 3:1 по контрасту. */}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="py-1.5 font-normal">{t("Актив")}</th>
            <th className="text-right font-normal">{t("Стоимость")}</th>
            <th className="text-right font-normal">{t("Доля")}</th>
            <th className="text-right font-normal">{t("За период")}</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((r) => {
            const before = firstRow?.[r.symbol]
            const change = before ? ((r.value_usd - before) / before) * 100 : null
            return (
              <tr key={r.symbol} className="h-9 tabular-nums">
                <td className="flex h-9 items-center gap-2 font-medium">
                  <span className="size-2.5 rounded-[3px]" style={{ background: colorFor(top, r.symbol) }} aria-hidden />
                  {tServer(r.symbol)}
                </td>
                <td className="text-right">{streamer ? MASK : money(toCurrency(r.value_usd), currency)}</td>
                <td className="text-right text-muted-foreground">{((r.value_usd / total) * 100).toFixed(1)}%</td>
                <td className="text-right"><Delta pct={change} /></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

function WalletsSection({ real, currency, streamer, toCurrency }: SectionProps) {
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-medium">{t("Кошельки")}</h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="py-1.5 font-normal">{t("Кошелёк")}</th>
            <th className="hidden font-normal sm:table-cell">{t("Динамика")}</th>
            <th className="text-right font-normal">{t("Сейчас")}</th>
            <th className="text-right font-normal">{t("За период")}</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {real.wallets.map((w) => {
            const s = w.series
            const now = s.at(-1)?.value_usd ?? 0
            const before = s[0]?.value_usd
            const change = s.length >= 2 && before ? ((now - before) / Math.abs(before)) * 100 : null
            return (
              <tr key={w.id} className="h-11 tabular-nums">
                <td className="max-w-0 pr-2">
                  <p className="truncate font-medium">{w.name}</p>
                  <p className="text-xs text-muted-foreground">{networkLabel(w.network)}</p>
                </td>
                <td className="hidden sm:table-cell"><Sparkline values={s.map((p) => p.value_usd)} /></td>
                <td className="text-right">{streamer ? MASK : money(toCurrency(now), currency)}</td>
                <td className="text-right"><Delta pct={change} /></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <span className="text-xs text-muted-foreground">—</span>
  const data = values.map((v, i) => ({ i, v }))
  return (
    <LineChart width={96} height={28} data={data} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
      <YAxis hide domain={["dataMin", "dataMax"]} />
      <Line
        type="monotone"
        dataKey="v"
        stroke="var(--series-muted)"
        strokeWidth={1.5}
        isAnimationActive={false}
        dot={(p: { cx?: number; cy?: number; index?: number }) =>
          p.index === values.length - 1 ? (
            <circle key="end" cx={p.cx} cy={p.cy} r={2.5} fill="var(--series-1)" />
          ) : (
            <g key={p.index} />
          )
        }
      />
    </LineChart>
  )
}

function Delta({ pct }: { pct: number | null }) {
  if (pct === null || !Number.isFinite(pct)) return <span className="text-xs text-muted-foreground">—</span>
  if (Math.abs(pct) < 0.005) return <span className="text-muted-foreground">0%</span>
  const up = pct > 0
  return (
    <span className="inline-flex items-center gap-0.5" style={{ color: up ? "var(--delta-up)" : "var(--delta-down)" }}>
      {up ? <ArrowUpRightIcon className="size-3.5" aria-hidden /> : <ArrowDownRightIcon className="size-3.5" aria-hidden />}
      {up ? "+" : ""}
      {pct.toFixed(1)}%
    </span>
  )
}
