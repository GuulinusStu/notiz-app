# 📚 Notiz-App

Eine Lern-App für den eigenen Laptop: OneNote-Notizen importieren, Unterricht aufnehmen,
und dann mit einer KI lernen (Fragen stellen, Zusammenfassungen, Quiz, Karteikarten).

**Alles läuft lokal und gratis.** Aufnahmen und Notizen verlassen den Laptop nicht.

## Was die App kann

- **OneNote importieren:** OneNote-Abschnitte als PDF exportieren und hochladen, oder Text einfügen.
- **Unterricht aufnehmen:** Direkt in der App aufnehmen oder eine Audiodatei hochladen.
  Whisper wandelt die Aufnahme in Text um, die KI bereinigt ihn (Schweizerdeutsch → Hochdeutsch,
  Pausen und Gruppengespräche raus) und schreibt eine Zusammenfassung.
- **Fragen stellen:** „Was war nochmal …?“ – die KI sucht in allen Notizen und nennt die Quellen.
- **Quiz und Karteikarten:** Aus jeder Notiz ein Quiz oder Karteikarten erstellen. Karteikarten
  werden nach einem Wiederholungsplan abgefragt (wie bei Anki).

## Einrichtung (Windows, einmalig)

1. **Python installieren:** https://www.python.org/downloads/ – beim Installieren
   **„Add python.exe to PATH“** ankreuzen.
2. **Ollama installieren:** https://ollama.com/download – danach läuft Ollama im Hintergrund.
3. **KI-Modelle herunterladen:** Eingabeaufforderung (cmd) öffnen und eingeben:
   ```
   ollama pull qwen3:14b
   ollama pull bge-m3
   ```
   Das sind zusammen etwa 10 GB, also etwas Geduld.
4. **App herunterladen:** Auf GitHub auf „Code → Download ZIP“ klicken und entpacken
   (oder mit `git clone`).

## Starten

Doppelklick auf **`start.bat`**. Beim ersten Mal wird alles installiert (ein paar Minuten),
danach öffnet sich die App im Browser unter http://127.0.0.1:8000.

Das schwarze Fenster muss offen bleiben, solange du die App benutzt.

Bei der ersten Aufnahme wird das Spracherkennungsmodell (ca. 1,6 GB) heruntergeladen.

## OneNote als PDF exportieren

In der **OneNote-App am PC** (nicht im Browser): Abschnitt auswählen → **Datei → Exportieren →
Abschnitt → PDF → Exportieren**. Die PDFs dann in der App unter „OneNote-PDFs importieren“ hochladen.

Hinweis: Getippter Text wird gelesen. Handschrift und Text in Bildern werden noch nicht erkannt.

## Tipps zur Aufnahme

- **Frag vorher die Lehrperson**, ob du aufnehmen darfst. In der Schweiz ist es verboten,
  Gespräche ohne Einverständnis aufzunehmen.
- Laptop während der Aufnahme nicht zuklappen und das Browserfenster offen lassen.
- Die Transkription läuft danach im Hintergrund. Eine Lektion von 45 Minuten braucht je nach
  Laptop etwa 10 bis 30 Minuten. Du kannst die App in der Zeit normal weiterbenutzen.

## Einstellungen (optional)

Über Umgebungsvariablen, z. B. in `start.bat` vor `python -m app` mit `set NAME=Wert`:

| Variable | Standard | Bedeutung |
|---|---|---|
| `NOTIZAPP_CHAT_MODEL` | `qwen3:14b` | KI-Modell für Antworten (kleiner/schneller: `qwen3:8b`) |
| `NOTIZAPP_EMBED_MODEL` | `bge-m3` | Modell für die Suche |
| `NOTIZAPP_WHISPER_MODEL` | `large-v3-turbo` | Spracherkennung (schneller, ungenauer: `small`) |
| `NOTIZAPP_DATA` | `data/` | Wo Notizen und Aufnahmen gespeichert werden |
| `NOTIZAPP_PORT` | `8000` | Port im Browser |

## Für Entwickler

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
python -m app
```

Aufbau: FastAPI-Server (`app/main.py`), SQLite-Datenbank (`app/db.py`), Import und Transkription
(`app/ingest.py`), Lernfunktionen (`app/study.py`), Oberfläche in `app/static/`.
