"""Tests for TCX generation -- the duration (ms->s) and distance fixes."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from keiser_garmin_sync.tcx import build_tcx, suggested_filename

NS = {"t": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}


def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def test_duration_is_seconds_not_milliseconds(keiser_ride):
    root = _parse(build_tcx(keiser_ride))
    total = float(root.find(".//t:Lap/t:TotalTimeSeconds", NS).text)
    # 2,868,000 ms must render as 2868 s, NOT 2,868,000.
    assert abs(total - 2868.0) < 1.0


def test_lap_totals_match_source(keiser_ride):
    root = _parse(build_tcx(keiser_ride))
    dist = float(root.find(".//t:Lap/t:DistanceMeters", NS).text)
    cal = int(root.find(".//t:Lap/t:Calories", NS).text)
    assert abs(dist - 29814.0) < 1.0
    assert cal == 824


def test_trackpoint_distance_is_monotonic_and_scaled_to_total(keiser_ride):
    root = _parse(build_tcx(keiser_ride))
    dists = [float(n.text) for n in root.findall(".//t:Trackpoint/t:DistanceMeters", NS)]
    assert dists == sorted(dists), "cumulative distance must be non-decreasing"
    # final trackpoint should equal the ride total (deltas summed to 18300, scaled up)
    assert abs(dists[-1] - 29814.0) < 1.0


def test_power_extension_present(keiser_ride):
    xml = build_tcx(keiser_ride)
    assert "ns3:Watts" in xml
    assert 'Sport="Biking"' in xml


def test_suggested_filename(keiser_ride):
    name = suggested_filename(keiser_ride)
    assert name.startswith("keiser-8400859-")
    assert name.endswith(".tcx")


def test_zero_graph_blip_uses_scaled_duration():
    """A 14 s blip (14000 ms, no graphData) must not become 14000 s."""
    blip = {"id": 1, "startedAt": "2026-08-13T15:28:53.000Z", "duration": 14000,
            "distance": 37.0, "caloricBurn": 0.0, "graphData": []}
    root = _parse(build_tcx(blip))
    total = float(root.find(".//t:Lap/t:TotalTimeSeconds", NS).text)
    assert abs(total - 14.0) < 1.0
