# keiser-garmin-sync

Sync your **Keiser M Series** indoor cycling rides to **Garmin Connect** —
cloud-to-cloud, with **no Bluetooth hardware** and **no third-party bridge
service**. Run it on demand from your laptop, or self-host it so every ride
shows up in Garmin automatically.

[![CI](https://github.com/emanuelbesliu/keiser-garmin-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/emanuelbesliu/keiser-garmin-sync/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

---

## How it works

```
Keiser Metrics cloud                         Garmin Connect
(metrics-api.keiser.com)                            ▲
   │  1. login (email/password)                     │  4. upload activity (.tcx)
   │  2. list new rides in a look-back window       │     via python-garminconnect / garth
   │  3. fetch ride + per-second graph data         │
   ▼                                                │
        keiser-garmin-sync ── build TCX (power / cadence / speed / distance / calories) ──┘
   • SQLite dedup store: each ride is uploaded exactly once
   • post-upload validator: re-reads the Garmin activity and confirms the
     duration / distance / calories match the Keiser source
```

Rides are converted to **TCX** (Garmin renders cycling power via the
`TPX/Watts` extension) rather than binary FIT, for reliability. Heart rate is
**not** included — the Keiser ride dataset doesn't contain it.

---

## Quick start

Pick the mode that fits you. All three share the same config and token.

### 1. On demand (laptop / cron) — `pipx`

```bash
pipx install keiser-garmin-sync          # or: pip install keiser-garmin-sync

keiser-garmin-sync login                 # one-time Garmin login (handles MFA)
export KEISER_EMAIL=you@example.com
export KEISER_PASSWORD=...                # see "Keiser Google-SSO" below
keiser-garmin-sync sync                   # upload any new rides, then exit
```

Useful flags: `sync --dry-run` (show what would upload, change nothing),
`sync --since 7` (look back 7 days), `sync --loop` (keep running).

### 2. Portable — Docker

```bash
docker run --rm -v keiser-garmin:/data \
  -e KEISER_EMAIL=you@example.com -e KEISER_PASSWORD=... \
  -e GARMIN_TOKEN_BASE64="$(cat token.b64)" \
  ghcr.io/emanuelbesliu/keiser-garmin-sync:latest sync
```

### 3. Self-hosted service — Docker Compose

```bash
cp .env.example .env      # fill in credentials + GARMIN_TOKEN_BASE64
docker compose up -d
curl -s localhost:8096/status | jq
```

The service polls on an interval, exposes `/health`, `/status`, and `POST
/sync`, and persists everything in the `/data` volume. Kubernetes manifests are
in [`deploy/k8s/`](deploy/k8s/).

---

## Garmin login (MFA-safe, one time)

Garmin accounts with MFA can't log in headlessly, so authenticate **once**
interactively and reuse the resulting refreshable token forever:

```bash
keiser-garmin-sync login
```

This writes a token to the local token store **and** prints a
`GARMIN_TOKEN_BASE64` blob. For hosted/containerised deploys, paste that blob
into your secret manager (or `.env`) as `GARMIN_TOKEN_BASE64`; the service loads
it, persists a refreshable token on its volume, and never needs the code again.

> **Rate limits:** Garmin throttles logins by source IP. If you get HTTP 429,
> wait and retry from a different network (a phone hotspot works well) — a
> shared corporate/VPN egress is often already flagged.

---

## Keiser Google-SSO accounts

If you sign into the M Series app with **Google**, your Keiser account has no
password and the Metrics API can't replay Google OAuth. Add a password to the
same account (SSO keeps working):

> metrics.keiser.com → **Forgot password** → set a password → use it as
> `KEISER_PASSWORD`.

---

## Configuration

Config resolves from three layers, each overriding the previous:

1. built-in defaults
2. a config file (`config.toml`) and/or a `.env` file
3. environment variables (highest priority)

See [`config.example.toml`](config.example.toml) and
[`.env.example`](.env.example).

| Variable | Default | Notes |
|---|---|---|
| `KEISER_EMAIL` / `KEISER_PASSWORD` | — | Keiser Metrics account |
| `GARMIN_TOKEN_BASE64` | — | Seeded token from `login` (MFA-safe, preferred) |
| `GARMIN_EMAIL` / `GARMIN_PASSWORD` | — | Fallback login (fails under MFA) |
| `LOOKBACK_DAYS` | `30` | How far back each sync scans |
| `SYNC_INTERVAL_MINUTES` | `30` | Poll cadence in `serve` / `--loop` |
| `MIN_RIDE_SECONDS` | `60` | Ignore accidental micro-rides |
| `GRAPH_RESOLUTION` | `3000` | Data points per ride requested from Keiser |
| `VALIDATE_UPLOADS` | `true` | Re-read the Garmin activity and cross-check totals |
| `DB_PATH` | *(data dir)* | SQLite dedup store |
| `GARMIN_TOKENSTORE` | *(data dir)* | Persisted Garmin token directory |
| `HTTP_PORT` | `8096` | `serve` port |

The default data directory is `~/.local/share/keiser-garmin-sync` (override with
`KGS_DATA_DIR`); containers use `/data`.

---

## CLI reference

| Command | Purpose |
|---|---|
| `login` | Interactive Garmin auth; writes the token store + prints `GARMIN_TOKEN_BASE64` |
| `sync` | Upload new Keiser rides once (`--loop`, `--dry-run`, `--since N`, `--json`) |
| `upload FILE` | Upload a single exported `.tcx`/`.fit` (with validation) |
| `status` | Show sync history / totals (`--json`) |
| `serve` | Run the HTTP service (hosted mode; `--host`, `--port`) |

### "My ride is in the app but didn't sync"

The M Series app's History merges **local** (on-phone) and **cloud** rides, and
this tool reads only Keiser's cloud API. If a ride never finished uploading from
the bike/phone to Keiser, it won't be visible here. Open the app while online to
let it upload, or export the ride as a TCX and push it directly:

```bash
keiser-garmin-sync upload "2026-08-13 06.29.21 PM.tcx"
```

---

## Development

```bash
git clone https://github.com/emanuelbesliu/keiser-garmin-sync
cd keiser-garmin-sync
python -m venv .venv && source .venv/bin/activate
pip install -e ".[server,dev]"
pytest
ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Security & privacy

Your Keiser and Garmin credentials (and the Garmin token, which is **as
sensitive as your password**) stay entirely within your own environment — this
tool talks only to Keiser and Garmin. Never commit `.env`, `config.toml`, the
token store, or a `GARMIN_TOKEN_BASE64` value. See [SECURITY.md](SECURITY.md).

## Disclaimer

Not affiliated with, endorsed by, or supported by Keiser or Garmin. "Keiser",
"M Series", and "Garmin Connect" are trademarks of their respective owners. Use
at your own risk, in accordance with each provider's terms of service.

## License

[MIT](LICENSE)
