import httpx
import pytest

from app import db, llm


class FakeResponse:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


def test_chat_needs_key():
    with pytest.raises(llm.LLMError, match="Schlüssel"):
        llm.ask("s", "u")


def test_chat_sends_model_and_key(monkeypatch):
    db.set_setting("openrouter_key", "sk-or-test")
    db.set_setting("chat_model", "foo/bar:free")
    seen = {}

    def post(url, json, headers, timeout):
        seen.update(url=url, json=json, headers=headers)
        return FakeResponse(200, {"choices": [{"message": {"content": "<think>hm</think>Hallo!"}}]})

    monkeypatch.setattr(httpx, "post", post)
    assert llm.ask("s", "u", json_mode=True) == "Hallo!"
    assert seen["json"]["model"] == "foo/bar:free"
    assert seen["json"]["response_format"] == {"type": "json_object"}
    assert seen["headers"]["Authorization"] == "Bearer sk-or-test"


@pytest.mark.parametrize("status,needle", [(401, "ungültig"), (402, "Guthaben"), (429, "Limit"), (500, "500")])
def test_chat_errors_are_german(monkeypatch, status, needle):
    db.set_setting("openrouter_key", "sk-or-test")
    db.set_setting("chat_model", "foo/bar")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse(status, {"error": {"message": "x"}}))
    with pytest.raises(llm.LLMError, match=needle):
        llm.ask("s", "u")


def test_pick_default_model_prefers_known_free_models():
    models = [{"id": "a/paid", "free": False}, {"id": "x/other:free", "free": True},
              {"id": "meta-llama/llama-3.3-70b-instruct:free", "free": True}]
    assert llm.pick_default_model(models) == "meta-llama/llama-3.3-70b-instruct:free"
    assert llm.pick_default_model([{"id": "a/paid", "free": False}]) is None
    assert llm.pick_default_model([{"id": "cohere/north-mini-code:free", "free": True},
                                   {"id": "openrouter/free", "free": True}]) == "openrouter/free"
