"""
Unit normalization — converts raw readings into the canonical units the rest
of the system expects (see .claude/agents/ingestion.md § Normalization rules):
temperature -> Celsius, pressure -> kPa, humidity -> %RH. Units already in
canonical form, or with no known conversion rule, pass through unchanged;
the latter is tagged unit_normalized=False so it can be flagged downstream
("unknown units: log a warning, store raw, tag with unit_normalized=false").
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

CANONICAL_UNITS: dict[str, str] = {
    "temperature": "C",
    "humidity": "%RH",
    "pressure": "kPa",
}

_CONVERSIONS: dict[tuple[str, str], Callable[[float], float]] = {
    ("F", "C"): lambda v: (v - 32.0) * 5.0 / 9.0,
    ("K", "C"): lambda v: v - 273.15,
    ("psi", "kPa"): lambda v: v * 6.894757,
    ("bar", "kPa"): lambda v: v * 100.0,
    ("atm", "kPa"): lambda v: v * 101.325,
}


def normalize(sensor_type: str, value: float, unit: str) -> tuple[float, str, bool]:
    """Convert (value, unit) to the canonical unit for `sensor_type`.

    Returns (normalized_value, normalized_unit, unit_normalized). Passes the
    reading through unchanged when: the sensor type has no canonical unit,
    the unit is already canonical, or no conversion rule is registered for
    (unit -> canonical) — the last case sets unit_normalized=False.
    """
    canonical = CANONICAL_UNITS.get(sensor_type)
    if canonical is None or unit == canonical:
        return value, unit, True

    convert = _CONVERSIONS.get((unit, canonical))
    if convert is None:
        log.warning(
            "No conversion rule for %s -> %s (sensor_type=%s) — storing raw value",
            unit, canonical, sensor_type,
        )
        return value, unit, False

    return round(convert(value), 4), canonical, True
