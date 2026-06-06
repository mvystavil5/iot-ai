---
name: run-experiment
description: Trigger one active-exploration cycle. The Explorer agent picks the highest-ranked hypothesis, designs an experiment, runs it, and reports the outcome. Use to actively fill knowledge gaps.
---

When this skill is invoked:

1. Show the current hypothesis queue:
   ```bash
   python -m src.exploration.scheduler --list
   ```

2. If the queue is empty, run hypothesis generation first:
   ```bash
   python -m src.exploration.hypothesis_generator --run
   ```

3. Ask the user to confirm the top hypothesis before running (show the falsification condition).

4. Run the experiment:
   ```bash
   python -m src.exploration.scheduler --run-next --verbose
   ```

5. Report:
   - Hypothesis tested
   - Outcome: confirmed / refuted / inconclusive
   - Confidence delta on affected beliefs
   - Whether a labeled example was added to the training queue
   - Updated belief state summary
