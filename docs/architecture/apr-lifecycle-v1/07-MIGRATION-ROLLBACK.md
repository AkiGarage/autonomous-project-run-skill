# Migration and rollback

## Migration policy

The rollout is additive and fail-closed. Existing v0.4.0 foreground behavior
remains available until the new lifecycle passes repository tests and a supervised
canary. Do not switch unattended scheduling on merely because schemas or prose
exist.

## Stages

1. **Document only:** add this design; no behavior change.
2. **Shadow:** emit/validate registry and host requests but do not execute new
   lifecycle mutations.
3. **Local disposable smoke:** create, acknowledge, and archive disposable tasks;
   never archive the active user task.
4. **Supervised canary:** one small synthetic/private project, sequential only.
5. **Opt-in APR:** enable for explicitly authorized runs with guardian recovery off
   unless its invariants are proven.
6. **Default APR path:** only after performance and failure-class acceptance passes.

## Compatibility and schema upgrades

- Every durable artifact has `schema_version` and a canonical hash.
- Readers reject unknown major versions and preserve the original artifact.
- Migrations are deterministic, reversible where possible, and tested from each
  supported prior version.
- Installed skill, runtime hook, host adapter, and repository validators publish a
  compatibility tuple; a mismatch fails closed before mutation.
- Existing active runs are not silently migrated mid-action. They reach a safe
  checkpoint, reconcile side effects, then migrate or finish on the old path.

## Backup requirements

Before changing machine-local hooks, global instructions, or installed skills:

- record path, mode, size, and SHA-256;
- create a timestamped backup outside active load paths;
- record the repository source revision and installation manifest;
- test the restore command without deleting the backup;
- keep secrets and private host state out of repository artifacts.

## Rollback triggers

Rollback or disable new scheduling on:

- duplicate task, successor, PR, merge, or Issue mutation;
- ownership/lease ambiguity;
- worktree/project binding mismatch;
- unbounded context/result payload or repeated broad scan regression;
- host alias/capability mismatch;
- lost durable registry transition or unreconciled unknown effect;
- archive of a non-eligible/current/final controller;
- material regression in ordinary non-APR tasks.

## Rollback behavior

1. atomically disable new lifecycle scheduling;
2. preserve registry, logs, and evidence;
3. reconcile in-flight/unknown external effects;
4. restore the prior known-good installed files from the manifest;
5. run the prior compatibility probe and focused regression suite;
6. continue only from the proven foreground owner or report the blocker;
7. never delete worktrees, branches, tasks, or evidence as part of automatic
   rollback.

## Remaining design decisions

Two integration decisions must be resolved by Phase 0 evidence, not assumption:

1. whether Codex `create_thread` can create/reuse the exact APR-managed worktree
   path required by current policy, or whether a validated host adapter contract
   must change that policy;
2. the authoritative location and atomic mechanism for the durable run registry
   across process restart. The location MUST be outside tracked/public source with
   restrictive access; Phase 0 must select the exact host-owned mechanism.

These are implementation blockers for unattended mode, not blockers for the
documentation or repository-local reducer/tests.
