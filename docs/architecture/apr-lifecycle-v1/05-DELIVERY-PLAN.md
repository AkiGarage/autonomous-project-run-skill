# Delivery plan

## Safety and sequencing

Implement the control plane before relying on automation. Each phase starts with
RED contract tests, makes the smallest GREEN change, freezes the diff, and runs
targeted then full validation. Global installation follows repository-local tests
and uses backups plus a rollback command. Do not test archival on a user-owned
active task; create and archive a disposable test task.

## Phase 0 — baseline and compatibility inventory

- Freeze current APR skill/runtime/hook/host fingerprints and full test results.
- Record exact tool identities and payloads observed for create/read/archive.
- Decide and test how Codex host worktree placement satisfies the managed
  `<project-root>/.codex/worktrees/<id>` policy.
- Produce a capability matrix; unsupported actions remain fail-closed.

Exit: deterministic fixture captures and a clean baseline report.

## Phase 1 — host adapter bootstrap

- Add canonicalization tests for exact namespaced `create_thread` and
  `set_thread_archived` identities.
- Require every mutation to match a pending durable request and owner generation.
- Add read-after-write result persistence and unknown-outcome reconciliation.
- Restore a compact global operational contract that points to repository-local
  details without re-expanding always-injected context.

Exit: disposable planning task can be created, acknowledged, archived, and read
back; arbitrary unmatched mutation remains blocked.

## Phase 2 — registry and lifecycle reducer

- Define schemas and atomic transition/reducer code.
- Implement run, ticket, task, action, lease, archive, cancellation, and revision
  drift states.
- Add idempotency, fencing, attempt counters, circuit breaker, and migration tests.

Exit: replaying duplicate/reordered actions yields one correct logical state.

## Phase 3 — planning train

- Implement route detection and fresh task creation for Wayfinder, `to-spec`, and
  `to-tickets`.
- Persist structured results and publish/verify dependency-linked Issues.
- Ensure planning tasks do not create worktrees without code mutation.

Exit: goal-to-ready-Issue dry run completes without manual task copy/paste.

## Phase 4 — implementation train

- Select the ready frontier sequentially.
- Create one Issue-bound worktree task and validate first-entry snapshot.
- Integrate structured worker result, independent reconciliation, and same-Issue
  continuation.
- Add worker archive and separate worktree cleanup states.

Exit: a synthetic multi-Issue project completes Issue→PR→merge→close→archive with
no duplicate worker.

## Phase 5 — controller continuity

- Implement semantic/pressure-triggered controller checkpoint and exactly-one
  successor request.
- Validate hash-bound acknowledgement, predecessor archival, timeout recovery,
  and final-controller visibility.
- Confirm first compaction alone does not trigger handoff.

Exit: controller succession survives duplicate delivery and a simulated restart.

## Phase 6 — final audit and guarded rollout

- Add fresh final verifier, cross-ticket acceptance dossier, cancellation, spec
  drift, host outage, archive failure, and stale lease replays.
- Run local canary, then one supervised real repository run, then opt-in APR use.
- Compare wall time, root sampling, compactions, packet sizes, duplicate actions,
  and user interventions against the baseline.

Exit: every requirement in `06-VERIFICATION.md` is PASS with revision-bound
evidence. Only then may documentation call the lifecycle complete.

## Suggested ticket boundaries

1. Host action identity and safe request binding.
2. Registry schema and atomic lifecycle reducer.
3. Planning-task orchestration.
4. Issue/worktree worker orchestration.
5. Structured result and independent reconciliation.
6. Worker archive and cleanup separation.
7. Controller handoff/ACK/predecessor archive.
8. Cancellation and spec revision drift.
9. Final verifier and end-to-end replay/canary.
10. Compact global contract and installation/rollback documentation.

Dependencies should be encoded explicitly; tickets 3–8 depend on 1–2, and ticket
9 depends on all functional slices. Each ticket must be independently testable in
one fresh task.
