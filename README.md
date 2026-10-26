# 📚 Notiz-App

Eine Lern-App für den eigenen Laptop: OneNote-Notizen importieren, Unterricht aufnehmen,
und dann mit einer KI lernen (Fragen stellen, Zusammenfassungen, Quiz, Karteikarten).

Die KI (Antworten, Zusammenfassungen, Quiz) läuft über **OpenRouter** im Internet, damit der Laptop
nicht überlastet wird. Spracherkennung (Whisper) und Suche laufen lokal.

## Was die App kann

- **OneNote importieren:** OneNote-Abschnitte als PDF exportieren und hochladen, oder Text einfügen.
- **Unterricht aufnehmen:** Direkt in der App aufnehmen oder eine Audiodatei hochladen.
  Whisper wandelt die Aufnahme in Text um, die KI bereinigt ihn (Schweizerdeutsch → Hochdeutsch,
  Pausen und Gruppengespräche raus) und schreibt eine Zusammenfassung.
- **Fragen stellen:** „Was war nochmal …?“ – die KI sucht in allen Notizen und nennt die Quellen.
- **Fächer:** Links ein Fach wählen. Notizen, Fragen, Quiz und Karteikarten beziehen sich dann nur auf
  dieses Fach. OneNote-Notizbücher werden automatisch zu Fächern, mit ✏️ kann man sie umbenennen
  oder zusammenlegen. Beim Aufnehmen wählt man das Fach aus.
- **Quiz und Karteikarten:** Für eine einzelne Notiz oder für ein ganzes Fach. Karteikarten
  werden nach einem Wiederholungsplan abgefragt (wie bei Anki).

## Einrichtung (Windows, einmalig)

1. **Python installieren:** https://www.python.org/downloads/ – beim Installieren
   **„Add python.exe to PATH“** ankreuzen.
2. **OpenRouter einrichten:** Auf https://openrouter.ai ein Konto erstellen und unter
   https://openrouter.ai/settings/keys einen Schlüssel erstellen. Den Schlüssel trägst du später
   in der App unter ⚙️ Einstellungen ein (nicht in einen Chat oder eine Datei kopieren).
   Gratis-Modelle haben ein Tageslimit. Mit ein paar Franken Guthaben gibt es mehr Anfragen und
   bessere Modelle.
3. **App herunterladen:** Auf GitHub auf „Code → Download ZIP“ klicken und entpacken
   (oder mit `git clone`).

## Starten

Doppelklick auf **`start.bat`**. Beim ersten Mal wird alles installiert (ein paar Minuten),
danach öffnet sich die App im Browser unter http://127.0.0.1:8000.

Das schwarze Fenster muss offen bleiben, solange du die App benutzt.

Beim ersten Start wird das kleine Suchmodell (ca. 250 MB) heruntergeladen, bei der ersten Aufnahme
das Spracherkennungsmodell (ca. 1,6 GB).

## OneNote importieren

**Automatisch (empfohlen):** In der App auf **„OneNote synchronisieren“** klicken. Die App holt
alle Abschnitte aus der **OneNote-App auf deinem PC** (die aus Microsoft 365, nicht die Web-Version)
und exportiert jeden Abschnitt als PDF. Beim Start der App passiert das auch automatisch.
Beim nächsten Mal werden nur Abschnitte neu geholt, die sich geändert haben.

Voraussetzung: Die Notizbücher sind in der OneNote-App geöffnet und synchronisiert.
Gesperrte (passwortgeschützte) Abschnitte werden übersprungen.

**Von Hand:** In der OneNote-App: Abschnitt auswählen → **Datei → Exportieren → Abschnitt → PDF**.
Die PDFs dann unter „Oder PDFs selbst hochladen“ importieren.

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
| `OPENROUTER_API_KEY` | – | Schlüssel, falls nicht in der App eingetragen |
| `NOTIZAPP_CHAT_MODEL` | – | KI-Modell, falls nicht in der App gewählt |
| `NOTIZAPP_EMBED_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | Lokales Modell für die Suche |
| `NOTIZAPP_WHISPER_MODEL` | `large-v3-turbo` | Spracherkennung (schneller, ungenauer: `small`) |
| `NOTIZAPP_DATA` | `data/` | Wo Notizen und Aufnahmen gespeichert werden |
| `NOTIZAPP_PORT` | `8000` | Port im Browser |
| `NOTIZAPP_ONENOTE_AUTO_SYNC` | `1` | OneNote beim Start synchronisieren (`0` = aus) |

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
