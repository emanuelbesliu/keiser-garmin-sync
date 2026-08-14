"""One sync cycle: Keiser cloud -> TCX -> Garmin Connect, with dedup."""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Config
from .garmin import GarminAuthError, GarminUploader, activity_type_key
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

        # Fresh upload -- resolve the created activity once, then (optionally)
        # reclassify its type and verify it stored what we sent.
        activity = self._find_activity(started_at)
        activity_id = result.get("activity_id") or self._activity_id_of(activity)

        self._apply_activity_type(activity_id, activity, summary)

        validation_error = self._validate(ds, activity, summary)

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

    @staticmethod
    def _activity_id_of(activity: dict[str, Any] | None) -> str | None:
        if not activity:
            return None
        return str(activity.get("activityId") or activity.get("activityUuidId") or "") or None

    def _find_activity(
        self, started_at: Any, count: int = 10, attempts: int = 6
    ) -> dict[str, Any] | None:
        """Locate the just-uploaded Garmin activity by start time (best effort).

        Called once per upload; the result feeds both type reclassification and
        validation so we don't poll Garmin twice. The backfill passes a larger
        ``count`` and ``attempts=1`` to reach older activities in one shot.
        """
        if not (self.cfg.validate_uploads or self.cfg.garmin_activity_type):
            return None
        try:
            return self.garmin.find_activity_by_start(
                str(started_at or ""), attempts=attempts, count=count
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("activity lookup failed: %s", exc)
            return None

    def _apply_activity_type(
        self, activity_id: str | None, activity: dict[str, Any] | None, summary: dict[str, Any]
    ) -> None:
        """Reclassify the uploaded activity to the configured type if needed.

        Never raises -- a type-setting failure must not fail the upload."""
        target = self.cfg.garmin_activity_type
        if not target or not activity_id:
            return
        current = activity_type_key(activity or {})
        if current and current == target:
            return
        try:
            self.garmin.set_activity_type(activity_id, target)
        except Exception as exc:  # noqa: BLE001
            summary["messages"].append(f"activity {activity_id}: could not set type: {exc}")
            logger.warning("could not set activity %s type to %s: %s", activity_id, target, exc)

    def _validate(
        self, ds: dict[str, Any], activity: dict[str, Any] | None, summary: dict[str, Any]
    ) -> str | None:
        """Return a mismatch description if Garmin disagrees with Keiser, else
        ``None``. Never raises -- validation must not fail a good upload."""
        if not self.cfg.validate_uploads:
            return None
        if not activity:
            summary["messages"].append("validation: uploaded activity not found yet in Garmin")
            return None
        issues = compare(source_summary(ds), activity)
        if issues:
            return "validation mismatch: " + "; ".join(issues)
        return None

    def retype_synced(
        self, target: str | None = None, dry_run: bool = False, limit: int = 500
    ) -> dict[str, Any]:
        """Backfill: reclassify already-synced Garmin rides to the target type.

        Iterates rides this tool has uploaded (from the dedup store), resolving
        the Garmin activity id by start time when it wasn't captured earlier,
        and sets any whose current type differs from ``target``. Only touches
        activities we know we created -- other Garmin activities are untouched.
        """
        target = target or self.cfg.garmin_activity_type
        out: dict[str, Any] = {
            "target": target, "dry_run": dry_run, "checked": 0, "updated": 0,
            "already": 0, "unresolved": 0, "errors": 0, "messages": [],
        }
        for row in self.store.synced_rides(limit):
            out["checked"] += 1
            keiser_id = row.get("keiser_id")
            activity_id = row.get("garmin_activity_id")
            activity: dict[str, Any] | None = None
            if not activity_id:
                activity = self._find_activity(row.get("started_at"), count=100, attempts=1)
                activity_id = self._activity_id_of(activity)
                if activity_id and not dry_run:
                    self.store.set_garmin_activity_id(int(keiser_id), activity_id)
            if not activity_id:
                out["unresolved"] += 1
                out["messages"].append(f"ride {keiser_id}: no Garmin activity found")
                continue
            current = activity_type_key(activity or {}) or self.garmin.get_activity_type_key(
                activity_id
            )
            if current == target:
                out["already"] += 1
                continue
            if dry_run:
                out["updated"] += 1
                out["messages"].append(
                    f"activity {activity_id}: {current or '?'} -> {target} (dry-run)"
                )
                continue
            try:
                self.garmin.set_activity_type(activity_id, target)
                out["updated"] += 1
                out["messages"].append(f"activity {activity_id}: {current or '?'} -> {target}")
            except Exception as exc:  # noqa: BLE001
                out["errors"] += 1
                out["messages"].append(f"activity {activity_id}: {exc}")
        out["garmin_status"] = self.garmin.status
        return out
