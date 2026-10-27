"""Startet die App: python -m app"""
import socket
import threading
import webbrowser

import uvicorn

from . import config


def already_running() -> bool:
    """True, wenn auf dem Port schon etwas läuft (z. B. die App in einem anderen Fenster)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((config.HOST, config.PORT)) == 0


def main() -> None:
    url = f"http://{config.HOST}:{config.PORT}"
    if already_running():
        # Sonst laufen zwei Versionen gleichzeitig und antworten durcheinander.
        print(f"Die Notiz-App läuft schon. Ich öffne sie im Browser: {url}")
        print("Nach einem Update zuerst das andere Notiz-App-Fenster schliessen und dann neu starten.")
        webbrowser.open(url)
        return
    print(f"Notiz-App läuft auf {url}  (Fenster offen lassen, Beenden mit Strg+C)")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
