# Phase 0 baseline and portable lifecycle contracts

Status: portable implementation complete; live host smoke remains `UNVERIFIED`.

## Frozen source and design

- Source revision: `235ccc44d1c6bf94acf5a4896fa3eb39a35e4690`.
- Requirements digest: `1ecfe59bd3d5bce0d41e5e5e9fb2a8d69bf22edeaf34dd6c2233fcc3666c9c8d`.
- Architecture digest: `3189a031742fdbd902ad8e25a932902a8013e8d922d91ecf0646351ccdcfe196`.
- Protocol digest: `6780ffac2baca839a1bf6617dadd85a66f2d18f93249443d4faa3b6f20c01a09`.
- Verification digest: `a17ff94fb1db05cb769e342926264b3e79417202cab85a89ea217017ea2e8743`.
- Baseline `./scripts/validate.sh`: PASS, 67 unit tests before this implementation.
- Baseline public-surface suite: PASS from the primary checkout. Its copy-based
  fixture was incompatible with a linked worktree because it copied the `.git`
  indirection and nested managed worktrees; the suite now exports only the
  staged index into an independent temporary repository.

## Host capability matrix

| Capability | Exact identity or policy | Evidence | Status |
|---|---|---|---|
| Create task | `codex_app__create_thread` | versioned synthetic declaration fixture | fixture-only |
| Read task | `codex_app__read_thread` | versioned synthetic declaration fixture | fixture-only |
| List tasks | `codex_app__list_threads` | versioned synthetic declaration fixture | fixture-only |
| Archive task | `codex_app__set_thread_archived` | versioned synthetic declaration fixture | fixture-only |
| Managed worktree | `<project-root>/.codex/worktrees/<id>` | physical Git worktree and common-dir check | proven locally |
| Trusted owner generation | host-supplied owner evidence | runtime probe returned `missing_owner_evidence` | unavailable |
| GitHub tracker state | authenticated remote readback | authentication unavailable during baseline | unverified |

Bare aliases and guessed host identities are not accepted. The collapsed forms
observed at the trusted `PreToolUse` canonicalization boundary are separately
allowlisted as exact fixtures; this is not a general alias rule.

## Portable implementation

- `host_actions.py` validates bounded, versioned create/archive requests and
  results. It binds stable request and logical keys, owner generation, expected
  state, exact tool identity, payload hash, and readback. Raw prompts are not
  accepted or persisted.
- `lifecycle_registry.py` provides a deterministic reducer for ticket,
  external-action, and archive events. Duplicate replay is idempotent,
  conflicting event IDs and stale generations fail closed, and unknown external
  effects remain pending. Nested ticket/planning records, duplicate ticket
  identities, and pending-action references are validated fail closed.
- `RegistryStore` derives a deterministic path from the host-supplied external
  state root plus repository/run identity. It rejects identity mismatches and
  requires a restrictive parent directory, `0600` files, symlink rejection,
  locking, compare-and-swap, same-directory atomic replacement, and `fsync`.
- Archive eligibility requires a reconciled ticket, durable evidence, no pending
  or unknown effects, a non-controller exact thread, and a matching registered
  archive action. The domain transition derives its outcome from that action's
  exact-thread host readback; caller-supplied success fields cannot forge it.
  Failure, unknown outcome, or missing readback becomes `archive_pending`.
  Archival never changes `cleanup_state`.
- The public validator builds its fixture from the staged index, so runtime
  registry files, continuity notes, `.git` indirection, and nested managed
  worktrees cannot be copied into a release candidate.

This covers the portable portions of APR-016–APR-018, APR-040–APR-043,
APR-049–APR-053, APR-055, and APR-057. It does not claim the complete lifecycle
verification matrix.

## Remaining live evidence

A disposable create → read → terminal → archive → readback smoke was not run.
The trusted host owner generation was unavailable, and the user request did not
authorize creating a real background task solely for a smoke test. Until a host
adapter supplies trusted authority and that live smoke passes, unattended task
creation and archive automation must remain disabled. The foreground controller
may continue only within independently proven authority.

## Verification

- Targeted host-action and lifecycle-registry tests: PASS, 22 tests.
- `./scripts/validate.sh`: PASS, 89 tests in the final staged-index suite.
- `./tests/validate-public-surface.sh`: PASS from an isolated staged-index export
  in the linked worktree, including its negative mutation fixtures.
- Split-chunk supervisor regression stress: PASS, 200/200 iterations after the
  test writer handshake made thread-start failures observable.
- Publish staging snapshot: not retained or published. The public-surface gate
  validates an isolated temporary export of the final staged index.
