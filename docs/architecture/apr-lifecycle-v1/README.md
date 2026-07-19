# APR Lifecycle v1 — design index

Status: approved target design; Phase 0 portable contracts implemented, live host verification pending.

This directory is the source of truth for the next Autonomous Project Run (APR)
lifecycle improvement. It turns a requirements document into independently
verified GitHub work while keeping controller contexts fresh, avoiding repeated
large-context sampling, and minimizing user supervision.

## Reading order

1. [00-CHARTER.md](00-CHARTER.md) — purpose, final outcome, boundaries, and principles.
2. [01-REQUIREMENTS.md](01-REQUIREMENTS.md) — normative functional and quality requirements.
3. [02-ARCHITECTURE.md](02-ARCHITECTURE.md) — components, ownership, data flow, and context policy.
4. [03-STATE-MACHINES.md](03-STATE-MACHINES.md) — project, ticket, controller, worker, and archive states.
5. [04-PROTOCOLS.md](04-PROTOCOLS.md) — durable registry, task creation, handoff, result, and archive contracts.
6. [05-DELIVERY-PLAN.md](05-DELIVERY-PLAN.md) — safe implementation order and ticket candidates.
7. [06-VERIFICATION.md](06-VERIFICATION.md) — acceptance matrix, replay tests, and completion rules.
8. [07-MIGRATION-ROLLBACK.md](07-MIGRATION-ROLLBACK.md) — staged rollout, compatibility, and rollback.
9. [08-BASELINE-GAPS.md](08-BASELINE-GAPS.md) — verified current gaps and the evidence required to close them.
10. [09-PHASE-0-BASELINE.md](09-PHASE-0-BASELINE.md) — frozen baseline, capability matrix, portable implementation, and remaining live evidence.

## Normative language

`MUST`, `MUST NOT`, `SHOULD`, and `MAY` are normative. A requirement is not
complete merely because the APR skill says it should happen: the responsible
runtime or host integration must enforce it and verification must produce the
evidence named in `06-VERIFICATION.md`.

## Implementation scope

The public skill now includes portable host-action validation, a versioned
lifecycle reducer and external atomic registry store, and archive lifecycle
transitions. It does not modify hooks, global Codex configuration, installed
skills, GitHub state, or user tasks. Host-side enforcement and disposable live
create/read/archive verification remain `UNVERIFIED` until trusted owner
evidence and those capabilities are available.
