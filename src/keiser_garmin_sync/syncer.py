"""One sync cycle: Keiser cloud -> TCX -> Garmin Connect, with dedup."""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Config
from .garmin import GarminAuthError, GarminUploader
from .keiser import KeiserClient, KeiserError
from .store import Store
from .tcx import build_tcx, suggested_filename
from .validate import compare, source_summary

logger = logging.getLogger("keiser-garmin-sync.syncer")


class Syncer:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.garmin = GarminUploader(
            cfg.garmin_email, cfg.garmin_password, cfg.garmin_tokenstore, cfg.garmin_token_base64
        )
        self._last_activity_id: str | None = None

    def run_cycle(self, dry_run: bool = False) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        summary: dict[str, Any] = {
            "started_at": started.isoformat(),
            "listed": 0,
            "uploaded": 0,
            "duplicates": 0,
            "skipped": 0,
            "errors": 0,
            "mismatches": 0,
            "would_upload": 0,
            "dry_run": dry_run,
            "messages": [],
        }

        if not self.cfg.keiser_ready:
            summary["messages"].append("keiser credentials not configured")
            summary["finished_at"] = datetime.now(timezone.utc).isoformat()
            return summary

        keiser = KeiserClient(
            self.cfg.keiser_email, self.cfg.keiser_password, self.cfg.keiser_api_base
        )
        try:
            keiser.login()
            since = started - timedelta(days=self.cfg.lookback_days)
            datasets = keiser.list_datasets(since)
            summary["listed"] = len(datasets)

            for summary_ds in datasets:
                ride_id = summary_ds.get("id")
                if ride_id is None or self.store.is_synced(int(ride_id)):
                    continue
                try:
                    self._process_ride(keiser, int(ride_id), summary, dry_run=dry_run)
                except (KeiserError, GarminAuthError) as exc:
                    summary["errors"] += 1
                    summary["messages"].append(f"ride {ride_id}: {exc}")
                    self.store.record(int(ride_id), "error", error=str(exc))
                    # Auth errors won't recover this cycle -- stop early.
                    if isinstance(exc, GarminAuthError):
                        summary["messages"].append("stopping cycle: Garmin auth unavailable")
                        break
                except Exception as exc:  # noqa: BLE001
                    summary["errors"] += 1
                    summary["messages"].append(f"ride {ride_id}: unexpected {exc}")
                    self.store.record(int(ride_id), "error", error=str(exc))
        except KeiserError as exc:
            summary["messages"].append(f"keiser: {exc}")
        finally:
            keiser.close()

        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        summary["garmin_status"] = self.garmin.status
        return summary

    def _process_ride(
        self, keiser: KeiserClient, ride_id: int, summary: dict[str, Any], dry_run: bool = False
    ) -> None:
        ds = keiser.get_dataset(ride_id, self.cfg.graph_resolution)
        # Keiser reports duration in milliseconds; work in seconds everywhere.
        duration = float(ds.get("duration") or 0) / 1000.0
        started_at = ds.get("startedAt")
        ended_at = ds.get("endedAt")

        if duration < self.cfg.min_ride_seconds:
            if not dry_run:
                self.store.record(
                    ride_id, "skipped", started_at=started_at, ended_at=ended_at,
                    duration_seconds=duration, error="ride shorter than MIN_RIDE_SECONDS",
                )
            summary["skipped"] += 1
            return

        if dry_run:
            summary["would_upload"] += 1
            summary["messages"].append(
                f"ride {ride_id}: would upload ({started_at}, {duration/60:.1f} min, "
                f"{float(ds.get('distance') or 0)/1000:.1f} km)"
            )
            return

        machine = ds.get("machineType")
        device_name = f"Keiser {machine}" if machine else "Keiser M Series"
        tcx = build_tcx(ds, device_name=device_name)
        fname = suggested_filename(ds)
        tmp_path = os.path.join(tempfile.gettempdir(), fname)
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(tcx)
        try:
            result = self.garmin.upload(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if result["duplicate"]:
            self.store.record(
                ride_id, "duplicate", started_at=started_at, ended_at=ended_at,
                duration_seconds=duration, garmin_activity_id=result.get("activity_id"),
            )
            summary["duplicates"] += 1
            logger.info("ride %s already in Garmin (duplicate)", ride_id)
            return

        # Fresh upload -- optionally verify Garmin stored what we sent.
        activity_id = result.get("activity_id")
        validation_error = self._validate_upload(ds, started_at, summary)
        if activity_id is None and validation_error is None:
            # Grab the id from the matched activity if the upload response
            # didn't include one (HTTP 202 returns only an uploadId).
            activity_id = self._last_activity_id

        self.store.record(
            ride_id, "uploaded", started_at=started_at, ended_at=ended_at,
            duration_seconds=duration, garmin_activity_id=activity_id,
            error=validation_error,
        )
        summary["uploaded"] += 1
        if validation_error:
            summary["mismatches"] += 1
            summary["messages"].append(f"ride {ride_id}: {validation_error}")
            logger.warning("ride %s uploaded but validation FAILED: %s", ride_id, validation_error)
        else:
            logger.info("ride %s uploaded to Garmin (activity %s)", ride_id, activity_id)

    def _validate_upload(
        self, ds: dict[str, Any], started_at: Any, summary: dict[str, Any]
    ) -> str | None:
        """Return a mismatch description if the Garmin activity disagrees with
        the Keiser source, else ``None``. Never raises -- validation problems
        must not fail an otherwise-successful upload."""
        self._last_activity_id = None
        if not self.cfg.validate_uploads:
            return None
        try:
            activity = self.garmin.find_activity_by_start(str(started_at or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("validation lookup failed: %s", exc)
            return None
        if not activity:
            summary["messages"].append("validation: uploaded activity not found yet in Garmin")
            return None
        self._last_activity_id = str(
            activity.get("activityId") or activity.get("activityUuidId") or ""
        ) or None
        issues = compare(source_summary(ds), activity)
        if issues:
            return "validation mismatch: " + "; ".join(issues)
        return None
