# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.5.0] - 2026-07-19

### Added

- Reliable continuation between separate coding tasks, without asking the user
  to reopen the project or repeat the original request when the host supports it.
- Checks that tie every task action to the intended project and Issue, helping
  prevent work from continuing in the wrong place.
- Safer completion and archive handling so a finished task does not leave stale
  state that blocks the next run.

### Changed

- Waiting caused by timeouts or temporary capacity limits now survives safely and
  resumes later without creating duplicate tasks or a background polling service.
- When a long conversation is shortened by the coding app, APR saves its place,
  reviews what remains, and does not stop solely because the conversation was long.
- Project and worktree checks now reject ambiguous or mismatched locations before
  any repository change.
- Only a clear request to run APR activates it. Documentation examples, questions,
  inspection requests, and negated requests do not start autonomous work.
- Interrupted handoffs and repeated completion signals are reconciled safely
  instead of repeating work or leaving stale ownership behind.
- Public-release validation now uses only the intended staged files, keeping local
  worktree metadata and ignored runtime state out of the published snapshot.

## [0.4.0] - 2026-07-15

### Added

- Stronger project and worktree checks before repository changes.
- A quiet recovery check that does not duplicate work or keep reporting unchanged
  state.
- Automatic per-repository Matt Pocock setup detection, official setup-skill invocation, and completion revalidation before planning or mutation.

### Changed

- Long runs save a verified continuation point before conversation limits become
  a problem.
- Work now continues only when the open project and isolated worktree both match
  the intended repository.

## [0.3.0] - 2026-07-15

### Added

- Saved progress and safe task handoff for long runs.
- Reuse of earlier reads and test results only while the relevant source and
  requirements remain unchanged.
- Targeted re-checking when related files or dependencies change.
- Final integration verification from the exact reviewed commit.

### Changed

- Routine repository changes now require a clear end-to-end request.
- Uncertain GitHub results are checked before retrying, reducing duplicate actions.

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
