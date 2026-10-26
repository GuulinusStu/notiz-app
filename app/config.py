"""Zentrale Einstellungen. Alles kann über Umgebungsvariablen angepasst werden."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NOTIZAPP_DATA", ROOT / "data"))
DB_PATH = DATA_DIR / "notizapp.db"
UPLOAD_DIR = DATA_DIR / "uploads"

HOST = os.environ.get("NOTIZAPP_HOST", "127.0.0.1")
PORT = int(os.environ.get("NOTIZAPP_PORT", "8000"))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
CHAT_MODEL = os.environ.get("NOTIZAPP_CHAT_MODEL", "qwen3:14b")
# bge-m3 ist mehrsprachig und funktioniert gut mit deutschen Texten.
EMBED_MODEL = os.environ.get("NOTIZAPP_EMBED_MODEL", "bge-m3")

WHISPER_MODEL = os.environ.get("NOTIZAPP_WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.environ.get("NOTIZAPP_WHISPER_DEVICE", "auto")  # auto | cpu | cuda
WHISPER_LANGUAGE = os.environ.get("NOTIZAPP_WHISPER_LANGUAGE", "de")

# OneNote-Abschnitte beim Start der App automatisch exportieren (nur Windows)
ONENOTE_AUTO_SYNC = os.environ.get("NOTIZAPP_ONENOTE_AUTO_SYNC", "1") == "1"
