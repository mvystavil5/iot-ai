import pytest

from src.model.llm import LLM


def _cfg(backend: str = "ollama") -> dict:
    return {"llm": {
        "backend": backend, "model": "smollm2:135m", "temperature": 0.2,
        "max_tokens": 512, "system_prompt": "You are a helper.",
    }}


class _FakeBackend:
    def __init__(self):
        self.calls: list[dict] = []

    def generate(self, prompt, *, system, temperature, max_tokens):
        self.calls.append({"prompt": prompt, "system": system, "temperature": temperature, "max_tokens": max_tokens})
        return "the answer"


def test_generate_delegates_to_injected_backend_with_config_params():
    backend = _FakeBackend()
    llm = LLM(_cfg(), backend=backend)

    assert llm.generate("hello?") == "the answer"
    assert backend.calls == [{"prompt": "hello?", "system": "You are a helper.", "temperature": 0.2, "max_tokens": 512}]


def test_unknown_backend_raises_value_error():
    llm = LLM(_cfg(backend="bogus-backend"))
    with pytest.raises(ValueError, match="bogus-backend"):
        llm.generate("hello?")


def test_backend_is_constructed_lazily_and_cached():
    backend = _FakeBackend()
    llm = LLM(_cfg(), backend=backend)
    assert llm._get_backend() is backend is llm._get_backend()
