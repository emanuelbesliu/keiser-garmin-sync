# Contributing

Thanks for your interest in improving **keiser-garmin-sync**! This is a small,
focused project — contributions of all sizes are welcome.

## Getting started

```bash
git clone https://github.com/emanuelbesliu/keiser-garmin-sync
cd keiser-garmin-sync
python -m venv .venv && source .venv/bin/activate
pip install -e ".[server,dev]"
pytest
ruff check .
```

## Ground rules

- **Keep it dependency-light.** The core (`sync`/`upload`/CLI) must stay usable
  with just `httpx`, `garminconnect`, and `garth`. The web server lives behind
  the optional `[server]` extra.
- **Add a test for behaviour changes.** The TCX conversion and validator are the
  parts most likely to regress — see `tests/`.
- **Run `ruff check .` and `pytest`** before opening a PR; CI runs both.
- **Never commit secrets** — no real credentials, tokens, `.env`, `config.toml`,
  or token stores. `.gitignore` covers the common cases.

## Commit style

Conventional commits are appreciated but not required:
`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.

## Reporting bugs / requesting features

Use the GitHub issue templates. For sync problems, include:

- the CLI command / mode you used,
- `keiser-garmin-sync status --json` output (redact anything sensitive),
- relevant log lines with `--log-level DEBUG`.

Please **do not** paste real tokens, passwords, or `GARMIN_TOKEN_BASE64` values.

## Scope

This tool intentionally covers **Keiser M Series → Garmin Connect** cycling
rides. Other machines/vendors or bi-directional sync are out of scope for now,
but a well-argued issue is welcome.
