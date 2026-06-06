---
name: query-knowledge
description: Ask the model what it currently knows or believes about the environment. Uses RAG over the vector store. Shows retrieved context, answer, and confidence.
---

When this skill is invoked:

1. Get the query from the user (or the argument passed to the skill).

2. Run the RAG query:
   ```bash
   python -m src.model.cli "{query}" --show-context --show-beliefs
   ```

3. Display:
   - **Answer**: the model's response
   - **Confidence**: 0–1 score
   - **Supporting sensors**: which sensor readings were retrieved
   - **Active beliefs**: any prior beliefs on this topic from `data/beliefs.jsonl`
   - **Caveats**: if confidence < 0.5, suggest running `/run-experiment` to fill the gap

4. If this is a repeated low-confidence query (confidence < 0.4 and asked > 3 times), automatically escalate to the Explorer agent.
