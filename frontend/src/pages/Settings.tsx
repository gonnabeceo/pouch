import { useEffect, useState, type FormEvent } from "react"
import { toast } from "sonner"
import { api, PROVIDER_KEYS, type ProviderKey, type SnapshotSummary } from "@/lib/api"
import { formatWhen } from "@/lib/format"
import { t, useI18n, type Lang } from "@/lib/i18n"
import { HOTKEY_LABEL } from "@/lib/streamer"
import { setTheme, useTheme, type ThemePref } from "@/lib/theme"
import PortfolioSettings from "@/components/PortfolioSettings"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

export default function Settings() {
  useI18n()
  const [keys, setKeys] = useState<Record<ProviderKey, boolean> | null>(null)

  useEffect(() => {
    api.keys().then(setKeys).catch((e) => toast.error(e.message))
  }, [])

  return (
    <div className="space-y-10">
      <h1 className="text-xl font-semibold tracking-tight">{t("Настройки")}</h1>

      <PortfolioSettings />

      <Appearance />

      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-medium">{t("API-ключи провайдеров")}</h2>
          <p className="text-sm text-muted-foreground">
            {t("Хранятся в зашифрованной базе и не возвращаются в браузер. Все сети работают и без ключей, но с жёсткими лимитами.")}
          </p>
        </div>
        <ul className="divide-y rounded-xl border">
          {PROVIDER_KEYS.map((p) => (
            <KeyRow
              key={p.id}
              id={p.id}
              label={p.label}
              hint={t(p.hint)}
              isSet={keys?.[p.id] ?? false}
              onChange={(isSet) => setKeys((k) => (k ? { ...k, [p.id]: isSet } : k))}
            />
          ))}
        </ul>
      </section>

      <RefreshInterval />

      <DustThreshold />

      <ChangePassword />

      <Export />
    </div>
  )
}

type KeyRowProps = {
  id: ProviderKey
  label: string
  hint: string
  isSet: boolean
  onChange: (isSet: boolean) => void
}

function KeyRow({ id, label, hint, isSet, onChange }: KeyRowProps) {
  const [value, setValue] = useState("")
  const [busy, setBusy] = useState(false)

  const save = async (next: string | null) => {
    setBusy(true)
    try {
      await api.setKey(id, next)
      onChange(!!next)
      setValue("")
      toast(next ? t("Ключ {name} сохранён", { name: label }) : t("Ключ {name} удалён", { name: label }))
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (value.trim()) save(value.trim())
  }

  return (
    <li className="space-y-2 px-4 py-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">{label}</span>
        {isSet ? <Badge variant="secondary">{t("задан")}</Badge> : <Badge variant="outline">{t("не задан")}</Badge>}
      </div>
      <p className="text-xs text-muted-foreground">{hint}</p>
      <form onSubmit={submit} className="flex flex-wrap gap-2 sm:flex-nowrap">
        <Input
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder={isSet ? t("Новый ключ, чтобы заменить") : t("Ключ")}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="min-w-0 flex-1 font-mono text-xs"
        />
        <Button type="submit" variant="outline" disabled={busy || !value.trim()}>
          {t("Сохранить")}
        </Button>
        {isSet && (
          <Button type="button" variant="ghost" disabled={busy} onClick={() => save(null)}>
            {t("Удалить")}
          </Button>
        )}
      </form>
    </li>
  )
}

function DustThreshold() {
  const [value, setValue] = useState("")
  const [saved, setSaved] = useState<number | null>(null)

  useEffect(() => {
    api
      .general()
      .then((s) => {
        setSaved(s.dust_threshold_usd)
        setValue(String(s.dust_threshold_usd))
      })
      .catch((e) => toast.error(e.message))
  }, [])

  const parsed = Number(value.replace(",", "."))
  const valid = value.trim() !== "" && Number.isFinite(parsed) && parsed >= 0

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!valid) return
    try {
      const s = await api.setGeneral({ dust_threshold_usd: parsed })
      setSaved(s.dust_threshold_usd)
      toast(t("Порог пыли сохранён"))
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-sm font-medium">{t("Порог пыли")}</h2>
        <p className="text-sm text-muted-foreground">
          {t("Токены и позиции дешевле порога скрыты и в итог не входят. 0 — показывать всё.")}
        </p>
      </div>
      <form onSubmit={submit} className="flex items-center gap-2">
        <Label htmlFor="dust" className="sr-only">
          {t("Порог пыли в долларах")}
        </Label>
        <span className="text-sm text-muted-foreground">$</span>
        <Input
          id="dust"
          inputMode="decimal"
          className="w-28 tabular-nums"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-invalid={!valid}
        />
        <Button type="submit" variant="outline" disabled={!valid || parsed === saved}>
          {t("Сохранить")}
        </Button>
      </form>
    </section>
  )
}

function Appearance() {
  const { pref } = useTheme()
  const { lang, setLang } = useI18n()
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-sm font-medium">{t("Оформление")}</h2>
        <p className="text-sm text-muted-foreground">
          {t("Тема и язык переключаются и кнопками в шапке. Режим стримера прячет суммы и количества: кнопка с глазом или")}{" "}
          <kbd className="rounded border px-1 font-mono text-xs">{HOTKEY_LABEL}</kbd>.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex items-center gap-3">
          <Label htmlFor="theme" className="text-sm font-normal text-muted-foreground">
            {t("Тема")}
          </Label>
          <Select value={pref} onValueChange={(v) => setTheme(v as ThemePref)}>
            <SelectTrigger id="theme" className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="system">{t("Как в системе")}</SelectItem>
              <SelectItem value="light">{t("Светлая")}</SelectItem>
              <SelectItem value="dark">{t("Тёмная")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-3">
          <Label htmlFor="lang" className="text-sm font-normal text-muted-foreground">
            {t("Язык")}
          </Label>
          <Select value={lang} onValueChange={(v) => setLang(v as Lang)}>
            <SelectTrigger id="lang" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ru">Русский</SelectItem>
              <SelectItem value="en">English</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
    </section>
  )
}

function ChangePassword() {
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  const [repeat, setRepeat] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (next !== repeat) return setError(t("Новые пароли не совпадают"))
    setBusy(true)
    try {
      await api.changePassword(current, next)
      setCurrent("")
      setNext("")
      setRepeat("")
      toast(t("Мастер-пароль изменён"))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-sm font-medium">{t("Мастер-пароль")}</h2>
        <p className="text-sm text-muted-foreground">
          {t("База перешифровывается новым ключом. Старый пароль после смены не подходит.")}
        </p>
      </div>
      <form onSubmit={submit} className="grid max-w-sm gap-2">
        <Input type="password" autoComplete="current-password" placeholder={t("Текущий пароль")}
          value={current} onChange={(e) => setCurrent(e.target.value)} />
        <Input type="password" autoComplete="new-password" placeholder={t("Новый пароль")}
          value={next} onChange={(e) => setNext(e.target.value)} />
        <Input type="password" autoComplete="new-password" placeholder={t("Новый пароль ещё раз")}
          value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button type="submit" variant="outline" className="justify-self-start" disabled={busy || !current || !next}>
          {busy ? t("Перешифровываю…") : t("Сменить пароль")}
        </Button>
      </form>
    </section>
  )
}

function RefreshInterval() {
  const [value, setValue] = useState("")
  const [saved, setSaved] = useState<number | null>(null)

  useEffect(() => {
    api
      .general()
      .then((s) => {
        setSaved(s.refresh_interval_minutes)
        setValue(String(s.refresh_interval_minutes))
      })
      .catch((e) => toast.error(e.message))
  }, [])

  const parsed = Number(value)
  const valid = Number.isInteger(parsed) && parsed >= 1 && parsed <= 60

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!valid) return
    try {
      const s = await api.setGeneral({ refresh_interval_minutes: parsed })
      setSaved(s.refresh_interval_minutes)
      toast(t("Интервал сохранён, действует с открытия главной"))
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-sm font-medium">{t("Автообновление")}</h2>
        <p className="text-sm text-muted-foreground">
          {t("Пока главная открыта, балансы и цены обновляются сами. Данные старше трёх интервалов помечаются «устарел».")}
        </p>
      </div>
      <form onSubmit={submit} className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">{t("каждые")}</span>
        <Input
          inputMode="numeric"
          className="w-20 tabular-nums"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label={t("Интервал в минутах")}
          aria-invalid={!valid}
        />
        <span className="text-sm text-muted-foreground">{t("мин")}</span>
        <Button type="submit" variant="outline" disabled={!valid || parsed === saved}>
          {t("Сохранить")}
        </Button>
      </form>
    </section>
  )
}

function Export() {
  const [summary, setSummary] = useState<SnapshotSummary | null>(null)
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.snapshotSummary().then(setSummary).catch(() => {})
  }, [])

  const run = async (what: "csv" | "json" | "backup") => {
    setBusy(true)
    try {
      const name = what === "backup" ? await api.exportBackup(password) : await api.exportSnapshots(password, what)
      toast(t("Сохранён файл {name}", { name }))
      setPassword("")
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-sm font-medium">{t("Экспорт и бэкап")}</h2>
        <p className="text-sm text-muted-foreground">
          {t("Снапшоты сохраняются после полного успешного обновления, не чаще раза в час.")}{" "}
          {summary &&
            (summary.count
              ? t("Сейчас {n}, последний — {time}.", { n: summary.count, time: formatWhen(summary.last!) })
              : t("Пока ни одного."))}{" "}
          {t("CSV и JSON — открытые данные, бэкап — зашифрованная база, без пароля не читается.")}
        </p>
      </div>
      <div className="grid max-w-sm gap-2">
        <Input
          type="password"
          autoComplete="current-password"
          placeholder={t("Мастер-пароль для экспорта")}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" disabled={busy || !password || !summary?.count} onClick={() => run("csv")}>
            {t("Снапшоты CSV")}
          </Button>
          <Button variant="outline" disabled={busy || !password || !summary?.count} onClick={() => run("json")}>
            {t("Снапшоты JSON")}
          </Button>
          <Button variant="outline" disabled={busy || !password} onClick={() => run("backup")}>
            {t("Зашифрованный бэкап")}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          {t("Забытый пароль = потеря данных: бэкап восстанавливается только паролем, действовавшим в момент бэкапа.")}
        </p>
      </div>
    </section>
  )
}
