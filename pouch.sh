#!/bin/sh
# Запуск Pouch одной командой: собирает фронт при необходимости и поднимает сервер на 127.0.0.1.
set -e
cd "$(dirname "$0")"

if [ ! -f frontend/dist/index.html ] || [ -n "$(find frontend/src frontend/index.html -newer frontend/dist/index.html 2>/dev/null | head -1)" ]; then
  echo "Собираю фронт…"
  (cd frontend && { [ -d node_modules ] || npm ci; } && npm run build >/dev/null)
fi

exec uv run --project backend --frozen -q python -m pouch
