# Requirements

## A. Intake and planning

- **APR-001** The controller MUST fingerprint authority, repository, remote,
  tracker, branch/HEAD, dirty state, and relevant toolchain before routing work.
- **APR-002** It MUST resume at the earliest incomplete gate and MUST NOT rerun
  Wayfinder, specification, or ticket generation already proven complete.
- **APR-003** A loose goal MUST route through Wayfinder; a clear goal without a
  specification through `to-spec`; an approved specification without execution
  tickets through `to-tickets`.
- **APR-004** Each Wayfinder ticket MUST run in a fresh project task.
- **APR-005** `to-spec` and `to-tickets` MUST each run in a fresh project task.
- **APR-006** Planning tasks SHOULD NOT create worktrees unless they will mutate
  repository content.
- **APR-007** Published implementation Issues MUST have observable acceptance,
  dependency edges, validation, rollback/recovery expectations, and a canonical
  parent specification revision/digest.
- **APR-008** Every planning stage/unit MUST have durable request, task, input/output
  digest, result, publish/readback, and reconciliation state so restart cannot
  duplicate maps, specifications, Issues, or tasks.
- **APR-009** A planning result MUST be reconciled against the canonical tracker
  and artifact digest before the next planning stage starts.

## B. Scheduling and isolation

- **APR-010** The controller MUST schedule only the ready dependency frontier.
- **APR-011** Sequential execution MUST be the default.
- **APR-012** One implementation Issue MUST map to one branch, one managed
  worktree, and at most one active owner lease.
- **APR-013** The first worker for an Issue MUST start in a fresh worktree task.
- **APR-014** An intra-Issue successor MUST be a fresh task but MUST reuse the
  same Issue, branch, and worktree after ownership acknowledgement.
- **APR-015** A worker MUST NOT spawn its successor, change project scope, or
  claim another Issue.
- **APR-016** Task creation MUST be bound to a versioned durable request ID and
  deduplicated before and after the host action.
- **APR-017** The managed worktree policy and the Codex host's actual worktree
  placement MUST be reconciled and tested; a path mismatch MUST fail closed.
- **APR-018** Every git/GitHub/task mutation MUST use a stable logical action key,
  expected pre-state, durable request/result, and read-after-write reconciliation.

## C. Worker execution and evidence

- **APR-020** A worker MUST receive a bounded structured bootstrap packet rather
  than the retired controller transcript.
- **APR-021** The packet MUST bind project/repo/worktree, Issue, owner generation,
  requirements digest, base/HEAD, scope, acceptance, relevant fingerprints, and
  authority.
- **APR-022** A worker MUST use focused implementation, targeted tests, required
  full validation, diff/secret scan, and configured review gates.
- **APR-023** Reusable evidence MUST be keyed by path + source-state fingerprint +
  query and invalidated only across affected dependency/reverse-dependency scope.
- **APR-024** The worker MUST return a versioned structured result containing
  tested SHA, PR/CI/review/Issue state, evidence references, negative evidence,
  residual uncertainty, and pending side effects.
- **APR-025** The controller MUST independently reconcile the result against
  current git/GitHub and evidence before advancing lifecycle state.
- **APR-026** Push, PR creation/update, merge, Issue mutation, and task lifecycle
  actions MUST reconcile unknown outcomes before retry and MUST NOT duplicate a
  logically completed action.

## D. Controller continuity and context efficiency

- **APR-030** The controller MUST keep a bounded durable ledger with goal,
  constraints, decisions, Done/Now/Next, registry pointer, open questions,
  fingerprints, and pending/unknown side effects.
- **APR-031** It MUST NOT reread unchanged broad evidence or repeatedly pass a
  large root history to delegated work.
- **APR-032** Bounded delegation SHOULD use empty-context packets only when packet
  and verification cost is lower than expected root-context savings.
- **APR-033** Controller handoff MAY occur only at a natural phase boundary,
  ineffective compaction, or unrecoverable context pressure, at a safe checkpoint.
- **APR-034** First compaction alone MUST NOT force a handoff.
- **APR-035** A controller handoff MUST create exactly one fresh successor,
  receive a hash-bound acknowledgement, and archive the predecessor only after
  reconciliation.
- **APR-036** The final controller MUST remain visible unless the user later asks
  to archive it.
- **APR-037** Auto-compaction SHOULD use the model/host default unless a measured,
  reversible experiment justifies an override. A temporary override MUST NOT be
  presented as the root fix for repeated large-context sampling.
- **APR-038** Always-injected global context MUST contain only the compact trigger,
  safety boundary, and pointer to detailed APR instructions.
- **APR-039** If the active controller disappears, only a host-trusted fenced
  recovery owner MAY consume a durable recovery request and create one controller
  successor after proving no valid owner exists. Without that capability,
  unattended recovery MUST fail closed.

## E. Completion, archival, and recovery

- **APR-040** A worker task is archive-eligible only after the controller proves
  durable terminal lifecycle, reconciles remote/local state, and preserves its
  result and evidence references.
- **APR-041** Archive failure MUST transition to `archive_pending`; it MUST NOT
  reopen or duplicate completed implementation work.
- **APR-042** Thread archival MUST NOT imply branch/worktree deletion.
- **APR-043** Worktree cleanup MUST be a separately authorized, verified action.
- **APR-044** Cancellation MUST stop new scheduling, reconcile in-flight and
  unknown effects, preserve evidence, and enter a durable terminal state.
- **APR-045** A canonical map/spec revision change MUST invalidate affected
  tickets/evidence, stop stale scheduling, and replan the affected closure.
- **APR-046** A recovery guardian MUST be read-only, singleton, bounded, silent on
  no change, and incapable of normal successor creation.
- **APR-047** After three failures with the same deterministic root cause, APR MUST
  circuit-break and report the evidence-backed blocker.
- **APR-048** A fresh final verifier MUST check all tickets, merged PRs, closed
  Issues, canonical acceptance, exact clean commit, and residual `UNVERIFIED`
  boundaries before parent closure.
- **APR-049** Implementation completion and archive completion MUST be orthogonal.
  `archive_pending` MUST NOT reopen implementation; final project completion MAY
  preserve a bounded archive backlog only when evidence and retry ownership are
  durable and the user-visible report states it.

## F. Quality attributes

- **APR-050 Safety:** mutation requires current authority, identity, ownership,
  and freshness evidence.
- **APR-051 Idempotency:** every external action has a stable key and read-after-
  write reconciliation.
- **APR-052 Observability:** lifecycle decisions are reconstructible without a
  transcript.
- **APR-053 Bounded context:** routine packets, results, registry records, and
  guardian input have schemas and size limits.
- **APR-054 Low supervision:** routine scheduling, acknowledgement, reconciliation,
  and archive handling do not require copy/paste by the user.
- **APR-055 Compatibility:** unsupported host capabilities fail closed and produce
  a precise blocker rather than claiming automation works.
- **APR-056 Ordinary-task isolation:** non-APR work MUST NOT create APR Issues,
  worktrees, guardians, or lifecycle tasks merely because it is long-running.
- **APR-057 Registry privacy:** runtime registry/state MUST remain outside tracked
  and publishable source, use restrictive local access, redact user/private data,
  and be denied by release validation if accidentally introduced.
