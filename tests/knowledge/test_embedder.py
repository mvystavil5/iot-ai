import pytest

from src.knowledge.embedder import Embedder


def _cfg() -> dict:
    return {"embedding": {"model": "nomic-embed-text", "dim": 4, "batch_size": 32}}


class _FakeClient:
    def __init__(self, dim: int = 4):
        self.dim = dim
        self.calls: list[tuple[str, str]] = []

    def embeddings(self, model: str, prompt: str) -> dict:
        self.calls.append((model, prompt))
        # deterministic "embedding": length of text repeated to fill dim
        return {"embedding": [float(len(prompt))] * self.dim}


def test_embed_calls_client_per_text_and_preserves_order():
    client = _FakeClient()
    embedder = Embedder(_cfg(), client=client)

    vectors = embedder.embed(["hello", "a longer string"])

    assert len(vectors) == 2
    assert client.calls == [("nomic-embed-text", "hello"), ("nomic-embed-text", "a longer string")]
    assert vectors[0] == [5.0, 5.0, 5.0, 5.0]
    assert vectors[1] == [15.0, 15.0, 15.0, 15.0]


def test_embed_one_returns_single_vector():
    embedder = Embedder(_cfg(), client=_FakeClient())
    assert embedder.embed_one("hi") == [2.0, 2.0, 2.0, 2.0]


def test_embed_warns_on_dim_mismatch(caplog):
    client = _FakeClient(dim=3)  # config says dim=4
    embedder = Embedder(_cfg(), client=client)

    with caplog.at_level("WARNING"):
        embedder.embed(["x"])

    assert any("dim=3" in record.message for record in caplog.records)
