// Проверка перевода: у каждой строки t("…") и каждой подсказки hint: "…" должен быть английский вариант.
import { readFileSync, readdirSync, statSync } from "node:fs"
import { join } from "node:path"

const files = []
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) {
      if (name !== "ui") walk(path)
    } else if (/\.(tsx?|mts)$/.test(name) && !name.startsWith("i18n")) files.push(path)
  }
}
walk("src")

const en = readFileSync("src/lib/i18n.en.ts", "utf8")
const known = new Set([...en.matchAll(/^\s*"((?:[^"\\]|\\.)*)":/gm)].map((m) => m[1]))
const used = new Set()
for (const file of files) {
  const src = readFileSync(file, "utf8")
  for (const m of src.matchAll(/\bt\(\s*"((?:[^"\\]|\\.)*)"/g)) used.add(m[1])
  for (const m of src.matchAll(/\bhint: "((?:[^"\\]|\\.)*)"/g)) used.add(m[1])
  // Подписи, которые переводятся через t(переменная): словари вида { key: "…" } с кириллицей.
  for (const m of src.matchAll(/^\s*(?:\w+|\[[^\]]+\]):\s*(?:\["[^"]*",\s*)?"([^"]*[А-Яа-яЁё][^"]*)"/gm)) used.add(m[1])
}
const missing = [...used].filter((s) => /[А-Яа-яЁё]/.test(s) && !known.has(s))
if (missing.length) {
  console.error(`Нет английского перевода для ${missing.length} строк:\n` + missing.map((s) => `  ${s}`).join("\n"))
  process.exit(1)
}
console.log(`i18n: ${used.size} строк, перевод есть у всех`)
