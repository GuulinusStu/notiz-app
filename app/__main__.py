"""Startet die App: python -m app"""
import os
import socket
import sys
import threading
import webbrowser

import uvicorn

from . import config


def already_running() -> bool:
    """True, wenn auf dem Port schon etwas läuft (z. B. die App in einem anderen Fenster)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((config.HOST, config.PORT)) == 0


def lower_priority() -> None:
    """Etwas tiefere Priorität: Andere Programme bleiben flüssig, die App rechnet genau gleich gut,
    nur nimmt sie sich die Rechenzeit, wenn der Laptop gerade nichts anderes tut."""
    if sys.platform == "win32":
        import ctypes
        BELOW_NORMAL_PRIORITY_CLASS = 0x4000
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
    else:
        try:
            os.nice(5)
        except OSError:
            pass


def main() -> None:
    background = "--hintergrund" in sys.argv  # beim automatischen Start mit Windows: kein Browserfenster
    url = f"http://{config.HOST}:{config.PORT}"
    if already_running():
        if background:
            return
        # Sonst laufen zwei Versionen gleichzeitig und antworten durcheinander.
        print(f"Die Notiz-App läuft schon. Ich öffne sie im Browser: {url}")
        print("Nach einem Update zuerst das andere Notiz-App-Fenster schliessen und dann neu starten.")
        webbrowser.open(url)
        return
    print(f"Notiz-App läuft auf {url}  (Fenster offen lassen, Beenden mit Strg+C)")
    lower_priority()
    if not background:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
