"""Command-line interface -- the "on-demand" frontend.

    keiser-garmin-sync login          # one-time interactive Garmin auth
    keiser-garmin-sync sync           # sync new rides once, now
    keiser-garmin-sync sync --loop    # keep syncing on an interval
    keiser-garmin-sync sync --dry-run # show what would upload, change nothing
    keiser-garmin-sync upload ride.tcx  # push a single exported ride
    keiser-garmin-sync status         # show what has been synced
    keiser-garmin-sync serve          # run the HTTP service (hosted mode)

All commands share the same layered config (defaults -> file/.env -> env).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import xml.etree.ElementTree as ET

from . import __version__
from .config import Config


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _load_cfg(args: argparse.Namespace) -> Config:
    cfg = Config.load(config_path=getattr(args, "config", None))
    if getattr(args, "since", None) is not None:
        cfg.lookback_days = args.since
    if getattr(args, "log_level", None):
        cfg.log_level = args.log_level
    return cfg


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_login(args: argparse.Namespace) -> int:
    from .seed import interactive_login

    cfg = _load_cfg(args)
    return interactive_login(
        tokenstore=args.tokenstore or cfg.garmin_tokenstore,
        email=args.email,
        print_base64=not args.no_base64,
    )


def cmd_sync(args: argparse.Namespace) -> int:
    from .store import Store
    from .syncer import Syncer

    cfg = _load_cfg(args)
    _configure_logging(cfg.log_level)

    if not cfg.keiser_ready:
        print(
            "Keiser credentials missing. Set KEISER_EMAIL / KEISER_PASSWORD "
            "(env, .env, or config file).",
            file=sys.stderr,
        )
        return 2
    if not cfg.garmin_ready and not args.dry_run:
        print(
            "Garmin not configured. Run 'keiser-garmin-sync login' first, or set "
            "GARMIN_TOKEN_BASE64.",
            file=sys.stderr,
        )
        return 2

    store = Store(cfg.db_path)
    syncer = Syncer(cfg, store)

    def one() -> dict:
        result = syncer.run_cycle(dry_run=args.dry_run)
        _print_cycle(result, as_json=args.json)
        return result

    if not args.loop:
        result = one()
        return 1 if result.get("errors") else 0

    interval = max(60, cfg.sync_interval_minutes * 60)
    print(f"Looping every {interval}s. Ctrl-C to stop.", file=sys.stderr)
    try:
        while True:
            one()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("stopped.", file=sys.stderr)
        return 0


def cmd_upload(args: argparse.Namespace) -> int:
    from .garmin import GarminAuthError, GarminUploader

    cfg = _load_cfg(args)
    _configure_logging(cfg.log_level)

    up = GarminUploader(
        cfg.garmin_email, cfg.garmin_password, cfg.garmin_tokenstore, cfg.garmin_token_base64
    )
    try:
        result = up.upload(args.file)
    except GarminAuthError as exc:
        print(f"Garmin auth failed: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"file not found: {args.file}", file=sys.stderr)
        return 2

    if result.get("duplicate"):
        print(f"duplicate: Garmin already has this activity ({args.file})")
        return 0
    print(f"uploaded: {args.file} (activity_id={result.get('activity_id')})")

    if not args.no_validate:
        started = args.start or _tcx_start_time(args.file)
        if started:
            act = up.find_activity_by_start(started)
            if act:
                print(
                    "  Garmin recorded: "
                    f"{(act.get('duration') or 0) / 60:.1f} min, "
                    f"{(act.get('distance') or 0) / 1000:.2f} km, "
                    f"{act.get('calories')} cal, "
                    f"type={(act.get('activityType') or {}).get('typeKey')}"
                )
                src = _tcx_summary(args.file)
                if src:
                    from .validate import compare

                    issues = compare(src, act)
                    if issues:
                        print("  VALIDATION MISMATCH:")
                        for i in issues:
                            print(f"    - {i}")
                        return 1
                    print("  validation OK")
            else:
                print("  (activity not visible yet; Garmin ingests asynchronously)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from .store import Store

    cfg = _load_cfg(args)
    store = Store(cfg.db_path)
    counts = store.counts()
    recent = store.recent(args.limit)

    if args.json:
        print(json.dumps({"totals": counts, "mismatches": store.mismatch_count(),
                          "recent": recent}, indent=2, default=str))
        return 0

    print(f"keiser configured : {cfg.keiser_ready}")
    print(f"garmin configured : {cfg.garmin_ready}")
    print(f"database          : {cfg.db_path}")
    print(
        "totals            : "
        f"{counts.get('total', 0)} rides "
        f"({counts.get('uploaded', 0)} uploaded, "
        f"{counts.get('duplicate', 0)} duplicate, "
        f"{counts.get('skipped', 0)} skipped, "
        f"{counts.get('error', 0)} error, "
        f"{store.mismatch_count()} mismatch)"
    )
    if recent:
        print("\nrecent:")
        for r in recent:
            mins = (r.get("duration_seconds") or 0) / 60
            flag = " !MISMATCH" if r.get("status") == "uploaded" and r.get("error") else ""
            print(
                f"  {str(r.get('started_at') or '')[:19]:20} "
                f"{r.get('status',''):10} {mins:6.1f} min  "
                f"garmin={r.get('garmin_activity_id') or '-'}{flag}"
            )
    return 0


def cmd_retype(args: argparse.Namespace) -> int:
    from .store import Store
    from .syncer import Syncer

    cfg = _load_cfg(args)
    _configure_logging(cfg.log_level)

    if not cfg.garmin_ready:
        print(
            "Garmin not configured. Run 'keiser-garmin-sync login' first, or set "
            "GARMIN_TOKEN_BASE64.",
            file=sys.stderr,
        )
        return 2

    store = Store(cfg.db_path)
    syncer = Syncer(cfg, store)
    result = syncer.retype_synced(
        target=args.activity_type, dry_run=args.dry_run, limit=args.limit
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        verb = "would change" if args.dry_run else "changed"
        print(
            f"retype -> {result['target']}: checked {result['checked']}, "
            f"{verb} {result['updated']}, already {result['already']}, "
            f"unresolved {result['unresolved']}, errors {result['errors']}"
        )
        for m in result.get("messages", []):
            print(f"  {m}")
    return 1 if result.get("errors") else 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import run_server

    cfg = _load_cfg(args)
    _configure_logging(cfg.log_level)
    run_server(cfg, host=args.host, port=args.port)
    return 0


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _print_cycle(result: dict, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return
    if result.get("dry_run"):
        print(
            f"[dry-run] listed={result.get('listed')} "
            f"would_upload={result.get('would_upload')} "
            f"skipped={result.get('skipped')}"
        )
    else:
        print(
            f"listed={result.get('listed')} uploaded={result.get('uploaded')} "
            f"duplicates={result.get('duplicates')} skipped={result.get('skipped')} "
            f"errors={result.get('errors')} mismatches={result.get('mismatches')}"
        )
    for msg in result.get("messages", []):
        print(f"  - {msg}")


_TCX_NS = {"t": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}


def _tcx_start_time(path: str) -> str | None:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    node = root.find(".//t:Activity/t:Id", _TCX_NS)
    return node.text if node is not None else None


def _tcx_summary(path: str) -> dict[str, float] | None:
    """Parse a TCX's lap totals into the validator's source shape."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None

    def _f(tag: str) -> float:
        total = 0.0
        found = False
        for node in root.findall(f".//t:Lap/t:{tag}", _TCX_NS):
            try:
                total += float(node.text or 0)
                found = True
            except (TypeError, ValueError):
                pass
        return total if found else 0.0

    # validator uses duration in seconds and distance in metres; the TCX already
    # stores those, so multiply duration back to ms to match source_summary().
    return {
        "duration_s": _f("TotalTimeSeconds"),
        "distance_m": _f("DistanceMeters"),
        "calories": _f("Calories"),
    }


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="keiser-garmin-sync",
        description="Sync Keiser M Series indoor cycling rides to Garmin Connect.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--config", help="path to a config file (TOML)")
    p.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("login", help="interactive Garmin login / token seed")
    sp.add_argument("--email", help="Garmin email (else prompt / GARMIN_EMAIL)")
    sp.add_argument("--tokenstore", help="directory to write the token store")
    sp.add_argument("--no-base64", action="store_true", help="don't print the base64 token")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("sync", help="sync new Keiser rides to Garmin")
    sp.add_argument("--loop", action="store_true", help="keep running on an interval")
    sp.add_argument("--dry-run", action="store_true", help="show what would upload; change nothing")
    sp.add_argument("--since", type=int, help="look back N days (overrides config)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("upload", help="upload a single TCX/FIT file to Garmin")
    sp.add_argument("file", help="path to the .tcx / .fit file")
    sp.add_argument("--start", help="ride start time for validation lookup (ISO)")
    sp.add_argument("--no-validate", action="store_true", help="skip post-upload validation")
    sp.set_defaults(func=cmd_upload)

    sp = sub.add_parser("status", help="show sync history / totals")
    sp.add_argument("--limit", type=int, default=10, help="recent rows to show")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser(
        "retype",
        help="reclassify already-synced Garmin rides (e.g. cycling -> indoor_cycling)",
    )
    sp.add_argument(
        "--type", dest="activity_type", default=None,
        help="target Garmin activity type (default: GARMIN_ACTIVITY_TYPE / indoor_cycling)",
    )
    sp.add_argument("--dry-run", action="store_true", help="show changes; change nothing")
    sp.add_argument("--limit", type=int, default=500, help="max synced rides to scan")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_retype)

    sp = sub.add_parser("serve", help="run the HTTP service (hosted mode)")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=None)
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
