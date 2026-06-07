"""
Reasoner — the model's "thinking" layer (.claude/agents/reasoner.md):
retrieve -> build prompt -> call the LLM -> derive a RAG-grounded confidence
-> track beliefs with uncertainty.

Confidence is computed by src.model.rag_confidence (coverage x similarity x
recency x consistency — no LLM introspection required), not parsed from the
LLM's own self-report: a 135M model's stated confidence isn't trustworthy,
but retrieval-quality metadata is.

  python -m src.model.cli "What is the current temperature trend?"
"""

from __future__ import annotations

import logging

from src.config import load_model_config
from src.events import bus
from src.model.beliefs import BeliefStore
from src.model.llm import LLM
from src.model.rag_confidence import RetrievalResult, compute_rag_confidence
from src.model.retriever import Retriever

log = logging.getLogger(__name__)

# Confidence bands from .claude/agents/reasoner.md § Uncertainty handling
LOW_CONFIDENCE = 0.4
PLAUSIBLE_CONFIDENCE = 0.7

PROMPT_TEMPLATE = """\
You are a physical-world reasoning system. Below is recent sensor data relevant to the question.
Use ONLY this data plus known physical laws to answer. State your confidence (0-1).
If the data is insufficient, say so explicitly.

--- SENSOR CONTEXT ---
{context}
--- END CONTEXT ---

Question: {query}
"""


def _build_context(result: RetrievalResult) -> str:
    if not result.chunks:
        return "(no relevant sensor data found)"
    return "\n".join(f"- {chunk.text}" for chunk in result.chunks)


def _caveats(confidence: float, result: RetrievalResult) -> list[str]:
    """Plain-language caveats matching the confidence bands in
    .claude/agents/reasoner.md § Uncertainty handling."""
    caveats: list[str] = []
    if not result.chunks:
        caveats.append("No matching sensor data was retrieved for this query.")
    elif confidence < LOW_CONFIDENCE:
        caveats.append("Low confidence — limited or stale supporting sensor data.")
    elif confidence < PLAUSIBLE_CONFIDENCE:
        caveats.append("Moderate confidence — answer is plausible but not strongly supported.")
    return caveats


class Reasoner:
    """`retriever`/`llm`/`beliefs` are injectable so the chain can be tested
    without a vector store, Ollama, or a filesystem belief log."""

    def __init__(
        self,
        retriever: Retriever | None = None,
        llm: LLM | None = None,
        beliefs: BeliefStore | None = None,
        cfg: dict | None = None,
    ) -> None:
        cfg = cfg or load_model_config()
        self._cfg = cfg
        self.retriever = retriever or Retriever(cfg=cfg)
        self.llm = llm or LLM(cfg=cfg)
        self.beliefs = beliefs or BeliefStore(cfg=cfg)

    def answer(self, query: str) -> dict:
        """Run the full RAG chain for `query`; returns
        {query, answer, confidence, supporting_sensors, supporting_chunk_ids,
        caveats, belief}. Always records (and returns) a belief, even when
        the LLM call fails — a context-only summary still beats silence."""
        result = self.retriever.retrieve(query)
        confidence = compute_rag_confidence(result, self._cfg)
        prompt = PROMPT_TEMPLATE.format(context=_build_context(result), query=query)

        try:
            answer_text = self.llm.generate(prompt)
        except Exception:
            log.exception("LLM generation failed for %r — falling back to a context-only summary", query)
            answer_text = _build_context(result)

        supporting_sensors = sorted({chunk.sensor_id for chunk in result.chunks})
        supporting_chunk_ids = [chunk.chunk_id for chunk in result.chunks]
        caveats = _caveats(confidence, result)

        belief = self.beliefs.record(query, answer_text, confidence, supporting_chunk_ids)
        if confidence < LOW_CONFIDENCE:
            bus.publish("low_confidence", {"query": query, "confidence": confidence, "belief": belief})

        return {
            "query": query,
            "answer": answer_text,
            "confidence": confidence,
            "supporting_sensors": supporting_sensors,
            "supporting_chunk_ids": supporting_chunk_ids,
            "caveats": caveats,
            "belief": belief,
        }
