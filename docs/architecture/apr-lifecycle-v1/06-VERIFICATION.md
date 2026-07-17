# Verification and traceability

## Completion rule

“Long-duration problems are completely resolved” is accepted only for the scoped
failure classes below. It does not promise every project will be fast. Legitimate
builds, tests, reviews, network waits, and difficult reasoning can remain long.
The claim means APR no longer adds the identified avoidable repetition,
task-lifecycle gaps, or context amplification.

## Acceptance matrix

| Verification | Requirements | Evidence |
|---|---|---|
| Start-state router skips completed stages | APR-001–APR-007 | fixture matrix + tracker readback |
| Planning units survive restart without duplicate task/artifact/Issue | APR-008–APR-009 | duplicate/reorder/restart replay + tracker readback |
| Each planning unit gets one fresh project task and no unnecessary worktree | APR-004–APR-006 | host action log + task metadata |
| Dependency frontier defaults to sequential | APR-010–APR-011 | scheduler reducer tests |
| One Issue→one branch/worktree/owner | APR-012–APR-017 | duplicate/race/restart replay |
| All external actions dedupe and reconcile unknown outcome | APR-018, APR-026, APR-051 | adapter fixtures + duplicate/reorder/fault replay |
| Exact create/archive identities are recognized but unmatched mutations fail | APR-016, APR-040–APR-043, APR-050–APR-051 | PreToolUse payload fixtures + negative tests |
| Worker implementation/review/full validation is revision-bound | APR-022 | synthetic change + targeted/full/review evidence |
| Bounded empty-context worker packet | APR-020–APR-021, APR-032, APR-053 | schema, size, and routing-ROI tests |
| Result is independently reconciled | APR-024–APR-025 | tampered/stale result negative tests |
| Evidence reuse invalidates only affected closure | APR-023, APR-030–APR-031, APR-052 | ledger/restart/fingerprint drift fixtures |
| No forced first-compaction handoff | APR-033–APR-034, APR-037 | telemetry policy tests |
| Exactly-one controller successor and ACK | APR-035–APR-036 | duplicate/timeout/restart replay |
| Orphan controller recovery is fenced or unattended mode blocks | APR-039, APR-055 | owner-loss/restart/capability-negative replay |
| Completed worker archived; failure becomes archive_pending | APR-040–APR-043, APR-049 | disposable live task smoke + fault injection |
| Cancellation creates no new work | APR-044 | in-flight/unknown-outcome replay |
| Spec drift fences old owners and replans closure | APR-045 | revision-change + stale-generation mutation denial |
| Guardian is read-only and silent on no change | APR-046 | policy fixtures + mutation denial |
| Circuit breaker stops deterministic repetition | APR-047 | three-failure replay |
| Fresh final verifier catches gaps and deduplicates restart | APR-048 | omitted Issue/PR/evidence + duplicate-create cases |
| Global injection remains compact and operational | APR-038 | byte/token baseline + behavior replay |
| Routine autonomy reduces user handling | APR-054 | end-to-end intervention count |
| Unsupported host capabilities fail closed | APR-055 | create/archive/recovery capability-negative suite |
| Ordinary Codex work does not activate APR | APR-056 | negative trigger suite |
| Runtime state cannot enter public snapshot | APR-057 | permission/redaction/publish-deny negative tests |

## End-to-end scenarios

1. Requirements document → Wayfinder → Spec Issue → implementation Issues → two
   sequential worktree workers → merged PRs → closed Issues → archived workers →
   fresh final verifier → visible final controller.
2. Existing approved spec skips Wayfinder and `to-spec`.
3. Worker stops after local commit; same-Issue successor resumes exact worktree.
4. Host returns unknown create result; reconciliation finds the existing task and
   does not create another.
5. Archive call fails; ticket remains complete and moves to `archive_pending`.
6. Controller handoff acknowledgement is delayed; no second successor is created.
7. Spec digest changes during execution; affected frontier stops and replans.
8. User cancels during PR checks; no new ticket starts and pending state is kept.
9. Runtime lacks create/archive capability; APR fails closed with one precise
   blocker and does not claim unattended operation.

## Performance and context evidence

Capture separately for controller, worker, guardian, and verifier:

- wall-clock time by phase;
- model sampling calls and input/cached-input tokens;
- compaction count and effectiveness;
- task/handoff count and packet/result byte size;
- repeated reads of the same revision/fingerprint/query;
- retries by transient/deterministic/unknown category;
- duplicate task or remote action count;
- manual user interventions.

Success requires zero duplicate mutation, no repeated broad scan by the same
actor/trust boundary for the same revision and query purpose without a fingerprint
change, bounded packets/results, and materially fewer repeated large
controller-context samples than the captured baseline. A model routing comparison
is secondary: compare equivalent work and verification, and do not attribute a
workflow defect to Luna/Sol without controlled evidence.

Independent controller reconciliation and a fresh final verifier are separate
trust boundaries and may intentionally inspect the same revision. Their required
checks are not counted as wasteful duplicate scans.

## Final review gates

- targeted and repository full suites pass on the exact final source fingerprint;
- public-surface validation finds no private paths, secrets, transcripts, or
  machine-local artifacts;
- an independent review finds no unresolved major safety, lifecycle, or UX issue;
- install/canary evidence matches repository revision and hashes;
- every requirement has a PASS or an explicit `UNVERIFIED` blocker; no silent N/A;
- documentation is updated to reflect actual behavior, not the intended design.
