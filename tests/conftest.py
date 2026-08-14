"""Shared fixtures: a realistic Keiser ride dataset."""
from __future__ import annotations

import pytest


@pytest.fixture
def keiser_ride() -> dict:
    """A 4-point ride: 2868 s (2,868,000 ms), 29,814 m total, 824 cal.

    Per-point ``distance`` values are per-sample DELTAS (metres) that do NOT sum
    to the total -- exercising the accumulate-and-scale logic. ``duration`` on a
    point is cumulative SECONDS.
    """
    return {
        "id": 8400859,
        "startedAt": "2026-08-03T14:27:38.000Z",
        "endedAt": "2026-08-03T15:15:26.000Z",
        "duration": 2868000,          # milliseconds
        "distance": 29814.0,          # metres, total
        "caloricBurn": 824.0,
        "machineType": "M3",
        "averageCadence": 85,
        "averagePower": 150,
        "maxPower": 320,
        "graphData": [
            {"takenAt": "2026-08-03T14:27:38.000Z", "duration": 0,    "distance": 0.0,   "cadence": 0,  "power": 0},
            {"takenAt": "2026-08-03T14:42:38.000Z", "duration": 900,  "distance": 6000., "cadence": 90, "power": 160},
            {"takenAt": "2026-08-03T14:57:38.000Z", "duration": 1800, "distance": 6200., "cadence": 88, "power": 155},
            {"takenAt": "2026-08-03T15:15:26.000Z", "duration": 2868, "distance": 6100., "cadence": 80, "power": 140},
        ],
    }
