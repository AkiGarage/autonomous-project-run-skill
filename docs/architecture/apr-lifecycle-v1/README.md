# APR Lifecycle v1 — design index

Status: approved target design; implementation not yet complete.

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

## Normative language

`MUST`, `MUST NOT`, `SHOULD`, and `MAY` are normative. A requirement is not
complete merely because the APR skill says it should happen: the responsible
runtime or host integration must enforce it and verification must produce the
evidence named in `06-VERIFICATION.md`.

## Implementation status

Version `0.4.0` implements the Phase 0 local runtime gates, setup preflight, and
guardian policy described by this design. The remaining lifecycle architecture
is still a target design and is not complete until the verification matrix is
satisfied. Repository code does not itself modify global Codex configuration,
installed skills, GitHub state, or user tasks.
