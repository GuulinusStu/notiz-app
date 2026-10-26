"""Startet die App: python -m app"""
import threading
import webbrowser

import uvicorn

from . import config


def main() -> None:
    url = f"http://{config.HOST}:{config.PORT}"
    print(f"Notiz-App läuft auf {url}  (Fenster offen lassen, Beenden mit Strg+C)")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
