"""
Opt-in identity registration — the consent mechanism at the heart of this
module (see docs/architecture.md § Identity & consent, plan
"Opt-in Identity Registration & Occupancy Attribution").

A person who *wants* to be recognized consciously starts a registration
window ("tag the next hour as me"); once the window has elapsed, `register`
aggregates the board's existing motion history over that window into a
behavioral-routine signature (src/identity/signature.build_signature) and
stores it. Nothing is captured passively or without the subject's knowledge.

Revocation is symmetric and absolute: `revoke` hard-deletes the profile AND
purges every match record that ever referenced it — a real purge, not a
revoked_at flag left sitting next to live data. That distinction is what
makes this a consent system rather than surveillance with extra steps.

  python -m src.identity.registration --register "Person A" --duration 3600
  python -m src.identity.registration --revoke person_a_3f9a21
  python -m src.identity.registration --list
"""

from __future__ import annotations

import argparse
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import load_sensor_registry
from src.events import bus
from src.identity import store
from src.identity.matcher import _motion_sensor_id
from src.identity.signature import build_signature
from src.ingestion.storage import TimeSeriesStore

log = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _profile_id(display_name: str) -> str:
    slug = _SLUG_RE.sub("_", display_name.strip().lower()).strip("_") or "profile"
    return f"{slug}_{uuid.uuid4().hex[:6]}"


def register(
    display_name: str,
    window_start: datetime,
    window_end: datetime,
    *,
    ts_store: TimeSeriesStore | None = None,
    registry: dict | None = None,
    profiles_path: Path | None = None,
    cfg: dict | None = None,
) -> dict:
    """Aggregate the (already-elapsed) [window_start, window_end) motion
    history into a routine signature and persist it as a new profile.
    `cfg` is accepted (and forwarded nowhere yet) purely so callers can pass
    a fixture config the same way other DI'd functions in this codebase do —
    signature building takes no config today."""
    registry = registry if registry is not None else load_sensor_registry()
    ts_store = ts_store or TimeSeriesStore()

    sensor_id = _motion_sensor_id(registry)
    if sensor_id is None:
        raise ValueError("No motion sensor in the registry — cannot build a routine signature")

    readings = ts_store.query(sensor_id, since=window_start.isoformat(), limit=100_000)
    in_window = [r for r in readings if r.timestamp <= window_end]
    chronological = list(reversed(in_window))  # TimeSeriesStore.query is most-recent-first

    profile = {
        "profile_id": _profile_id(display_name),
        "display_name": display_name,
        "consent_at": _now_iso(),
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "signature": build_signature(chronological),
        "revoked_at": None,
    }

    store._append(profiles_path or store.DEFAULT_PROFILES_PATH, profile)
    bus.publish("identity_registered", profile)
    log.info(
        "Registered profile %s (%s) from %d motion reading(s)",
        profile["profile_id"], display_name, profile["signature"]["sample_size"],
    )
    return profile


def revoke(
    profile_id: str,
    *,
    profiles_path: Path | None = None,
    matches_path: Path | None = None,
) -> bool:
    """Hard-delete a profile and purge every match record that references
    it. Returns False if the profile doesn't exist (or was already
    revoked), True once the purge completes."""
    profiles_path = profiles_path or store.DEFAULT_PROFILES_PATH
    matches_path = matches_path or store.DEFAULT_MATCHES_PATH

    profiles = store.load_profiles(profiles_path)
    remaining = [p for p in profiles if p["profile_id"] != profile_id]
    if len(remaining) == len(profiles):
        return False
    store._rewrite(profiles_path, remaining)

    matches = store.load_matches(matches_path)
    purged_matches = [m for m in matches if m.get("profile_id") != profile_id]
    if len(purged_matches) != len(matches):
        store._rewrite(matches_path, purged_matches)

    bus.publish("identity_revoked", {"profile_id": profile_id, "revoked_at": _now_iso()})
    log.info(
        "Revoked profile %s — purged 1 profile record and %d match record(s)",
        profile_id, len(matches) - len(purged_matches),
    )
    return True


def list_profiles(*, profiles_path: Path | None = None) -> list[dict]:
    """Summary view of non-revoked profiles — id/name/consent date only;
    the raw signature is omitted from this default listing (it's still on
    disk for the matcher, just not surfaced here for casual inspection)."""
    return [
        {"profile_id": p["profile_id"], "display_name": p["display_name"], "consent_at": p["consent_at"]}
        for p in store.load_profiles(profiles_path)
        if p.get("revoked_at") is None
    ]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Opt-in identity registration — register/revoke/list routine profiles")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", metavar="DISPLAY_NAME", help="Start a registration window under this name")
    group.add_argument("--revoke", metavar="PROFILE_ID", help="Hard-delete a profile and purge its match history")
    group.add_argument("--list", action="store_true", help="List active (non-revoked) profiles")
    p.add_argument("--duration", type=int, default=3600, help="Registration window length in seconds (with --register)")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    if args.register:
        start, end = _now(), _now() + timedelta(seconds=args.duration)
        print(f"Registration window open for {args.register}: now until {args.duration}s from now.")
        print("Go about your normal routine — recording starts immediately and stops automatically.")
        try:
            time.sleep(args.duration)
        except KeyboardInterrupt:
            end = _now()
            print("\nWindow ended early — registering on the partial window.")
        profile = register(args.register, start, end)
        print(f"Registered {profile['display_name']} as {profile['profile_id']} "
              f"({profile['signature']['sample_size']} motion reading(s) captured).")
    elif args.revoke:
        if revoke(args.revoke):
            print(f"Revoked {args.revoke} — profile and all match history purged.")
        else:
            print(f"No active profile found with id {args.revoke!r}.")
    else:
        profiles = list_profiles()
        if not profiles:
            print("No registered profiles.")
        for p in profiles:
            print(f"{p['profile_id']}  {p['display_name']}  (registered {p['consent_at']})")
