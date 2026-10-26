import { locale } from "./i18n"

// Форматтеры создаются на каждый вызов: язык интерфейса можно сменить на лету.
const usd = () => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 })
const usdSmall = () => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumSignificantDigits: 4 })
const rub = () => new Intl.NumberFormat(locale(), { style: "currency", currency: "RUB", maximumFractionDigits: 0 })

export const formatUsd = (v: number) => usd().format(v)
export const formatPrice = (v: number) => (v >= 1 ? usd().format(v) : usdSmall().format(v))
export const formatRub = (v: number) => rub().format(v)

// Количество приходит строкой без потери точности; показываем до 8 знаков после запятой.
export function formatAmount(amount: string): string {
  const [int, frac = ""] = amount.split(".")
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, " ")
  const trimmed = frac.slice(0, 8).replace(/0+$/, "")
  if (!trimmed && frac && /[1-9]/.test(frac)) return `<0.00000001`
  return trimmed ? `${grouped}.${trimmed}` : grouped
}

export function formatWhen(iso: string): string {
  const d = new Date(iso)
  const sameDay = d.toDateString() === new Date().toDateString()
  return new Intl.DateTimeFormat(
    locale(),
    sameDay ? { hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" },
  ).format(d)
}

export const formatDate = (iso: string, options: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat(locale(), options).format(new Date(iso))
