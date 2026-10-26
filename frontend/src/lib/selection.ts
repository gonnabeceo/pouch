// Выбранное портфолио — общее для главной и истории, запоминается между запусками.
const SELECTED_KEY = "pouch.portfolio"

export function readSelected(): number | null {
  try {
    const value = Number(localStorage.getItem(SELECTED_KEY))
    return Number.isInteger(value) && value > 0 ? value : null
  } catch {
    return null
  }
}

export function saveSelected(id: number | null) {
  try {
    if (id === null) localStorage.removeItem(SELECTED_KEY)
    else localStorage.setItem(SELECTED_KEY, String(id))
  } catch {
    /* без запоминания */
  }
}
