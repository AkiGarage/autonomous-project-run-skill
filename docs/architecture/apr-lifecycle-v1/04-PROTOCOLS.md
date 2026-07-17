# Protocols

## Durable run registry

The registry is an atomic, versioned store. It contains no secrets or raw
transcripts. At minimum it maps:

```json
{
  "schema_version": 1,
  "run_id": "stable-id",
  "project": {"repo_id": "...", "spec_digest": "..."},
  "controller": {
    "thread_id": "...",
    "generation": 1,
    "state": "active",
    "handoff_request_id": null
  },
  "planning_stages": {
    "wayfinder/unit-id": {
      "request_id": "stable-id",
      "thread_id": "...",
      "input_digest": "...",
      "output_digest": "...",
      "publish_state": "readback_confirmed",
      "lifecycle": "reconciled"
    },
    "to-spec": {"lifecycle": "pending"},
    "to-tickets": {"lifecycle": "pending"}
  },
  "tickets": {
    "issue-node-id": {
      "issue_number": 42,
      "thread_id": "...",
      "worktree": "validated-path",
      "branch": "validated-branch",
      "lease_generation": 3,
      "base_sha": "...",
      "head_sha": "...",
      "tested_sha": "...",
      "pr_number": 51,
      "merged_sha": "...",
      "lifecycle": "reconciled",
      "archive_state": "eligible"
    }
  },
  "pending_actions": []
}
```

Production schemas MUST define required fields, length limits, enums, canonical
JSON hashing, atomic write semantics, lock/fencing, lease TTL/renewal, and upgrade
behavior. Paths and untrusted tracker text MUST be validated, never interpolated.

Runtime instances MUST be outside tracked/public source, use restrictive local
permissions, contain bounded/redacted data, and be rejected by publish validation
if copied into the repository.

Planning stages use the same request/result discipline as implementation: stable
unit identity, one task request, input/output digest, structured result,
publication readback, and controller reconciliation before the next stage.

## External action request

Every external mutation uses a two-record pattern:

1. controller/runtime emits a durable `external_action_request` with stable
   logical key/request ID, action kind, validated arguments, owner generation,
   expected repo/ref/SHA/tracker state, and payload hash;
2. the applicable host/git/GitHub adapter executes at most once logically, then
   stores a reconciled `external_action_result` with observed identifiers/state
   and timestamp.

This applies to task create/archive, push, PR create/update/ready/merge, Issue
create/update/close, and any other state-changing adapter action. Unknown outcomes
are looked up by logical key and expected/observed state before retry.

The hook/tool canonicalizer MUST recognize the exact Codex tool identities used
in production, including namespaced forms. Unknown mutation aliases fail closed.
Allowlisting an alias alone is insufficient: arguments must match the pending
request and current owner/state.

Required actions:

- `create_thread` for a project planning task;
- `create_thread` for an implementation worktree task;
- `create_thread` for a same-Issue successor bound to the existing worktree;
- `set_thread_archived` for a proven archive-eligible task;
- read/list operations used for reconciliation.

## Final verifier protocol

The final verifier is a distinct `task_kind: final_verifier` with a stable request
ID. Its bootstrap binds the exact clean commit, canonical map/spec digest, complete
ticket/change inventory, acceptance-to-evidence map, relevant negative evidence,
and explicitly withheld mutation authority. It returns a structured PASS/BLOCK
verdict with evidence references. The controller reconciles the verifier task,
commit, digest, and verdict before parent closure. Unknown create/result outcomes
deduplicate by request ID. Its eventual archive/disposition is recorded separately;
the final controller remains visible.

## Bootstrap packet

The packet is versioned, schema-limited, hash-bound, and contains only:

- run, phase, Issue/planning-unit, role, owner generation;
- authority and explicit forbidden actions;
- repository/project/worktree/branch/base/HEAD identity;
- canonical map/spec revision and digest;
- bounded objective, scope, acceptance, dependencies, and validation commands;
- relevant fingerprints and evidence references;
- pending/unknown side effects and required acknowledgement shape.

It does not contain the retired root transcript or broad logs.

## Successor acknowledgement

The successor independently verifies authority and state, then returns:

```json
{
  "schema_version": 1,
  "request_id": "...",
  "successor_thread_id": "...",
  "run_id": "...",
  "owner_generation": 4,
  "handoff_hash": "...",
  "observed_repo_id": "...",
  "observed_worktree": "...",
  "observed_head": "...",
  "decision": "acknowledged"
}
```

The predecessor remains the owner until the acknowledgement is validated and the
registry transition commits atomically.

## Worker result

The result includes status, complete change inventory, tested SHA, validation and
review evidence, negative evidence, PR/CI/Issue state, merged SHA if applicable,
pending/unknown effects, and residual `UNVERIFIED` boundaries. The controller
checks current sources rather than accepting the result as authority.

## Archive protocol

Before archive, the controller proves:

- ticket lifecycle is `reconciled` or controller handoff is `acknowledged`;
- evidence/result is durable and hash-addressable;
- no pending or unknown mutation depends on the task;
- the archive request matches the exact thread ID and owner generation.

On success, record readback. On failure or unknown outcome, set `archive_pending`
and reconcile before retry. Never archive the current/final controller as part of
worker cleanup.

## Ordinary Codex work

APR is not automatically activated for a normal one-shot task. Ordinary long work
uses the compact continuity ledger and the handoff skill only when remaining work
and context pressure justify a fresh task. It does not create GitHub Issues,
worktrees, guardians, or multi-ticket orchestration without the applicable user
request and authority.

## Orphan-controller recovery

A guardian may only record a bounded durable recovery request. A separate
host-trusted, singleton, fenced recovery owner may consume it after proving the
registered controller is absent, no valid successor/request already exists, and a
safe checkpoint is available. It creates at most one controller successor using
the ordinary request/ACK protocol. If the host cannot provide this owner and proof,
APR disables unattended recovery and reports the capability blocker.
