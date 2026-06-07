"""
Reasoner CLI — ask the model what it knows (CLAUDE.md quickstart step 5):

  python -m src.model.cli "What is the current temperature trend?"
  python -m src.model.cli "..." --show-context --show-beliefs
"""

from __future__ import annotations

import argparse
import json
import logging

from src.model.beliefs import BeliefStore
from src.model.reasoner import Reasoner

log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Query the Reasoner via RAG")
    p.add_argument("query", help="Question to ask, e.g. 'What is the current temperature trend?'")
    p.add_argument("--show-context", action="store_true", help="List the retrieved sensor chunk IDs")
    p.add_argument("--show-beliefs", action="store_true", help="Print the stored belief for this query")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )

    result = Reasoner().answer(args.query)

    print(f"Answer: {result['answer']}")
    print(f"Confidence: {result['confidence']:.2f}")
    if result["caveats"]:
        print("Caveats:")
        for caveat in result["caveats"]:
            print(f"  - {caveat}")
    print(f"Supporting sensors: {', '.join(result['supporting_sensors']) or 'none'}")

    if args.show_context:
        print("\nRetrieved chunks:")
        for chunk_id in result["supporting_chunk_ids"]:
            print(f"  - {chunk_id}")

    if args.show_beliefs:
        print("\nStored belief:")
        print(json.dumps(BeliefStore().active_belief(args.query), indent=2))
