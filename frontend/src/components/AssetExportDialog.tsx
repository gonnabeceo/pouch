import { useEffect, useState, type FormEvent } from "react"
import { toast } from "sonner"
import { api, type AssetExportOptions } from "@/lib/api"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  portfolio: { id: number | null; name: string }
}

function Choice<T extends string>({ value, options, onChange }: {
  value: T
  options: [T, string][]
  onChange: (v: T) => void
}) {
  return (
    <div className="inline-flex max-w-full overflow-x-auto rounded-lg border p-0.5">
      {options.map(([id, label]) => (
        <button
          key={id}
          type="button"
          onClick={() => onChange(id)}
          aria-pressed={value === id}
          className={cn(
            "rounded-md px-3 py-1 text-sm transition-colors",
            value === id ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

export default function AssetExportDialog({ open, onOpenChange, portfolio }: Props) {
  const [scope, setScope] = useState<AssetExportOptions["scope"]>("current")
  const [format, setFormat] = useState<AssetExportOptions["format"]>("csv")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) {
      setPassword("")
      setError(null)
    }
  }, [open])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const name = await api.exportAssets(password, { scope, format, portfolio_id: portfolio.id })
      toast(t("Сохранён файл {name}", { name }))
      onOpenChange(false)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="space-y-5">
          <DialogHeader>
            <DialogTitle>{t("Экспорт активов")}</DialogTitle>
            <DialogDescription>
              {t("Портфолио «{name}». В файле только активы: количество, цена, стоимость в USD и RUB, доля. Без адресов, названий кошельков и сетей.", { name: portfolio.name })}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm text-muted-foreground">{t("Что")}</span>
              <Choice value={scope} onChange={setScope} options={[["current", t("Сейчас")], ["history", t("История по снапшотам")]]} />
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm text-muted-foreground">{t("Формат")}</span>
              <Choice value={format} onChange={setFormat} options={[["csv", "CSV"], ["json", "JSON"]]} />
            </div>
            <p className="text-xs text-muted-foreground">
              {scope === "current"
                ? t("Строка на актив и итоговая строка. CSV открывается в Numbers и Excel.")
                : t("Строка на актив в каждом снапшоте — удобно для сводной таблицы и графиков. Состав портфолио — текущий.")}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="export-password">{t("Мастер-пароль")}</Label>
            <Input
              id="export-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
            />
            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              {t("Отмена")}
            </Button>
            <Button type="submit" disabled={busy || !password}>
              {t("Скачать")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
