"""Keiser Metrics cloud API client.

Reproduces the relevant subset of the official TypeScript SDK
(github.com/KeiserCorp/Keiser.Metrics.SDK) as plain Python HTTP calls:

  * POST /auth/login                 {email, password, refreshable}
  * GET  /m-series/data-set/list     ?userId&from&sort&ascending
  * GET  /m-series/data-set          ?id&userId&graph   (includes graphData[])

Auth model (from the SDK's connection layer): the JWT is passed to every
authenticated call as an ``authorization`` parameter -- a query-string param on
GET requests and a body field on POST requests. There are no Axios interceptors
that move it into a header, so we replicate that exactly; we also send a
``Bearer`` header defensively in case the backend accepts it.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("keiser-garmin-sync.keiser")


class KeiserError(RuntimeError):
    pass


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verification (we only need the user id)."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # pad to a multiple of 4
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except (IndexError, binascii.Error, ValueError, json.JSONDecodeError) as exc:
        raise KeiserError(f"could not decode Keiser access token: {exc}") from exc


class KeiserClient:
    def __init__(self, email: str, password: str, api_base: str, timeout: float = 20.0):
        self._email = email
        self._password = password
        self._base = api_base.rstrip("/")
        self._client = httpx.Client(timeout=timeout, headers={"Accept": "application/json"})
        self._access_token: str | None = None
        self._user_id: int | None = None

    # ------------------------------------------------------------------ auth
    def login(self) -> int:
        """Authenticate with email/password. Returns the numeric user id."""
        url = f"{self._base}/auth/login"
        body = {"email": self._email, "password": self._password, "refreshable": True}
        try:
            resp = self._client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise KeiserError(f"Keiser login request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise KeiserError(
                f"Keiser login rejected (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        data = resp.json()
        token = data.get("accessToken")
        if not token:
            raise KeiserError(f"Keiser login returned no accessToken: {list(data)}")
        self._access_token = token
        # user id lives in the JWT payload ({"user": {"id": ...}}), and usually
        # also in the response body; prefer the body, fall back to the token.
        user = (data.get("user") or {}).get("id")
        if user is None:
            user = _decode_jwt_payload(token).get("user", {}).get("id")
        if user is None:
            raise KeiserError("could not determine Keiser user id from login response")
        self._user_id = int(user)
        logger.info("Keiser login OK (user id %s)", self._user_id)
        return self._user_id

    def _auth_params(self, extra: dict[str, Any]) -> dict[str, Any]:
        if not self._access_token:
            raise KeiserError("not authenticated -- call login() first")
        return {"authorization": self._access_token, **extra}

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        resp = self._client.get(url, params=self._auth_params(params), headers=headers)
        if resp.status_code >= 400:
            raise KeiserError(
                f"GET {path} failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        return resp.json()

    # --------------------------------------------------------------- queries
    @property
    def user_id(self) -> int | None:
        return self._user_id

    # The Keiser Metrics API caps the list ``limit`` at 100 (higher values are
    # rejected with a 400 "validation error in parameters: [limit]").
    PAGE_SIZE = 100

    def list_datasets(self, since: datetime, limit: int = 0) -> list[dict[str, Any]]:
        """List ride summaries started on/after ``since`` (ascending by start).

        Pages through the results in blocks of ``PAGE_SIZE`` (the server's max)
        so accounts with more than 100 rides in the window are fully covered.
        ``limit`` optionally caps the total number of summaries returned (0 =
        no cap; fetch everything in the window).
        """
        from_iso = since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        results: list[dict[str, Any]] = []
        offset = 0
        while True:
            params = {
                "userId": self._user_id,
                "from": from_iso,
                "sort": "startedAt",
                "ascending": "true",
                "limit": self.PAGE_SIZE,
                "offset": offset,
            }
            data = self._get("/m-series/data-set/list", params)
            page = data.get("mSeriesDataSets", []) or []
            results.extend(page)
            meta = data.get("mSeriesDataSetsMeta", {}) or {}
            total = meta.get("totalCount")
            offset += self.PAGE_SIZE
            if limit and len(results) >= limit:
                return results[:limit]
            if len(page) < self.PAGE_SIZE:
                break
            if isinstance(total, int) and offset >= total:
                break
        return results

    def get_dataset(self, dataset_id: int, graph_resolution: int) -> dict[str, Any]:
        """Fetch one ride including its ``graphData`` time-series points."""
        params = {"id": dataset_id, "userId": self._user_id, "graph": graph_resolution}
        data = self._get("/m-series/data-set", params)
        ds = data.get("mSeriesDataSet")
        if not ds:
            raise KeiserError(f"ride {dataset_id} not found in response")
        return ds

    def close(self) -> None:
        self._client.close()
