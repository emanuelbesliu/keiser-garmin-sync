# Security Policy

## Handling of credentials

`keiser-garmin-sync` talks **only** to Keiser Metrics and Garmin Connect. Your
credentials never leave your environment and are never sent to any third party.

Treat these as highly sensitive and **never commit them**:

- `KEISER_EMAIL` / `KEISER_PASSWORD`
- `GARMIN_EMAIL` / `GARMIN_PASSWORD`
- **`GARMIN_TOKEN_BASE64` and the token store directory** — a Garmin token is a
  long-lived, refreshable credential that is effectively **as powerful as your
  password**. Anyone with it can act as you on Garmin Connect. Store it in a
  secret manager, restrict file permissions, and rotate it (re-run
  `keiser-garmin-sync login`) if it may have been exposed.

The provided `.gitignore` and `.dockerignore` exclude `.env`, `config.toml`,
`*.db`, and `garmin-tokens/`. Keep it that way.

## Reporting a vulnerability

If you discover a security issue, please **do not open a public issue**.
Instead, report it privately via GitHub Security Advisories
("Report a vulnerability" on the repository's **Security** tab). Include:

- a description and impact,
- reproduction steps,
- affected versions.

We aim to acknowledge reports within a few days and to coordinate a fix and
disclosure timeline with you.

## Supported versions

This is a community project without formal LTS; fixes are made against the
latest `main` and the most recent release.
