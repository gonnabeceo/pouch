import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import App from "./App.tsx"
import { I18nProvider } from "./lib/i18n"
import { StreamerProvider } from "./lib/streamer"
import { initTheme } from "./lib/theme"

// Тема применяется до первой отрисовки, чтобы не было вспышки светлого фона.
initTheme()

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      <StreamerProvider>
        <App />
      </StreamerProvider>
    </I18nProvider>
  </StrictMode>,
)
