# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Changed

- Require affirmative user intent for host activation; negated, quoted, documentation, configuration, inspection, and review-only mentions no longer activate APR.
- Keep live leases exclusive and allow expired cross-session ownership reconciliation only through the new session's trusted affirmative APR `UserPromptSubmit`; ordinary `Stop` and caller-supplied evidence cannot release or transfer ownership.

## [0.4.0] - 2026-07-15

### Added

- Deterministic APR-local runtime gates for project/worktree mutation safety, compact handoffs, and Luna xhigh bootstrap packets.
- Stateless singleton guardian policy with bounded state-only input and silent unchanged/terminal polling.
- Automatic per-repository Matt Pocock setup detection, official setup-skill invocation, and completion revalidation before planning or mutation.

### Changed

- Treat 64k as an emergency root ceiling and require safe continuation before a second root compaction.
- Require project association, physical cwd, and managed worktree identity together; Git common-dir equality alone is insufficient.

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
