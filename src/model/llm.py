"""
LLM wrapper — thin facade over the configured backend: Ollama local by
default, Claude API as the documented fallback for more capable hardware
(see .claude/agents/reasoner.md § LLM backends and config/model.yaml: llm).

Backend SDKs are imported lazily (and the whole client is injectable) so
this module stays importable/testable without Ollama running or an
Anthropic API key configured — the lazy-import convention src.model.trainer
and src.knowledge.embedder also follow.
"""

from __future__ import annotations

import logging
from typing import Protocol

from src.config import load_model_config

log = logging.getLogger(__name__)


class _Backend(Protocol):
    def generate(self, prompt: str, *, system: str, temperature: float, max_tokens: int) -> str: ...


class _OllamaBackend:
    def __init__(self, model: str) -> None:
        self.model = model

    def generate(self, prompt: str, *, system: str, temperature: float, max_tokens: int) -> str:
        import ollama

        response = ollama.generate(
            model=self.model,
            prompt=prompt,
            system=system,
            options={"temperature": temperature, "num_predict": max_tokens},
        )
        return response["response"]


class _ClaudeBackend:
    def __init__(self, model: str) -> None:
        self.model = model

    def generate(self, prompt: str, *, system: str, temperature: float, max_tokens: int) -> str:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=self.model,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


_BACKENDS = {"ollama": _OllamaBackend, "claude-api": _ClaudeBackend}


class LLM:
    """`backend` is injectable — tests pass a fake exposing
    `.generate(prompt, *, system, temperature, max_tokens) -> str`."""

    def __init__(self, cfg: dict | None = None, backend: _Backend | None = None) -> None:
        llm_cfg = (cfg or load_model_config())["llm"]
        self.backend_name = llm_cfg["backend"]
        self.model = llm_cfg["model"]
        self.temperature = llm_cfg["temperature"]
        self.max_tokens = llm_cfg["max_tokens"]
        self.system_prompt = llm_cfg["system_prompt"]
        self._backend = backend

    def _get_backend(self) -> _Backend:
        if self._backend is None:
            backend_cls = _BACKENDS.get(self.backend_name)
            if backend_cls is None:
                raise ValueError(
                    f"Unknown llm.backend {self.backend_name!r} in config/model.yaml "
                    f"(expected one of {sorted(_BACKENDS)})"
                )
            self._backend = backend_cls(self.model)
        return self._backend

    def generate(self, prompt: str) -> str:
        return self._get_backend().generate(
            prompt,
            system=self.system_prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
