import { useCallback, useEffect, useState } from "react"
import { CopyIcon, EllipsisIcon, PlusIcon } from "lucide-react"
import { toast } from "sonner"
import { api, networkLabel, shortAddress, type Wallet } from "@/lib/api"
import { formatDate } from "@/lib/format"
import { t, useI18n } from "@/lib/i18n"
import { useStreamer } from "@/lib/streamer"
import { cn } from "@/lib/utils"
import WalletDialog from "@/components/WalletDialog"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"

const SHOW_HIDDEN_KEY = "pouch.showHidden"

const readShowHidden = () => {
  try {
    return localStorage.getItem(SHOW_HIDDEN_KEY) === "1"
  } catch {
    return false
  }
}

export default function Wallets() {
  useI18n()
  const [wallets, setWallets] = useState<Wallet[] | null>(null)
  const [showHidden, setShowHidden] = useState(readShowHidden)
  const [editing, setEditing] = useState<Wallet | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleting, setDeleting] = useState<Wallet | null>(null)
  const { on: streamer } = useStreamer()

  const load = useCallback(() => {
    api.wallets(showHidden).then(setWallets).catch((e) => toast.error(e.message))
  }, [showHidden])

  useEffect(load, [load])

  const toggleShowHidden = (value: boolean) => {
    setShowHidden(value)
    try {
      localStorage.setItem(SHOW_HIDDEN_KEY, value ? "1" : "0")
    } catch {
      /* без запоминания */
    }
  }

  const openDialog = (wallet: Wallet | null) => {
    setEditing(wallet)
    setDialogOpen(true)
  }

  const setHidden = async (w: Wallet, hidden: boolean) => {
    try {
      await api.updateWallet(w.id, { hidden })
      toast(hidden ? t("«{name}» скрыт", { name: w.name }) : t("«{name}» снова учитывается", { name: w.name }), {
        action: hidden ? { label: t("Вернуть"), onClick: () => setHidden(w, false) } : undefined,
      })
      load()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const confirmDelete = async () => {
    if (!deleting) return
    try {
      await api.deleteWallet(deleting.id)
      toast(t("«{name}» удалён", { name: deleting.name }))
      load()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setDeleting(null)
    }
  }

  const copy = async (address: string) => {
    await navigator.clipboard.writeText(address)
    toast(t("Адрес скопирован"))
  }

  const hiddenCount = wallets?.filter((w) => w.hidden).length ?? 0

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <h1 className="text-xl font-semibold tracking-tight">{t("Кошельки")}</h1>
        <Button onClick={() => openDialog(null)} className="ml-auto sm:order-last">
          <PlusIcon />
          {t("Добавить")}
        </Button>
        <div className="flex w-full items-center gap-2 sm:ml-auto sm:w-auto">
          <Switch id="show-hidden" checked={showHidden} onCheckedChange={toggleShowHidden} />
          <Label htmlFor="show-hidden" className="text-sm font-normal text-muted-foreground">
            {t("Показать скрытые")}
            {showHidden && hiddenCount > 0 ? ` (${hiddenCount})` : ""}
          </Label>
        </div>
      </div>

      {wallets === null ? null : wallets.length === 0 ? (
        <div className="rounded-xl border border-dashed p-10 text-center">
          <p className="text-sm text-muted-foreground">
            {showHidden ? t("Кошельков нет") : t("Нет видимых кошельков")}
          </p>
        </div>
      ) : (
        <ul className="divide-y rounded-xl border">
          {wallets.map((w) => (
            <li key={w.id} className="flex items-start gap-2 px-3 py-4 sm:gap-4 sm:px-4">
              <div className={cn("min-w-0 flex-1 space-y-1", w.hidden && "opacity-50")}>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="truncate text-sm font-medium">{w.name}</span>
                  <Badge variant="secondary">{networkLabel(w.network)}</Badge>
                  {w.hidden && <Badge variant="outline">{t("скрыт")}</Badge>}
                </div>
                <button
                  type="button"
                  onClick={() => copy(w.address)}
                  className="group flex max-w-full items-center gap-1.5 font-mono text-xs text-muted-foreground hover:text-foreground"
                  title={t("Скопировать адрес")}
                >
                  <span className="truncate">{streamer ? shortAddress(w.address) : w.address}</span>
                  <CopyIcon className="size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-100" />
                </button>
                {w.note &&
                  (streamer ? (
                    <p className="text-sm text-muted-foreground italic">{t("Заметка скрыта")}</p>
                  ) : (
                    <p className="text-sm whitespace-pre-line text-muted-foreground">{w.note}</p>
                  ))}
                <p className="text-xs text-muted-foreground">
                  {t("Добавлен {date}", { date: formatDate(w.created_at, { day: "numeric", month: "short", year: "numeric" }) })}
                </p>
              </div>

              {w.hidden ? (
                <Button variant="outline" size="sm" onClick={() => setHidden(w, false)}>
                  {t("Вернуть")}
                </Button>
              ) : null}

              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon-sm" aria-label={t("Действия")}>
                    <EllipsisIcon />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onSelect={() => openDialog(w)}>{t("Редактировать")}</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => setHidden(w, !w.hidden)}>
                    {w.hidden ? t("Вернуть") : t("Скрыть")}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" onSelect={() => setDeleting(w)}>
                    {t("Удалить")}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          ))}
        </ul>
      )}

      <WalletDialog open={dialogOpen} wallet={editing} onOpenChange={setDialogOpen} onSaved={load} />

      <AlertDialog open={!!deleting} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("Удалить «{name}»?", { name: deleting?.name ?? "" })}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("Кошелёк и заметка будут удалены. Если нужно только убрать его из итогов, лучше скрыть.")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("Отмена")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmDelete}>
              {t("Удалить")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
