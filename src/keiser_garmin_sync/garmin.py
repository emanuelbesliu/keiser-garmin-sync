"""Garmin Connect upload wrapper around python-garminconnect / garth.

Authentication strategy (headless-friendly):
  1. Resume from a persisted garth token directory on the PVC (no MFA needed).
  2. If empty, seed from ``GARMIN_TOKEN_BASE64`` (produced once locally with
     seed_garmin_token.py, then stored in Infisical) and persist it.
  3. Fall back to email/password -- which will fail if the Garmin account has
     MFA enabled, in which case we surface ``mfa_required`` and keep the ride
     queued (the dedup store leaves it un-synced for the next cycle).

garth tokens are long-lived and self-refresh, so step 1 covers steady state.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from garminconnect import Garmin
from garth.exc import GarthHTTPError

logger = logging.getLogger("keiser-garmin-sync.garmin")


def _parse_garmin_time(value: Any) -> datetime | None:
    """Parse a Garmin ``startTimeGMT`` ("2026-08-13 15:29:21") as UTC."""
    if not value:
        return None
    text = str(value).strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class GarminAuthError(RuntimeError):
    pass


class GarminUploader:
    def __init__(self, email: str, password: str, tokenstore: str, token_base64: str = ""):
        self._email = email
        self._password = password
        self._tokenstore = tokenstore
        self._token_base64 = token_base64
        self._client: Garmin | None = None
        self.status: str = "uninitialized"

    def _tokenstore_has_tokens(self) -> bool:
        try:
            return os.path.isdir(self._tokenstore) and any(
                f.endswith(".json") for f in os.listdir(self._tokenstore)
            )
        except OSError:
            return False

    def ensure_login(self) -> Garmin:
        if self._client is not None:
            return self._client

        os.makedirs(self._tokenstore, exist_ok=True)

        # 1) resume from persisted token dir
        if self._tokenstore_has_tokens():
            try:
                g = Garmin()
                g.login(self._tokenstore)
                self._client = g
                self.status = "ok"
                logger.info("Garmin auth resumed from token store")
                return g
            except Exception as exc:  # noqa: BLE001 - garth raises broad types
                logger.warning("Garmin token-store resume failed: %s", exc)

        # 2) seed from base64 token, then persist
        if self._token_base64:
            try:
                g = Garmin()
                g.login(self._token_base64)
                g.garth.dump(self._tokenstore)
                self._client = g
                self.status = "ok"
                logger.info("Garmin auth seeded from GARMIN_TOKEN_BASE64")
                return g
            except Exception as exc:  # noqa: BLE001
                logger.warning("Garmin base64 token seed failed: %s", exc)

        # 3) full email/password login (fails under MFA)
        if self._email and self._password:
            try:
                g = Garmin(self._email, self._password, return_on_mfa=True)
                result = g.login()
                if isinstance(result, tuple) and result and result[0] == "needs_mfa":
                    self.status = "mfa_required"
                    raise GarminAuthError(
                        "Garmin account requires MFA -- seed a token via "
                        "GARMIN_TOKEN_BASE64 (see README)."
                    )
                g.garth.dump(self._tokenstore)
                self._client = g
                self.status = "ok"
                logger.info("Garmin auth via email/password OK")
                return g
            except GarminAuthError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.status = "login_failed"
                raise GarminAuthError(f"Garmin login failed: {exc}") from exc

        self.status = "no_credentials"
        raise GarminAuthError("no Garmin token or credentials available")

    def upload(self, tcx_path: str) -> dict[str, Any]:
        """Upload a TCX/FIT file. Returns {activity_id, duplicate, raw}.

        Garmin responds with HTTP 409 for an activity it already has. The
        underlying garth client raises ``GarthHTTPError`` on any non-2xx status
        (rather than returning the response), so a 409 must be caught here and
        reported as a duplicate instead of bubbling up as an error.
        """
        g = self.ensure_login()
        try:
            resp = g.upload_activity(tcx_path)
        except GarthHTTPError as exc:
            status = getattr(getattr(exc.error, "response", None), "status_code", None)
            if status == 409:
                return {"activity_id": None, "duplicate": True, "status_code": 409, "raw": {}}
            raise

        status_code = getattr(resp, "status_code", 200)
        try:
            body = resp.json() if hasattr(resp, "json") else resp
        except Exception:  # noqa: BLE001
            body = {}

        detail = (body or {}).get("detailedImportResult", {}) if isinstance(body, dict) else {}
        successes = detail.get("successes") or []
        failures = detail.get("failures") or []

        activity_id: str | None = None
        if successes:
            activity_id = str(successes[0].get("internalId") or successes[0].get("id") or "")

        # Garmin returns HTTP 409 for a duplicate activity.
        duplicate = status_code == 409 or any(
            "duplicate" in str(m).lower()
            for f in failures
            for m in (f.get("messages") or [])
        )
        return {
            "activity_id": activity_id,
            "duplicate": duplicate,
            "status_code": status_code,
            "raw": body,
        }

    def find_activity_by_start(
        self,
        started_at: str,
        match_window_seconds: int = 180,
        attempts: int = 6,
        delay_seconds: float = 5.0,
    ) -> dict[str, Any] | None:
        """Locate the Garmin activity that corresponds to a Keiser start time.

        Garmin ingests uploads asynchronously (HTTP 202), so the activity may
        take a few seconds to appear. We poll ``get_activities`` and return the
        closest cycling activity whose start time is within
        ``match_window_seconds`` of ``started_at`` (Keiser's ``startedAt``).
        Returns ``None`` if nothing matches within the retry budget.
        """
        target = _parse_garmin_time(started_at)
        if target is None:
            return None
        g = self.ensure_login()
        for attempt in range(attempts):
            try:
                activities = g.get_activities(0, 10)
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not list Garmin activities: %s", exc)
                activities = []
            best: dict[str, Any] | None = None
            best_delta = match_window_seconds + 1
            for a in activities or []:
                a_time = _parse_garmin_time(a.get("startTimeGMT"))
                if a_time is None:
                    continue
                delta = abs((a_time - target).total_seconds())
                if delta <= match_window_seconds and delta < best_delta:
                    best, best_delta = a, delta
            if best is not None:
                return best
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
        return None
