import { useState, type FormEvent } from "react"
import icon from "@/assets/icon.png"
import { api, type Status } from "@/lib/api"
import { t, useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export default function Login({ status, onUnlocked }: { status: Status; onUnlocked: () => void }) {
  const { lang, setLang } = useI18n()
  const firstRun = !status.initialized
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (firstRun) {
      if (password.length < status.min_password_length) {
        return setError(t("Минимум {n} символов", { n: status.min_password_length }))
      }
      if (password !== confirm) return setError(t("Пароли не совпадают"))
    }
    setBusy(true)
    try {
      await (firstRun ? api.setup(password) : api.unlock(password))
      setPassword("")
      setConfirm("")
      onUnlocked()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative flex min-h-svh items-center justify-center px-4 sm:px-6">
      <Button
        variant="ghost"
        size="sm"
        className="absolute top-4 right-4 text-xs font-semibold"
        onClick={() => setLang(lang === "ru" ? "en" : "ru")}
        aria-label={lang === "ru" ? "Switch to English" : "Переключить на русский"}
      >
        {lang === "ru" ? "EN" : "RU"}
      </Button>
      <form onSubmit={submit} className="w-full max-w-xs space-y-6">
        <div className="space-y-1.5">
          <img src={icon} alt="" className="mb-5 size-14 rounded-[22%]" />
          <h1 className="text-xl font-semibold tracking-tight">Pouch</h1>
          <p className="text-sm text-muted-foreground">
            {firstRun ? t("Задайте мастер-пароль. Им шифруется вся база.") : t("Введите мастер-пароль")}
          </p>
        </div>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="password" className="sr-only">
              {t("Мастер-пароль")}
            </Label>
            <Input
              id="password"
              type="password"
              autoComplete={firstRun ? "new-password" : "current-password"}
              placeholder={t("Мастер-пароль")}
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={!!error}
            />
          </div>
          {firstRun && (
            <div className="space-y-1.5">
              <Label htmlFor="confirm" className="sr-only">
                {t("Повторите пароль")}
              </Label>
              <Input
                id="confirm"
                type="password"
                autoComplete="new-password"
                placeholder={t("Ещё раз")}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <Button type="submit" className="w-full" disabled={busy || !password}>
          {busy ? t("Проверяю…") : firstRun ? t("Создать базу") : t("Открыть")}
        </Button>

        {firstRun && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("Пароль нигде не хранится. Если его забыть, данные не восстановить.")}
          </p>
        )}
      </form>
    </div>
  )
}
