import { useCallback, useEffect, useState, type FormEvent } from "react"
import { EllipsisIcon, PlusIcon } from "lucide-react"
import { toast } from "sonner"
import { api, networkLabel, type PortfolioSet, type Wallet } from "@/lib/api"
import { t } from "@/lib/i18n"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export default function PortfolioSettings() {
  const [portfolios, setPortfolios] = useState<PortfolioSet[]>([])
  const [wallets, setWallets] = useState<Wallet[]>([])
  const [editing, setEditing] = useState<PortfolioSet | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleting, setDeleting] = useState<PortfolioSet | null>(null)

  const load = useCallback(() => {
    Promise.all([api.portfolios(), api.wallets(true)])
      .then(([p, w]) => {
        setPortfolios(p)
        setWallets(w)
      })
      .catch((e) => toast.error(e.message))
  }, [])

  useEffect(load, [load])

  const visibleCount = wallets.filter((w) => !w.hidden).length

  const open = (p: PortfolioSet | null) => {
    setEditing(p)
    setDialogOpen(true)
  }

  const confirmDelete = async () => {
    if (!deleting) return
    try {
      await api.deletePortfolio(deleting.id)
      toast(t("Портфолио «{name}» удалено", { name: deleting.name }))
      load()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setDeleting(null)
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex-1">
          <h2 className="text-sm font-medium">{t("Портфолио")}</h2>
          <p className="text-sm text-muted-foreground">
            {t("Наборы кошельков со своим итогом. Один кошелёк может входить в несколько портфолио.")}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => open(null)}>
          <PlusIcon />
          {t("Создать")}
        </Button>
      </div>
      <ul className="divide-y rounded-xl border">
        <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-sm">
          <span className="font-medium">{t("Все кошельки")}</span>
          <Badge variant="secondary">{t("встроенное")}</Badge>
          <span className="ml-auto text-xs text-muted-foreground">{t("все нескрытые: {n}", { n: visibleCount })}</span>
        </li>
        {portfolios.map((p) => {
          const members = wallets.filter((w) => p.wallet_ids.includes(w.id))
          const hidden = members.filter((w) => w.hidden).length
          return (
            <li key={p.id} className="flex items-center gap-3 px-4 py-3 text-sm">
              <div className="min-w-0 flex-1">
                <p className="font-medium">{p.name}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {members.length ? members.map((w) => w.name).join(", ") : t("пусто")}
                  {hidden > 0 && ` · ${t("скрытых: {n}", { n: hidden })}`}
                </p>
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon-sm" aria-label={t("Действия")}>
                    <EllipsisIcon />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onSelect={() => open(p)}>{t("Изменить")}</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" onSelect={() => setDeleting(p)}>
                    {t("Удалить")}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          )
        })}
      </ul>

      <PortfolioDialog
        open={dialogOpen}
        portfolio={editing}
        wallets={wallets}
        onOpenChange={setDialogOpen}
        onSaved={load}
      />

      <AlertDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("Удалить портфолио «{name}»?", { name: deleting?.name ?? "" })}</AlertDialogTitle>
            <AlertDialogDescription>{t("Кошельки останутся, удалится только набор.")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("Отмена")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmDelete}>
              {t("Удалить")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}

type DialogProps = {
  open: boolean
  portfolio: PortfolioSet | null
  wallets: Wallet[]
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}

function PortfolioDialog({ open, portfolio, wallets, onOpenChange, onSaved }: DialogProps) {
  const [name, setName] = useState("")
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setName(portfolio?.name ?? "")
    setSelected(new Set(portfolio?.wallet_ids ?? []))
    setError(null)
  }, [open, portfolio])

  const toggle = (id: number, checked: boolean) =>
    setSelected((s) => {
      const next = new Set(s)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const ids = [...selected]
      if (portfolio) await api.updatePortfolio(portfolio.id, { name, wallet_ids: ids })
      else await api.createPortfolio(name, ids)
      onSaved()
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
            <DialogTitle>{portfolio ? t("Изменить портфолио") : t("Новое портфолио")}</DialogTitle>
            <DialogDescription>{t("Отметьте кошельки, которые входят в набор.")}</DialogDescription>
          </DialogHeader>

          <div className="space-y-1.5">
            <Label htmlFor="p-name">{t("Название")}</Label>
            <Input
              id="p-name"
              placeholder={t("Сбережения")}
              maxLength={48}
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label>{t("Кошельки")}</Label>
            {wallets.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("Сначала добавьте кошельки.")}</p>
            ) : (
              <ul className="max-h-72 divide-y overflow-y-auto rounded-lg border">
                {wallets.map((w) => (
                  <li key={w.id}>
                    <label className="flex cursor-pointer items-center gap-3 px-3 py-2.5 text-sm hover:bg-muted/50">
                      <Checkbox checked={selected.has(w.id)} onCheckedChange={(c) => toggle(w.id, c === true)} />
                      <span className="flex-1 truncate">{w.name}</span>
                      {w.hidden && <Badge variant="outline">{t("скрыт")}</Badge>}
                      <span className="text-xs text-muted-foreground">{networkLabel(w.network)}</span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-xs text-muted-foreground">
              {t("Скрытый кошелёк остаётся в наборе, но не считается, пока его не вернут.")}
            </p>
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              {t("Отмена")}
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {portfolio ? t("Сохранить") : t("Создать")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
