from __future__ import annotations

import logging

from .instagram import ScraplingInstagramAdapter


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    adapter = ScraplingInstagramAdapter()
    try:
        adapter._ensure_session()
    finally:
        adapter.close()
    print("Scrapling browser started successfully.")


if __name__ == "__main__":
    main()
