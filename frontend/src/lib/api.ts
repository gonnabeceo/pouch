import { getLang, t, tServer } from "./i18n"

// Подсказки (hint) — исходные русские строки; переводятся через t() там, где показываются.
export type Network = "btc" | "ltc" | "doge" | "evm" | "sol" | "ton" | "tron" | "hyperliquid"

export const NETWORKS: { id: Network; label: string; hint?: string }[] = [
  { id: "btc", label: "Bitcoin", hint: "1…, 3… или bc1…" },
  { id: "ltc", label: "Litecoin", hint: "L…, M… или ltc1…" },
  { id: "doge", label: "Dogecoin", hint: "D…, 9… или A…" },
  { id: "evm", label: "EVM", hint: "0x… — один адрес проверяется во всех EVM-сетях" },
  { id: "sol", label: "Solana" },
  { id: "ton", label: "TON", hint: "EQ…, UQ… или 0:<hex>" },
  { id: "tron", label: "Tron", hint: "T…" },
  {
    id: "hyperliquid",
    label: "Hyperliquid",
    hint: "EVM-адрес мастер-аккаунта, суб-аккаунты подтянутся сами. Балансы в HyperEVM — добавьте этот же адрес как EVM.",
  },
]

export const networkLabel = (id: Network) => NETWORKS.find((n) => n.id === id)?.label ?? id

export type Wallet = {
  id: number
  name: string
  network: Network
  address: string
  note: string
  hidden: boolean
  created_at: string
}

export type WalletInput = Pick<Wallet, "name" | "network" | "address" | "note">

export type Status = {
  initialized: boolean
  unlocked: boolean
  autolock_seconds: number
  min_password_length: number
}

export type HoldingKind = "free" | "staked" | "deposit" | "loan" | "reward" | "locked" | "investment"

export type Holding = {
  symbol: string
  name: string
  amount: string
  kind: HoldingKind
  protocol: string | null
  chain: string | null
  price_usd: number | null
  value_usd: number | null
}

export type HlPosition = {
  dex: string | null
  coin: string
  side: "long" | "short"
  size: string
  entry_px: string | null
  position_value: string | null
  unrealized_pnl: string | null
  leverage: number | null
  leverage_type: string | null
  liquidation_px: string | null
  margin_used: string | null
}

export type HlAccount = {
  label: string
  address: string
  mode: string
  perp_value: string | null
  ui_total: string | null
  positions: HlPosition[]
  vaults: { name: string; address: string; equity: string; locked_until: number | null }[]
}

export type WalletStatus = "fresh" | "stale" | "error" | "pending"

export type WalletView = {
  id: number
  name: string
  network: Network
  address: string
  status: WalletStatus
  fetched_at: string | null
  error: string | null
  warning: string | null
  details: { accounts: HlAccount[] } | null
  holdings: Holding[]
  hidden_tokens: number
  dust_count: number
  dust_usd: number
  value_usd: number | null
}

export type Asset = { symbol: string; name: string; amount: string; value_usd: number }

export type PortfolioSet = { id: number; name: string; wallet_ids: number[] }

export type PortfolioView = {
  portfolio: { id: number | null; name: string }
  total_usd: number
  total_rub: number | null
  rub_rate: number | null
  updated_at: string | null
  prices_at: string | null
  prices_error: string | null
  incomplete: boolean
  dust_threshold_usd: number
  wallets: WalletView[]
  assets: Asset[]
}

export const PROVIDER_KEYS = [
  { id: "zerion", label: "Zerion", hint: "EVM: токены во всех сетях, стейкинг и DeFi. Без ключа EVM-кошельки не обновляются." },
  { id: "coingecko", label: "CoinGecko", hint: "Demo-ключ: цены. Без ключа работает с общим лимитом по IP." },
  { id: "helius", label: "Helius", hint: "Solana RPC с лимитами мягче публичного." },
  { id: "tonapi", label: "TonAPI", hint: "TON: баланс и жетоны." },
  { id: "trongrid", label: "TronGrid", hint: "Tron: без ключа запросы могут ограничиваться." },
] as const

export type GeneralSettings = { dust_threshold_usd: number; refresh_interval_minutes: number }

export type AssetExportOptions = {
  format: "csv" | "json"
  scope: "current" | "history"
  portfolio_id: number | null
}

export type HistoryPoint = { t: string; total_usd: number; usd_rub?: number | null; live?: boolean }

export type SnapshotHistory = {
  portfolio: { id: number | null; name: string }
  first_snapshot_at: string | null
  snapshot_count: number
  points: HistoryPoint[]
  assets: { top: string[]; rows: { t: string; values: Record<string, number> }[] }
  wallets: { id: number; name: string; network: Network; series: { t: string; value_usd: number }[] }[]
  allocation: { symbol: string; value_usd: number }[]
  total_usd: number
  rub_rate: number | null
}

export type SimulatedHistory = {
  portfolio: { id: number | null; name: string }
  days: number
  points: HistoryPoint[]
  coverage_pct: number
  assets_with_history: string[]
  assets_stable: string[]
  assets_constant: string[]
  pending: string[]
  rub_rate: number | null
}

export type SnapshotSummary = { count: number; first: string | null; last: string | null }

export type ProviderKey = (typeof PROVIDER_KEYS)[number]["id"]

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// Вызывается при любом 401: сервер заблокирован или сессия истекла.
let onLocked: () => void = () => {}
export const setLockedHandler = (fn: () => void) => {
  onLocked = fn
}

async function send(method: string, path: string, body?: unknown, headers?: Record<string, string>) {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => null)
    const detail = data?.detail
    if (res.status === 401 && detail === "locked") onLocked()
    const message =
      typeof detail === "string" ? tServer(detail) : Array.isArray(detail) ? t("Проверьте заполнение полей") : res.statusText
    throw new ApiError(res.status, message)
  }
  return res
}

async function request<T>(method: string, path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
  const res = await send(method, path, body, headers)
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => null)
  return data as T
}

// Файл из ответа сервера: имя берём из Content-Disposition и сохраняем через временную ссылку.
async function download(path: string, body: unknown): Promise<string> {
  const res = await send("POST", path, body)
  const disposition = res.headers.get("Content-Disposition") ?? ""
  const utf8 = /filename\*=UTF-8''([^;]+)/.exec(disposition)?.[1]
  const name = utf8 ? decodeURIComponent(utf8) : (/filename="([^"]+)"/.exec(disposition)?.[1] ?? "pouch-export")
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement("a")
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return name
}

// Период графиков в часах: 0 — вся история, меньше суток уходит как hours, остальное — как days.
const periodQuery = (hours: number) => (hours && hours < 24 ? `hours=${hours}` : `days=${hours / 24}`)

export const api = {
  status: () => request<Status>("GET", "/status"),
  setup: (password: string) => request<void>("POST", "/setup", { password }),
  unlock: (password: string) => request<void>("POST", "/unlock", { password }),
  lock: () => request<void>("POST", "/lock"),
  activity: () => request<void>("POST", "/activity"),
  changePassword: (old: string, next: string) => request<void>("POST", "/password", { old, new: next }),
  wallets: (includeHidden = false) =>
    request<Wallet[]>("GET", `/wallets${includeHidden ? "?include_hidden=true" : ""}`),
  createWallet: (w: WalletInput) => request<Wallet>("POST", "/wallets", w),
  updateWallet: (id: number, patch: Partial<WalletInput & { hidden: boolean }>) =>
    request<Wallet>("PATCH", `/wallets/${id}`, patch),
  deleteWallet: (id: number) => request<void>("DELETE", `/wallets/${id}`),
  portfolio: (id: number | null) => request<PortfolioView>("GET", `/portfolio${id ? `?portfolio_id=${id}` : ""}`),
  // onlyDue: опросить только кошельки старше интервала обновления (открытие страницы, автообновление).
  // background: автообновление не продлевает сессию, иначе автоблокировка не сработает.
  refresh: (id: number | null, { onlyDue = false, background = false } = {}) =>
    request<PortfolioView>("POST", `/refresh?only_due=${onlyDue}${id ? `&portfolio_id=${id}` : ""}`, undefined,
      background ? { "X-Pouch-Background": "1" } : undefined),
  history: (id: number | null, hours: number) =>
    request<SnapshotHistory>("GET", `/history?${periodQuery(hours)}${id ? `&portfolio_id=${id}` : ""}`),
  simulatedHistory: (id: number | null, hours: number) =>
    request<SimulatedHistory>("GET", `/history/simulated?${periodQuery(hours)}${id ? `&portfolio_id=${id}` : ""}`),
  snapshotSummary: () => request<SnapshotSummary>("GET", "/snapshots/summary"),
  exportSnapshots: (password: string, format: "csv" | "json") =>
    download("/export/snapshots", { password, format }),
  exportBackup: (password: string) => download("/export/backup", { password }),
  exportAssets: (password: string, options: AssetExportOptions) =>
    download("/export/assets", { password, lang: getLang(), ...options }),
  portfolios: () => request<PortfolioSet[]>("GET", "/portfolios"),
  createPortfolio: (name: string, wallet_ids: number[]) =>
    request<PortfolioSet>("POST", "/portfolios", { name, wallet_ids }),
  updatePortfolio: (id: number, patch: { name?: string; wallet_ids?: number[] }) =>
    request<PortfolioSet>("PATCH", `/portfolios/${id}`, patch),
  deletePortfolio: (id: number) => request<void>("DELETE", `/portfolios/${id}`),
  keys: () => request<Record<ProviderKey, boolean>>("GET", "/settings/keys"),
  setKey: (name: ProviderKey, value: string | null) => request<void>("PUT", `/settings/keys/${name}`, { value }),
  general: () => request<GeneralSettings>("GET", "/settings/general"),
  setGeneral: (s: Partial<GeneralSettings>) => request<GeneralSettings>("PUT", "/settings/general", s),
}

export const shortAddress = (a: string) => (a.length > 16 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a)
