"""
Internal event bus — simple in-process pub/sub for the Phase 1 single-board
deployment (see docs/architecture.md § Event bus). Replace with NATS or
Redis Streams when agents move to separate processes/hosts (Phase 2).

Known topics (producer -> consumer):
  knowledge_chunks   ingestion        -> knowledge builder
  store_updated      knowledge builder -> reasoner (cache invalidation)
  low_confidence     reasoner         -> explorer
  belief_invalidated reasoner         -> explorer
  labeled_examples   explorer         -> trainer
  model_updated      trainer          -> reasoner (model reload)
  identity_registered identity registration -> (audit / future consumers)
  identity_revoked   identity registration  -> (audit / future consumers)
  identity_matched   identity matcher       -> (audit / future consumers)

Usage:
  from src.events import bus

  def on_chunk(chunk): ...
  bus.subscribe("knowledge_chunks", on_chunk)
  bus.publish("knowledge_chunks", chunk)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[[Any], None]


class EventBus:
    """Synchronous, in-process publish/subscribe bus.

    Handlers run inline on publish, in subscription order. A handler that
    raises is logged and does not prevent the remaining handlers from running
    — one misbehaving consumer must not break the producer's call path."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subscribers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        handlers = self._subscribers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    def publish(self, topic: str, payload: Any = None) -> None:
        handlers = self._subscribers.get(topic, [])
        log.debug("publish %s -> %d subscriber(s)", topic, len(handlers))
        for handler in list(handlers):
            try:
                handler(payload)
            except Exception:
                log.exception("Handler %r for topic %r raised", handler, topic)

    def clear(self, topic: str | None = None) -> None:
        """Remove all subscribers for a topic, or every topic if none given.
        Mainly useful for test isolation between cases that share `bus`."""
        if topic is None:
            self._subscribers.clear()
        else:
            self._subscribers.pop(topic, None)


# Module-level singleton — agents import and share this instance directly,
# matching the "agents share filesystem context" model in CLAUDE.md.
bus = EventBus()
