"""KI-Anbindung: Antworten über OpenRouter (Internet), Suche mit einem kleinen lokalen Modell."""
import json
import re
import threading

import httpx
import numpy as np

from . import config, db


class LLMError(RuntimeError):
    pass


_THINK = re.compile(r"<think>.*?</think>", re.S)

# Bevorzugte Gratis-Modelle, falls in den Einstellungen nichts gewählt ist (erstes verfügbares gewinnt).
PREFERRED_FREE = ["deepseek", "llama-3.3-70b", "gemma-3-27b", "qwen3", "mistral-small", "openrouter/free"]
# Spezialmodelle, die für Schulnotizen wenig taugen
UNSUITABLE = ("code", "coder", "vision", "-vl", "guard", "embed", "audio", "image")


# --- Einstellungen -----------------------------------------------------------

def api_key() -> str:
    return db.get_setting("openrouter_key") or config.OPENROUTER_KEY


def chat_model() -> str:
    return db.get_setting("chat_model") or config.CHAT_MODEL


def _headers() -> dict:
    key = api_key()
    if not key:
        raise LLMError("Kein OpenRouter-Schlüssel eingetragen. Bitte unter „Einstellungen“ eintragen.")
    return {"Authorization": f"Bearer {key}", "X-Title": "Notiz-App"}


def _error_text(r: httpx.Response) -> str:
    try:
        return r.json().get("error", {}).get("message") or r.text[:200]
    except ValueError:
        return r.text[:200]


# --- Chat --------------------------------------------------------------------

def chat(messages: list[dict], *, json_mode: bool = False, temperature: float = 0.3,
         timeout: float = 300, **_ignored) -> str:
    headers = _headers()
    model = chat_model()
    if not model:
        raise LLMError("Kein KI-Modell gewählt. Bitte unter „Einstellungen“ ein Modell auswählen.")
    payload = {"model": model, "messages": messages, "temperature": temperature}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        r = httpx.post(f"{config.OPENROUTER_URL}/chat/completions", json=payload,
                       headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise LLMError(f"OpenRouter ist nicht erreichbar (Internet?): {e}") from e
    if r.status_code == 401:
        raise LLMError("Der OpenRouter-Schlüssel ist ungültig. Bitte unter „Einstellungen“ prüfen.")
    if r.status_code == 402:
        raise LLMError("Dein OpenRouter-Guthaben ist aufgebraucht. Lade Guthaben auf oder wähle ein Gratis-Modell.")
    if r.status_code == 429:
        raise LLMError("OpenRouter-Limit erreicht (bei Gratis-Modellen gibt es ein Tageslimit). Bitte später nochmals versuchen.")
    if r.status_code >= 400:
        raise LLMError(f"OpenRouter-Fehler {r.status_code}: {_error_text(r)}")
    data = r.json()
    if "error" in data:
        raise LLMError(f"OpenRouter-Fehler: {data['error'].get('message', data['error'])}")
    content = (data["choices"][0]["message"].get("content") or "")
    return _THINK.sub("", content).strip()


def ask(system: str, user: str, **kwargs) -> str:
    return chat([{"role": "system", "content": system}, {"role": "user", "content": user}], **kwargs)


def list_models() -> list[dict]:
    try:
        r = httpx.get(f"{config.OPENROUTER_URL}/models", timeout=20)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"Modell-Liste konnte nicht geladen werden: {e}") from e
    models = []
    for m in r.json().get("data", []):
        pricing = m.get("pricing", {})
        free = m["id"].endswith(":free") or (pricing.get("prompt") == "0" and pricing.get("completion") == "0")
        models.append({"id": m["id"], "name": m.get("name", m["id"]), "free": free,
                       "context": m.get("context_length"),
                       "price_in": pricing.get("prompt"), "price_out": pricing.get("completion")})
    models.sort(key=lambda m: (not m["free"], m["name"].lower()))
    return models


def pick_default_model(models: list[dict]) -> str | None:
    free = [m["id"] for m in models if m["free"] and not any(u in m["id"].lower() for u in UNSUITABLE)]
    for wanted in PREFERRED_FREE:
        for model_id in free:
            if wanted in model_id:
                return model_id
    return free[0] if free else None


# --- Suche (lokal) -----------------------------------------------------------

_embedder = None
_embed_lock = threading.Lock()


def _get_embedder():
    global _embedder
    with _embed_lock:
        if _embedder is None:
            from fastembed import TextEmbedding
            # Eigener Cache-Ordner, weil Windows den Temp-Ordner beim Aufräumen leeren kann.
            _embedder = TextEmbedding(config.EMBED_MODEL, cache_dir=str(config.DATA_DIR / "modelle"))
        return _embedder


def embed(texts: list[str], batch_size: int = 32) -> np.ndarray:
    try:
        vectors = list(_get_embedder().embed(texts, batch_size=batch_size))
    except Exception as e:
        raise LLMError(f"Suchmodell konnte nicht geladen werden: {e}") from e
    return np.asarray(vectors, dtype=np.float32)


# --- JSON --------------------------------------------------------------------

def parse_json(text: str):
    """Liest JSON aus einer KI-Antwort, auch wenn noch Text oder ```-Blöcke drumherum stehen."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    raise LLMError("Die Antwort der KI war kein gültiges JSON. Bitte nochmals versuchen.")


def status() -> dict:
    return {"provider": "openrouter", "key_set": bool(api_key()), "chat_model": chat_model(),
            "embed_model": config.EMBED_MODEL}
