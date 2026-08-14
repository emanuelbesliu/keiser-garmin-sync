# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-08-14

First public release.

### Added
- **CLI** (`keiser-garmin-sync`) with `login`, `sync`, `upload`, `status`, and
  `serve` subcommands — usable both on demand and as a hosted service.
- **Layered configuration**: defaults → config file (TOML) / `.env` →
  environment variables.
- **Post-upload validator**: re-reads the uploaded Garmin activity and confirms
  its duration, distance, and calories match the Keiser source (catches mangled
  uploads instead of silently shipping bad data).
- **`sync --dry-run`** to preview what would upload without changing anything.
- **`upload FILE`** to push a single exported `.tcx`/`.fit` (with validation),
  for rides that never reached the Keiser cloud.
- Packaging for `pipx`/`pip`, a Docker image, `docker-compose.yml`, and generic
  Kubernetes manifests.

### Fixed
- Keiser `dataset.duration` is **milliseconds**; it was being written to TCX as
  seconds, producing wildly inflated activity durations (e.g. a 14 s ride shown
  as ~233 min). Now converted to seconds everywhere, including the
  `MIN_RIDE_SECONDS` filter.
- TCX `DistanceMeters` is now cumulative/monotonic (Keiser's per-point distance
  is a per-sample delta) and scaled so the final trackpoint equals the ride
  total.
