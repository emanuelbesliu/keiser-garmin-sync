"""Tests for layered config resolution (defaults -> file/.env -> env)."""
from __future__ import annotations

from keiser_garmin_sync.config import Config


def test_env_overrides_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("KGS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KEISER_EMAIL", "a@b.com")
    monkeypatch.setenv("SYNC_INTERVAL_MINUTES", "5")
    monkeypatch.setenv("VALIDATE_UPLOADS", "false")
    cfg = Config.load(use_files=False)
    assert cfg.keiser_email == "a@b.com"
    assert cfg.sync_interval_minutes == 5
    assert cfg.validate_uploads is False


def test_config_file_read_and_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("KGS_DATA_DIR", str(tmp_path))
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "[keiser]\nemail='file@example.com'\npassword='filepw'\n"
        "[sync]\nlookback_days=7\n",
        encoding="utf-8",
    )
    # ensure no stray env
    monkeypatch.delenv("KEISER_EMAIL", raising=False)
    monkeypatch.delenv("LOOKBACK_DAYS", raising=False)
    cfg = Config.load(config_path=str(cfg_file))
    assert cfg.keiser_email == "file@example.com"
    assert cfg.lookback_days == 7

    # env must override the file
    monkeypatch.setenv("LOOKBACK_DAYS", "99")
    cfg2 = Config.load(config_path=str(cfg_file))
    assert cfg2.lookback_days == 99


def test_dotenv_does_not_override_real_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KGS_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("KEISER_EMAIL=fromdotenv@x.com\n", encoding="utf-8")
    monkeypatch.setenv("KEISER_EMAIL", "fromrealenv@x.com")
    cfg = Config.load()
    assert cfg.keiser_email == "fromrealenv@x.com"


def test_derived_paths_use_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("KGS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("GARMIN_TOKENSTORE", raising=False)
    cfg = Config.load(use_files=False)
    assert cfg.db_path == str(tmp_path / "keiser-garmin.db")
    assert cfg.garmin_tokenstore == str(tmp_path / "garmin-tokens")


def test_ready_properties(monkeypatch, tmp_path):
    monkeypatch.setenv("KGS_DATA_DIR", str(tmp_path))
    for k in ("KEISER_EMAIL", "KEISER_PASSWORD", "GARMIN_EMAIL", "GARMIN_PASSWORD", "GARMIN_TOKEN_BASE64"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.load(use_files=False)
    assert cfg.keiser_ready is False
    assert cfg.garmin_ready is False
    monkeypatch.setenv("KEISER_EMAIL", "e")
    monkeypatch.setenv("KEISER_PASSWORD", "p")
    monkeypatch.setenv("GARMIN_TOKEN_BASE64", "tok")
    cfg2 = Config.load(use_files=False)
    assert cfg2.keiser_ready is True
    assert cfg2.garmin_ready is True
