import logging
import os

import uvicorn

from . import config
from .main import create_app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("pouch").setLevel(os.environ.get("POUCH_LOG_LEVEL", "INFO"))
    # httpx и httpcore на INFO/DEBUG пишут полные URL — в них адреса кошельков и ключ Helius.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    if not (config.STATIC_DIR / "index.html").exists():
        print("Фронт не собран: выполните ./pouch.sh (он соберёт его сам)")
    print(f"Pouch: http://{config.HOST}:{config.PORT}")
    # access_log выключен: логи не должны содержать адресов и сумм.
    uvicorn.run(create_app(), host=config.HOST, port=config.PORT, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
