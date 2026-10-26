"""Kleiner Client für Ollama (lokale KI): Chat, Embeddings und Statusprüfung."""
import json
import re

import httpx
import numpy as np

from . import config


class LLMError(RuntimeError):
    pass


_THINK = re.compile(r"<think>.*?</think>", re.S)


def chat(messages: list[dict], *, json_mode: bool = False, num_ctx: int = 8192,
         temperature: float = 0.3, timeout: float = 900) -> str:
    payload = {
        "model": config.CHAT_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"num_ctx": num_ctx, "temperature": temperature},
    }
    if json_mode:
        payload["format"] = "json"
    try:
        r = httpx.post(f"{config.OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"Ollama ist nicht erreichbar oder hat einen Fehler gemeldet: {e}") from e
    content = r.json()["message"]["content"]
    return _THINK.sub("", content).strip()


def ask(system: str, user: str, **kwargs) -> str:
    return chat([{"role": "system", "content": system}, {"role": "user", "content": user}], **kwargs)


def embed(texts: list[str], batch_size: int = 16) -> np.ndarray:
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        try:
            r = httpx.post(f"{config.OLLAMA_URL}/api/embed",
                           json={"model": config.EMBED_MODEL, "input": batch}, timeout=600)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Embeddings fehlgeschlagen (läuft Ollama, ist '{config.EMBED_MODEL}' geladen?): {e}") from e
        vectors.extend(r.json()["embeddings"])
    return np.asarray(vectors, dtype=np.float32)


def parse_json(text: str):
    """Liest JSON aus einer KI-Antwort, auch wenn noch Text drumherum steht."""
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


def _has_model(names: list[str], model: str) -> bool:
    return any(n == model or n == f"{model}:latest" for n in names)


def status() -> dict:
    result = {"ollama": False, "chat_model": config.CHAT_MODEL, "embed_model": config.EMBED_MODEL,
              "chat_model_ok": False, "embed_model_ok": False}
    try:
        r = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
        r.raise_for_status()
    except httpx.HTTPError:
        return result
    names = [m["name"] for m in r.json().get("models", [])]
    result.update(ollama=True, chat_model_ok=_has_model(names, config.CHAT_MODEL),
                  embed_model_ok=_has_model(names, config.EMBED_MODEL))
    return result
