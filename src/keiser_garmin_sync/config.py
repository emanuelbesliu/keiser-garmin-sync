"""Configuration for keiser-garmin-sync.

Config resolves from three layers, later ones overriding earlier ones:

    1. built-in defaults (this dataclass)
    2. an optional config file (TOML) and/or a ``.env`` file
    3. environment variables

This keeps two audiences happy:

* **Self-hosters / K8s** inject everything as environment variables (a Secret),
  exactly as before -- no file is required.
* **Local / on-demand users** get a friendly ``config.toml`` (or ``.env``) so
  they don't have to export a dozen variables by hand.

The config file may be flat or use ``[keiser]`` / ``[garmin]`` / ``[sync]``
sections; keys are matched case-insensitively to the environment-variable names
(with or without a leading section, e.g. ``keiser.email`` == ``KEISER_EMAIL``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

APP_NAME = "keiser-garmin-sync"


def default_data_dir() -> Path:
    """Per-user data directory (XDG on Linux, Application Support on macOS)."""
    override = os.environ.get("KGS_DATA_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def default_config_paths() -> list[Path]:
    """Ordered list of locations searched for a config file."""
    paths: list[Path] = []
    if os.environ.get("KGS_CONFIG"):
        paths.append(Path(os.environ["KGS_CONFIG"]).expanduser())
    xdg_cfg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_cfg).expanduser() if xdg_cfg else Path.home() / ".config"
    paths.append(base / APP_NAME / "config.toml")
    paths.append(Path.cwd() / f"{APP_NAME}.toml")
    paths.append(Path.cwd() / "config.toml")
    return paths


# Maps an environment-variable name to the dataclass field it fills. This is the
# single source of truth for both env and config-file resolution.
_ENV_TO_FIELD = {
    "KEISER_EMAIL": ("keiser_email", str),
    "KEISER_PASSWORD": ("keiser_password", str),
    "KEISER_API_BASE": ("keiser_api_base", str),
    "GARMIN_EMAIL": ("garmin_email", str),
    "GARMIN_PASSWORD": ("garmin_password", str),
    "GARMIN_TOKENSTORE": ("garmin_tokenstore", str),
    "GARMIN_TOKEN_BASE64": ("garmin_token_base64", str),
    "LOOKBACK_DAYS": ("lookback_days", int),
    "SYNC_INTERVAL_MINUTES": ("sync_interval_minutes", int),
    "MIN_RIDE_SECONDS": ("min_ride_seconds", int),
    "GRAPH_RESOLUTION": ("graph_resolution", int),
    "VALIDATE_UPLOADS": ("validate_uploads", bool),
    "DB_PATH": ("db_path", str),
    "ACTIVITY_NAME": ("activity_name", str),
    "GARMIN_ACTIVITY_TYPE": ("garmin_activity_type", str),
    "HTTP_PORT": ("http_port", int),
    "LOG_LEVEL": ("log_level", str),
}


def _coerce(value: Any, typ: type) -> Any:
    if typ is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in ("0", "false", "no", "off", "")
    if typ is int:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None
    return str(value).strip()


def load_dotenv(path: Path) -> None:
    """Populate os.environ from a simple KEY=VALUE ``.env`` file.

    Existing environment variables are never overwritten (env wins over file).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _read_config_file(path: Path) -> dict[str, Any]:
    """Read a TOML config file into a flat ``{ENV_NAME: value}`` mapping.

    A ``[section] key`` maps to the environment name ``SECTION_KEY`` when that
    exists, otherwise to the bare ``KEY`` (so ``[sync] lookback_days`` resolves
    to ``LOOKBACK_DAYS`` while ``[keiser] email`` resolves to ``KEISER_EMAIL``).
    """
    if tomllib is None or not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return {}

    flat: dict[str, Any] = {}

    def absorb(prefix: str, mapping: dict[str, Any]) -> None:
        for key, val in mapping.items():
            if isinstance(val, dict):
                absorb(f"{prefix}{key}_", val)
                continue
            full = f"{prefix}{key}".upper().replace("-", "_")
            bare = str(key).upper().replace("-", "_")
            if full in _ENV_TO_FIELD:
                flat[full] = val
            elif bare in _ENV_TO_FIELD:
                flat[bare] = val

    absorb("", data)
    return flat


@dataclass
class Config:
    # --- Keiser Metrics cloud ---
    keiser_email: str = ""
    keiser_password: str = ""
    keiser_api_base: str = "https://metrics-api.keiser.com/api"

    # --- Garmin Connect ---
    garmin_email: str = ""
    garmin_password: str = ""
    garmin_tokenstore: str = ""
    garmin_token_base64: str = ""

    # --- sync behaviour ---
    lookback_days: int = 30
    sync_interval_minutes: int = 30
    min_ride_seconds: int = 60
    graph_resolution: int = 3000
    validate_uploads: bool = True

    # --- runtime ---
    db_path: str = ""
    activity_name: str = "Keiser Indoor Cycling"
    # Garmin activity type applied after upload. Keiser M Series are stationary
    # bikes, so the sensible default is "indoor_cycling" (Garmin uploads TCX
    # "Biking" as outdoor "cycling", which we correct via the API). Set to
    # "cycling" to keep Garmin's default, or any key in garmin.GARMIN_CYCLING_TYPES.
    garmin_activity_type: str = "indoor_cycling"
    http_port: int = 8096
    log_level: str = "INFO"

    @classmethod
    def load(
        cls,
        config_path: str | os.PathLike[str] | None = None,
        use_files: bool = True,
    ) -> Config:
        """Resolve config from defaults -> file/.env -> environment."""
        cfg = cls()
        valid_fields = {f.name for f in fields(cls)}

        file_values: dict[str, Any] = {}
        if use_files:
            # .env first so its values are visible as env vars below.
            for env_path in (
                [Path(os.environ["KGS_ENV"]).expanduser()]
                if os.environ.get("KGS_ENV")
                else []
            ) + [Path.cwd() / ".env"]:
                load_dotenv(env_path)

            search = (
                [Path(config_path).expanduser()] if config_path else default_config_paths()
            )
            for path in search:
                values = _read_config_file(path)
                if values:
                    file_values = values
                    break

        # Layer 2: config file
        for env_name, raw in file_values.items():
            mapped = _ENV_TO_FIELD.get(env_name.upper())
            if not mapped:
                continue
            field_name, typ = mapped
            coerced = _coerce(raw, typ)
            if coerced is not None and field_name in valid_fields:
                setattr(cfg, field_name, coerced)

        # Layer 3: environment variables (highest priority)
        for env_name, (field_name, typ) in _ENV_TO_FIELD.items():
            if env_name in os.environ:
                coerced = _coerce(os.environ[env_name], typ)
                if coerced is not None:
                    setattr(cfg, field_name, coerced)

        cfg._resolve_paths()
        return cfg

    def _resolve_paths(self) -> None:
        data_dir = default_data_dir()
        if not self.db_path:
            self.db_path = str(data_dir / "keiser-garmin.db")
        if not self.garmin_tokenstore:
            self.garmin_tokenstore = str(data_dir / "garmin-tokens")
        self.keiser_api_base = self.keiser_api_base.rstrip("/")

    @property
    def keiser_ready(self) -> bool:
        return bool(self.keiser_email and self.keiser_password)

    @property
    def garmin_ready(self) -> bool:
        return bool(
            (self.garmin_email and self.garmin_password) or self.garmin_token_base64
        )
