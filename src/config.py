from functools import lru_cache
from pathlib import Path
import yaml


_ROOT = Path(__file__).parent.parent


@lru_cache(maxsize=1)
def load_model_config() -> dict:
    return yaml.safe_load((_ROOT / "config" / "model.yaml").read_text())


@lru_cache(maxsize=1)
def load_sensor_registry() -> dict:
    return yaml.safe_load((_ROOT / "config" / "sensors.yaml").read_text())


@lru_cache(maxsize=1)
def load_agent_config() -> dict:
    return yaml.safe_load((_ROOT / "config" / "agents.yaml").read_text())
