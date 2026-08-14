"""Interactive Garmin login / token seeding (the ``login`` subcommand).

Garmin accounts commonly have MFA, which a headless container cannot satisfy.
Run this ONCE interactively to complete the SSO (and MFA) flow, after which a
refreshable token is persisted and the service authenticates headlessly forever.

Two outputs, both useful:

* the token is written to the local **token store** directory, so an on-demand
  ``sync`` / ``serve`` on the same machine just works; and
* the equivalent **base64** blob is printed, to paste into a secret manager
  (Infisical / K8s Secret) as ``GARMIN_TOKEN_BASE64`` for hosted deployments.

Tip: Garmin rate-limits by source IP. If you hit HTTP 429, retry later from a
different network (e.g. a phone hotspot); a shared corporate/VPN egress is often
already flagged.
"""
from __future__ import annotations

import getpass
import os
import sys

from .config import Config


def interactive_login(
    tokenstore: str | None = None,
    email: str | None = None,
    print_base64: bool = True,
) -> int:
    try:
        import garth
        from garth.exc import GarthHTTPError
    except ModuleNotFoundError as exc:  # pragma: no cover
        print(f"garth is required for login: {exc}", file=sys.stderr)
        return 1

    if tokenstore is None:
        tokenstore = Config.load().garmin_tokenstore

    email = email or os.environ.get("GARMIN_EMAIL", "").strip() or input(
        "Garmin Connect email: "
    ).strip()
    password = os.environ.get("GARMIN_PASSWORD", "").strip() or getpass.getpass(
        "Garmin Connect password: "
    )

    try:
        garth.login(email, password, prompt_mfa=lambda: input("MFA code: ").strip())
    except GarthHTTPError as exc:
        msg = str(exc)
        if "429" in msg or "Too Many" in msg:
            print(
                "\nGarmin returned HTTP 429 (rate limited by source IP).\n"
                "  - Wait ~30-60 minutes before retrying (longer if hit repeatedly).\n"
                "  - Retry from a DIFFERENT network (e.g. phone hotspot) -- the limit\n"
                "    is per-IP and a shared corporate/VPN egress may already be flagged.\n"
                "  - Do NOT retry in a tight loop; each attempt extends the cooldown.\n",
                file=sys.stderr,
            )
        else:
            print(f"\nGarmin login failed: {exc}\n", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"\nlogin failed: {exc}\n", file=sys.stderr)
        return 1

    # Persist the token store locally for on-demand / serve on this machine.
    try:
        os.makedirs(tokenstore, exist_ok=True)
        garth.client.dump(tokenstore)
        print(f"Token store written to: {tokenstore}")
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not write token store ({exc})", file=sys.stderr)

    if print_base64:
        token_b64 = garth.client.dumps()
        if token_b64:
            print("\n===== GARMIN_TOKEN_BASE64 (store in your secret manager) =====\n")
            print(token_b64)
            print("\n==============================================================\n")

    try:
        print(f"Logged in as: {garth.client.profile.get('fullName') or email}")
    except Exception:  # noqa: BLE001
        print(f"Logged in as: {email}")
    return 0
