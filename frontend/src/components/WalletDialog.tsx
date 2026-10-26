import { useEffect, useState, type FormEvent } from "react"
import { api, NETWORKS, type Network, type Wallet, type WalletInput } from "@/lib/api"
import { t } from "@/lib/i18n"
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"

const EMPTY: WalletInput = { name: "", network: "evm", address: "", note: "" }

type Props = {
  open: boolean
  wallet: Wallet | null // null — новый кошелёк
  onOpenChange: (open: boolean) => void
  onSaved: (wallet: Wallet) => void
}

export default function WalletDialog({ open, wallet, onOpenChange, onSaved }: Props) {
  const [form, setForm] = useState<WalletInput>(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setForm(wallet ? { name: wallet.name, network: wallet.network, address: wallet.address, note: wallet.note } : EMPTY)
    setError(null)
  }, [open, wallet])

  const set = <K extends keyof WalletInput>(key: K, value: WalletInput[K]) => setForm((f) => ({ ...f, [key]: value }))
  const hint = NETWORKS.find((n) => n.id === form.network)?.hint

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onSaved(wallet ? await api.updateWallet(wallet.id, form) : await api.createWallet(form))
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
            <DialogTitle>{wallet ? t("Редактировать кошелёк") : t("Новый кошелёк")}</DialogTitle>
            <DialogDescription>{t("Только публичный адрес. Ключи и сид-фразы сюда не вводятся.")}</DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="w-name">{t("Название")}</Label>
              <Input
                id="w-name"
                placeholder={t("Сбережения")}
                maxLength={64}
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
                autoFocus
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="w-network">{t("Сеть")}</Label>
              <Select value={form.network} onValueChange={(v) => set("network", v as Network)}>
                <SelectTrigger id="w-network" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {NETWORKS.map((n) => (
                    <SelectItem key={n.id} value={n.id}>
                      {n.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="w-address">{t("Адрес")}</Label>
              <Input
                id="w-address"
                className="font-mono text-xs"
                spellCheck={false}
                autoComplete="off"
                maxLength={128}
                value={form.address}
                onChange={(e) => set("address", e.target.value)}
              />
              {hint && <p className="text-xs text-muted-foreground">{t(hint)}</p>}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="w-note">{t("Заметка")}</Label>
              <Textarea
                id="w-note"
                rows={3}
                maxLength={2000}
                placeholder={t("Зачем кошелёк, где хранятся ключи")}
                value={form.note}
                onChange={(e) => set("note", e.target.value)}
              />
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              {t("Отмена")}
            </Button>
            <Button type="submit" disabled={busy || !form.name.trim() || !form.address.trim()}>
              {wallet ? t("Сохранить") : t("Добавить")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
