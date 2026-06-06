---
name: train-checkpoint
description: Kick off a LoRA fine-tuning run using accumulated labeled examples. Evaluates the result and promotes if it beats the current checkpoint.
---

When this skill is invoked:

1. Check training readiness:
   ```bash
   python -m src.model.trainer --check-readiness
   ```
   This reports: N labeled examples available, current model score, last training run date.

2. If < 10 labeled examples exist, warn the user and suggest running `/run-experiment` several times first.

3. Confirm with the user before starting (training can take several minutes on CPU).

4. Run fine-tuning:
   ```bash
   python -m src.model.trainer --run --verbose
   ```

5. Report:
   - Training loss curve (final train/val loss)
   - Eval accuracy vs. previous checkpoint
   - Whether the new checkpoint was promoted
   - New checkpoint path and model registry entry

6. If the new checkpoint was promoted, notify the Reasoner to reload the model.
