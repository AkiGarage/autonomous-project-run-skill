# Charter

## Purpose

APR must take a specification or an imprecise project goal to a verified result
with as little user handling as safety permits. It must preserve a fresh working
context at meaningful boundaries without repeatedly sampling the same large root
history.

The primary problem is not compaction itself. The costly failure mode is a
long-lived root repeatedly sampling a large cached context across thousands of
small actions. The design therefore fixes work shape, ownership, evidence reuse,
and lifecycle automation. A low auto-compaction threshold is not the primary
remedy.

## Final outcome

Given an authorized GitHub-backed project, APR can:

1. route the current project state;
2. run Wayfinder, `to-spec`, and `to-tickets` only where needed;
3. create one fresh project task for each planning unit;
4. create one fresh isolated worktree task for each implementation Issue;
5. implement, test, review, open/repair/merge a PR, and close its Issue within
   the granted authority;
6. reconcile every worker result independently from GitHub, git, and evidence;
7. archive completed worker tasks after durable completion is proven;
8. hand the controller to exactly one fresh successor at a safe phase or context
   boundary, then archive the predecessor after acknowledgement;
9. run a fresh final verifier and leave the final controller visible with a
   concise completion report.

The user should not need to manually create tasks, copy routine handoffs, track
worktrees, poll workers, or archive completed worker tasks.

## Non-goals

- Changing model weights or claiming a prompt can reproduce another model.
- Treating a context threshold as a universal quality boundary.
- Literal distributed exactly-once delivery. APR provides logically at-most-one
  successor through durable request identity, deduplication, and reconciliation.
- Giving workers authority to spawn successors, merge unrelated work, or broaden
  project scope.
- Using a guardian as the normal scheduler. The guardian is recovery-only.
- Automatically parallelizing every ready Issue. Sequential execution is the
  safe default; bounded parallelism is an optional later policy.
- Deleting tasks or worktrees merely to make the interface tidy.

## Design principles

- Durable state beats transcript memory.
- One Issue maps to one branch/worktree and one active worker owner.
- A fresh successor for the same Issue reuses that Issue, branch, and worktree.
- Planning tasks use fresh project tasks but no worktree unless they mutate code.
- Thread archival and worktree cleanup are separate state transitions.
- Prose defines protocol; host/runtime controls enforce mutation boundaries.
- Handoff occurs at recoverable semantic boundaries, not automatically at the
  first compaction.
- Evidence is keyed by revision/fingerprint and reused until invalidated.
- Every side effect is reconciled before retry when the outcome is unknown.
- The global always-injected contract stays compact; detailed behavior lives in
  this repository and is loaded only when APR is selected.

## Authority boundary

APR lifecycle invocation alone does not silently grant commit, push, PR, merge,
Issue closure, publication, production mutation, credentials, spending, or
destructive cleanup. It uses the authority explicitly granted for the run and
stops only at a material authority boundary. Routine local and task-lifecycle
steps within that grant should not create avoidable user chores.
