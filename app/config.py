"""Zentrale Einstellungen. Alles kann über Umgebungsvariablen angepasst werden."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NOTIZAPP_DATA", ROOT / "data"))
DB_PATH = DATA_DIR / "notizapp.db"
UPLOAD_DIR = DATA_DIR / "uploads"

HOST = os.environ.get("NOTIZAPP_HOST", "127.0.0.1")
PORT = int(os.environ.get("NOTIZAPP_PORT", "8000"))

OPENROUTER_URL = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1")
# Schlüssel und Modell werden normalerweise in der App unter „Einstellungen“ eingetragen.
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
CHAT_MODEL = os.environ.get("NOTIZAPP_CHAT_MODEL", "")
# Kleines mehrsprachiges Modell für die Suche, läuft lokal und braucht wenig Leistung.
EMBED_MODEL = os.environ.get("NOTIZAPP_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

WHISPER_MODEL = os.environ.get("NOTIZAPP_WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.environ.get("NOTIZAPP_WHISPER_DEVICE", "auto")  # auto | cpu | cuda
WHISPER_LANGUAGE = os.environ.get("NOTIZAPP_WHISPER_LANGUAGE", "de")

# OneNote-Abschnitte beim Start der App automatisch exportieren (nur Windows)
ONENOTE_AUTO_SYNC = os.environ.get("NOTIZAPP_ONENOTE_AUTO_SYNC", "1") == "1"
# Bildseiten, die wegen des Tageslimits fehlen, alle paar Stunden nochmals versuchen
AUTO_RETRY = os.environ.get("NOTIZAPP_AUTO_RETRY", "1") == "1"
# Gratis-Modelle erlauben etwa 50 Anfragen pro Tag. Das Abschreiben von Handschrift im Hintergrund
# darf davon nur einen Teil brauchen, damit Fragen, Quiz und Karteikarten immer funktionieren.
IMAGE_DAILY_BUDGET = int(os.environ.get("NOTIZAPP_IMAGE_DAILY_BUDGET", "25"))
# Datenbank täglich sichern (in OneDrive, falls vorhanden)
AUTO_BACKUP = os.environ.get("NOTIZAPP_AUTO_BACKUP", "1") == "1"
# Sekunden nach dem Start, bevor OneNote-Abgleich und Sicherung laufen (schont den Laptop beim Anmelden)
STARTUP_DELAY = float(os.environ.get("NOTIZAPP_STARTUP_DELAY", "120"))
