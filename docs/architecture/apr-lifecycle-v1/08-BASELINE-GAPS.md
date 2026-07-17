# Baseline gaps

Status: audited baseline for planning; revalidate fingerprints before implementation.

## Already present in APR v0.4.0 protocol

- route by goal/map/spec/ticket readiness;
- one Wayfinder ticket per fresh task;
- one implementation Issue per fresh task and isolated managed worktree;
- bounded empty-context worker delegation;
- checkpoint plus successor acknowledgement protocol;
- no mechanical first-compaction handoff;
- revision-bound evidence reuse and invalidation;
- recovery-only guardian, KAIROS gate, merge gate, and final cross-ticket audit.

These statements describe protocol coverage. They do not prove the Codex host
performs task creation, acknowledgement, archival, or durable reconciliation.

## Verified implementation gaps

1. **Host task creation path is blocked.** The observed namespaced create action
   canonicalized to `codex_appcreate_thread`, while the installed PreToolUse
   canonicalizer lacked that exact alias and rejected it as an unknown mutation.
   Retrying the unchanged action is deterministic waste.
2. **No proven end-to-end successor execution.** Runtime reporting marked
   automatic successor creation and trusted acknowledgement as host-unavailable.
3. **No completed-worker task archive lifecycle.** The current APR text and
   installed runtime do not prove archive eligibility, archive request/result,
   `archive_pending`, or readback.
4. **No complete durable cross-object registry.** There is no proven atomic map of
   Issue ↔ thread ↔ worktree ↔ branch ↔ lease ↔ PR ↔ tested/merged SHA ↔ lifecycle
   and archive state across restart.
5. **Compact global operational contract regressed.** Shortening always-injected
   instructions reduced context size, but also removed the explicit requirement
   for the root to execute a validated host-action request once, await the bound
   acknowledgement, and retain the predecessor until transfer completes.
6. **Host worktree compatibility is unresolved.** The exact path created or reused
   by the native worktree task action has not been proven compatible with APR's
   managed-worktree path rule.
7. **Cancellation and canonical revision drift are not end-to-end proven.** The
   protocol contains safety concepts, but task scheduling, in-flight reconciliation,
   and affected-closure replanning need reducer and integration evidence.

## Context-efficiency gap statement

Reducing always-injected text was useful, but lowering the auto-compaction
threshold does not solve repeated sampling of a large root. The remaining fix is
structural: bounded controller state, fresh task packets without inherited root
history, revision-keyed evidence reuse, fewer micro-turns, semantic controller
handoffs, and durable recovery outside the transcript.

The default threshold should remain model/host dependent unless a controlled
measurement supports a temporary override. Performance attribution to Luna,
Sol, Fast mode, or compaction is `UNCONFIRMED` without equivalent-work telemetry.

## Definition of gap closure

Each gap closes only when:

- a repository test first reproduces the failure or missing contract;
- the smallest implementation change passes targeted and full validation;
- host-bound behavior has a disposable live smoke or is explicitly marked
  `UNVERIFIED` and unattended mode remains disabled;
- installed artifact hashes match the tested repository revision;
- the acceptance row in `06-VERIFICATION.md` has durable evidence.

Do not rename a gap “complete” because prose was added or a unit test mocked away
the host boundary.
