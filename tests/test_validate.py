"""Tests for the post-upload validator tolerances."""
from __future__ import annotations

from keiser_garmin_sync.validate import compare, source_summary


def test_source_summary_converts_ms_to_seconds(keiser_ride):
    src = source_summary(keiser_ride)
    assert abs(src["duration_s"] - 2868.0) < 1.0
    assert src["distance_m"] == 29814.0
    assert src["calories"] == 824.0


def _garmin_activity(duration_s, distance_m, calories, type_key="cycling"):
    return {
        "duration": duration_s,
        "distance": distance_m,
        "calories": calories,
        "activityType": {"typeKey": type_key},
    }


def test_matching_activity_has_no_issues(keiser_ride):
    src = source_summary(keiser_ride)
    act = _garmin_activity(2868, 29814, 824)
    assert compare(src, act) == []


def test_within_tolerances(keiser_ride):
    src = source_summary(keiser_ride)
    # small deltas inside abs/rel tolerances
    act = _garmin_activity(2868 + 40, 29814 + 100, 824 + 20)
    assert compare(src, act) == []


def test_duration_order_of_magnitude_flagged(keiser_ride):
    src = source_summary(keiser_ride)
    act = _garmin_activity(13980, 30, 0)  # the ms-as-seconds bug shape
    issues = compare(src, act)
    assert any("duration" in i for i in issues)
    assert any("distance" in i for i in issues)


def test_wrong_activity_type_flagged(keiser_ride):
    src = source_summary(keiser_ride)
    act = _garmin_activity(2868, 29814, 824, type_key="running")
    issues = compare(src, act)
    assert any("type" in i for i in issues)


def test_zero_calories_source_skips_calorie_check(keiser_ride):
    src = source_summary(keiser_ride)
    src["calories"] = 0.0
    act = _garmin_activity(2868, 29814, 999)
    assert compare(src, act) == []
