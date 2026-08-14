"""Build a Garmin-ingestible TCX file from a Keiser M Series ride.

Garmin Connect accepts TCX uploads and renders cycling power (via the Garmin
ActivityExtension ``TPX/Watts`` element), cadence, speed, distance and
calories. TCX is used instead of binary FIT to avoid CRC/encoding pitfalls --
the resulting activity in Garmin Connect is identical in the fields we have
(Keiser does not expose heart rate in the ride dataset, so HR is absent).

Keiser semantics (verified against the live API, Aug 2026):
  * dataset.duration is the ride TOTAL in MILLISECONDS (e.g. 2868000 = 47.8 min).
    dataset.distance (m) and dataset.caloricBurn are TOTALS.
  * each graphData point carries: ``takenAt`` (absolute ISO time), ``duration``
    (cumulative SECONDS since start), a per-sample ``distance`` DELTA in metres
    (NOT cumulative -- it is the distance covered in that sample interval), and
    instantaneous ``cadence`` (rpm) and ``power`` (W).

Because TCX ``DistanceMeters`` on a Trackpoint must be CUMULATIVE and
monotonically non-decreasing, we accumulate the per-sample deltas into a running
total and scale it so the final point equals ``dataset.distance``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from xml.sax.saxutils import escape

TCX_NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
AX_NS = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_tcx(dataset: dict[str, Any], device_name: str = "Keiser M Series") -> str:
    started = _parse_ts(dataset.get("startedAt")) or datetime.now(timezone.utc)
    # Keiser reports the ride duration in MILLISECONDS; TCX needs seconds.
    duration = _num(dataset.get("duration")) / 1000.0
    total_distance = _num(dataset.get("distance"))
    calories = int(round(_num(dataset.get("caloricBurn"))))

    points: list[dict[str, Any]] = dataset.get("graphData") or []

    # First pass: resolve each point's absolute time and a running CUMULATIVE
    # distance (Keiser's per-point ``distance`` is a per-sample delta, so we sum
    # it). We scale the cumulative track to the ride total afterwards.
    resolved: list[dict[str, Any]] = []
    cum = 0.0
    max_watts = 0
    for p in points:
        pt_time = _parse_ts(p.get("takenAt"))
        if pt_time is None:
            pt_time = started + timedelta(seconds=_num(p.get("duration")))
        cum += max(0.0, _num(p.get("distance")))
        cadence = int(round(_num(p.get("cadence"))))
        watts = int(round(_num(p.get("power"))))
        max_watts = max(max_watts, watts)
        resolved.append({"time": pt_time, "cum": cum, "cadence": cadence, "watts": watts})

    # Scale the cumulative distance so the last trackpoint matches the ride
    # total (the per-sample deltas don't sum exactly to dataset.distance).
    scale = (total_distance / cum) if cum > 0 and total_distance > 0 else 1.0

    trackpoints: list[str] = []
    prev_time: datetime | None = None
    prev_dist = 0.0
    for r in resolved:
        pt_time = r["time"]
        dist = r["cum"] * scale

        # instantaneous speed (m/s) from the distance delta, when we can.
        speed_xml = ""
        if prev_time is not None:
            dt_s = (pt_time - prev_time).total_seconds()
            if dt_s > 0 and dist >= prev_dist:
                speed = (dist - prev_dist) / dt_s
                speed_xml = f"<ns3:Speed>{speed:.3f}</ns3:Speed>"
        prev_time, prev_dist = pt_time, dist

        cadence = r["cadence"]
        watts = r["watts"]
        trackpoints.append(
            "<Trackpoint>"
            f"<Time>{_iso(pt_time)}</Time>"
            f"<DistanceMeters>{dist:.2f}</DistanceMeters>"
            f"<Cadence>{max(0, min(254, cadence))}</Cadence>"
            "<Extensions><ns3:TPX>"
            f"<ns3:Watts>{max(0, watts)}</ns3:Watts>"
            f"{speed_xml}"
            "</ns3:TPX></Extensions>"
            "</Trackpoint>"
        )

    avg_cadence = int(round(_num(dataset.get("averageCadence"))))
    avg_watts = int(round(_num(dataset.get("averagePower"))))
    max_watts = max(max_watts, int(round(_num(dataset.get("maxPower")))))
    avg_speed = (total_distance / duration) if duration > 0 else 0.0

    track = "".join(trackpoints)
    lap = (
        f'<Lap StartTime="{_iso(started)}">'
        f"<TotalTimeSeconds>{duration:.1f}</TotalTimeSeconds>"
        f"<DistanceMeters>{total_distance:.2f}</DistanceMeters>"
        f"<Calories>{calories}</Calories>"
        f"<Cadence>{max(0, min(254, avg_cadence))}</Cadence>"
        "<Intensity>Active</Intensity>"
        "<TriggerMethod>Manual</TriggerMethod>"
        f"<Track>{track}</Track>"
        "<Extensions><ns3:LX>"
        f"<ns3:AvgSpeed>{avg_speed:.3f}</ns3:AvgSpeed>"
        f"<ns3:AvgWatts>{max(0, avg_watts)}</ns3:AvgWatts>"
        f"<ns3:MaxWatts>{max(0, max_watts)}</ns3:MaxWatts>"
        "</ns3:LX></Extensions>"
        "</Lap>"
    )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<TrainingCenterDatabase xmlns="{TCX_NS}" xmlns:ns3="{AX_NS}" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<Activities>"
        '<Activity Sport="Biking">'
        f"<Id>{_iso(started)}</Id>"
        f"{lap}"
        '<Creator xsi:type="Device_t">'
        f"<Name>{escape(device_name)}</Name>"
        "<UnitId>0</UnitId><ProductID>0</ProductID>"
        "<Version><VersionMajor>1</VersionMajor><VersionMinor>0</VersionMinor>"
        "<BuildMajor>0</BuildMajor><BuildMinor>0</BuildMinor></Version>"
        "</Creator>"
        "</Activity>"
        "</Activities>"
        "</TrainingCenterDatabase>"
    )


def suggested_filename(dataset: dict[str, Any]) -> str:
    started = _parse_ts(dataset.get("startedAt")) or datetime.now(timezone.utc)
    return f"keiser-{dataset.get('id', 'ride')}-{started.strftime('%Y%m%d-%H%M%S')}.tcx"
