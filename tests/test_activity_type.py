"""Tests for Garmin activity-type reclassification helpers and config."""
from __future__ import annotations

from keiser_garmin_sync.config import Config
from keiser_garmin_sync.garmin import (
    GARMIN_CYCLING_TYPES,
    activity_type_key,
)


def test_indoor_cycling_type_ids() -> None:
    # Sourced from Garmin's /activity-types endpoint; guards against regressions.
    assert GARMIN_CYCLING_TYPES["indoor_cycling"] == (25, "indoor_cycling", 2)
    assert GARMIN_CYCLING_TYPES["cycling"] == (2, "cycling", 17)


def test_activity_type_key_from_summary_and_detail() -> None:
    assert activity_type_key({"activityType": {"typeKey": "cycling"}}) == "cycling"
    assert activity_type_key({"activityTypeDTO": {"typeKey": "indoor_cycling"}}) == "indoor_cycling"
    assert activity_type_key({}) == ""
    assert activity_type_key({"activityType": {}}) == ""


def test_default_activity_type_is_indoor_cycling() -> None:
    cfg = Config.load(use_files=False)
    assert cfg.garmin_activity_type == "indoor_cycling"


def test_activity_type_env_override(monkeypatch) -> None:
    monkeypatch.setenv("GARMIN_ACTIVITY_TYPE", "virtual_ride")
    cfg = Config.load(use_files=False)
    assert cfg.garmin_activity_type == "virtual_ride"
