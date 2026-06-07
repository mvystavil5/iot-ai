"""
Knowledge Builder CLI — inspect store health.

  python -m src.knowledge.cli stats
"""

from __future__ import annotations

import argparse
import json
import logging

from src.knowledge.store import VectorStore

log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Knowledge store inspection")
    p.add_argument("command", choices=["stats"], help="Command to run")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    if args.command == "stats":
        print(json.dumps(VectorStore().stats(), indent=2))
