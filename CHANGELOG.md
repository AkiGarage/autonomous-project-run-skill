# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.3.0] - 2026-07-15

### Added

- Durable lifecycle state, fenced watchdog recovery, and safe task handoffs for long unattended runs.
- Canonical-spec and exact-source-state fingerprints for reusable reads, tests, and artifacts.
- Selective evidence invalidation, dependency/reverse-dependency closure checks, and full-reread fallbacks.
- Change-class gates and clean exact-commit final integration verification.

### Changed

- Clarified that routine repository mutations require an explicit end-to-end user request.
- Added read-after-write reconciliation for remote mutations and unknown outcomes.

## [0.2.0] - 2026-07-14

### Added

- Initial public-ready `autonomous-project-run` skill.
- English and Japanese documentation.
- Localized English and Japanese README hero images.
- MIT attribution and third-party notices.
- Public-surface validation script and publishing checklist.
- Explicit untrusted-project-content and companion-skill provenance boundaries.
- Atomic ticket-claim and pre-mutation ownership checks.
- GitHub Actions validation and regression coverage for local paths, secret-like values, and the project-content trust boundary.
- Separate project and upstream MIT notices, plus a safer vulnerability-reporting fallback.
- Clean-snapshot publishing when old GitHub pull refs retain rewritten history
  or unintended personal commit metadata.
