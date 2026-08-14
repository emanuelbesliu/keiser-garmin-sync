"""Post-upload validation: confirm the activity Garmin stored actually matches
the Keiser source ride, so a silently mangled TCX (wrong duration/distance, like
the ms-as-seconds bug) is caught instead of shipping bad data.

We compare the three summary numbers Garmin computes server-side (duration,
distance, calories) against the Keiser dataset totals, with tolerances generous
enough to absorb legitimate rounding / algorithm differences but tight enough to
flag order-of-magnitude mistakes.
"""
from __future__ import annotations

from typing import Any


def source_summary(dataset: dict[str, Any]) -> dict[str, float]:
    """Reduce a Keiser dataset to the values we can cross-check in Garmin."""
    def num(v: Any) -> float:
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    return {
        # Keiser reports duration in milliseconds.
        "duration_s": num(dataset.get("duration")) / 1000.0,
        "distance_m": num(dataset.get("distance")),
        "calories": num(dataset.get("caloricBurn")),
    }


# Tolerances: absolute floor OR relative fraction, whichever is larger.
_DURATION_ABS_S = 60.0
_DURATION_REL = 0.05
_DISTANCE_ABS_M = 250.0
_DISTANCE_REL = 0.05
_CALORIES_ABS = 30.0
_CALORIES_REL = 0.15
_CYCLING_TYPES = {"cycling", "indoor_cycling", "virtual_ride"}


def _within(measured: float, expected: float, abs_tol: float, rel_tol: float) -> bool:
    return abs(measured - expected) <= max(abs_tol, abs(expected) * rel_tol)


def compare(source: dict[str, float], activity: dict[str, Any]) -> list[str]:
    """Return a list of human-readable mismatch strings (empty == valid)."""
    issues: list[str] = []

    def num(v: Any) -> float:
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    g_dur = num(activity.get("duration"))
    g_dist = num(activity.get("distance"))
    g_cal = num(activity.get("calories"))
    g_type = ((activity.get("activityType") or {}).get("typeKey") or "").lower()

    if not _within(g_dur, source["duration_s"], _DURATION_ABS_S, _DURATION_REL):
        issues.append(
            f"duration {g_dur:.0f}s in Garmin vs {source['duration_s']:.0f}s from Keiser"
        )
    if not _within(g_dist, source["distance_m"], _DISTANCE_ABS_M, _DISTANCE_REL):
        issues.append(
            f"distance {g_dist:.0f}m in Garmin vs {source['distance_m']:.0f}m from Keiser"
        )
    if source["calories"] > 0 and not _within(
        g_cal, source["calories"], _CALORIES_ABS, _CALORIES_REL
    ):
        issues.append(
            f"calories {g_cal:.0f} in Garmin vs {source['calories']:.0f} from Keiser"
        )
    if g_type and g_type not in _CYCLING_TYPES:
        issues.append(f"activity type '{g_type}' is not a cycling type")

    return issues
