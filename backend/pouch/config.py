import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("POUCH_DATA_DIR", ROOT / "data"))
STATIC_DIR = ROOT / "frontend" / "dist"

# Сервер слушает только loopback, снаружи недоступен.
HOST = "127.0.0.1"
PORT = int(os.environ.get("POUCH_PORT", "8420"))

AUTOLOCK_SECONDS = 15 * 60

# Режим разработки: разрешает запросы от Vite dev-сервера на :5173.
DEV = os.environ.get("POUCH_DEV") == "1"
