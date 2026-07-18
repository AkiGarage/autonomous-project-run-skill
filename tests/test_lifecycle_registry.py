from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/autonomous-project-run/scripts"
sys.path.insert(0, str(SCRIPTS))
import lifecycle_registry as registry  # noqa: E402
import host_actions  # noqa: E402


def event(event_id: str, sequence: int, kind: str, **payload: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "event_id": event_id,
        "sequence": sequence,
        "kind": kind,
        "owner_generation": 1,
        "payload": payload,
    }
    value["event_hash"] = registry.digest_record(value)
    return value


def create_request(request_id: str = "create-1") -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "request_id": request_id,
        "logical_key": "worker-for-issue-1",
        "action_kind": "create_thread",
        "tool_identity": "codex_app__create_thread",
        "owner_generation": 1,
        "expected_state": {"ticket": "planned"},
        "arguments": {
            "task_kind": "implementation",
            "prompt_digest": "f" * 64,
            "target": {
                "type": "project",
                "project_id": "project-1",
                "environment": {
                    "type": "worktree",
                    "starting_state": {"type": "branch", "branch_name": "codex/issue-1"},
                },
            },
        },
    }
    value["payload_hash"] = registry.digest_record(value)
    return value


def create_result(request_id: str, status: str, **observed: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "request_id": request_id,
        "logical_key": "worker-for-issue-1",
        "action_kind": "create_thread",
        "owner_generation": 1,
        "status": status,
        "observed": observed,
    }
    value["result_hash"] = registry.digest_record(value)
    return value


def archive_request(
    request_id: str = "archive-1", thread_id: str = "worker-1"
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "request_id": request_id,
        "logical_key": f"run-1:issue-node-1:archive:{request_id}",
        "action_kind": "set_thread_archived",
        "tool_identity": "codex_app__set_thread_archived",
        "owner_generation": 1,
        "expected_state": {"archive_state": "eligible"},
        "arguments": {"thread_id": thread_id, "archived": True},
    }
    value["payload_hash"] = registry.digest_record(value)
    return value


def archive_result(
    request: dict[str, object], status: str = "succeeded", *, readback: bool = True
) -> dict[str, object]:
    observed: dict[str, object]
    if status == "succeeded":
        arguments = request["arguments"]
        assert isinstance(arguments, dict)
        observed = {
            "thread_id": arguments["thread_id"],
            "archived": True,
            "readback": readback,
        }
    else:
        observed = {"code": "host_unavailable"}
    value: dict[str, object] = {
        "schema_version": 1,
        "request_id": request["request_id"],
        "logical_key": request["logical_key"],
        "action_kind": "set_thread_archived",
        "owner_generation": 1,
        "status": status,
        "observed": observed,
    }
    value["result_hash"] = registry.digest_record(value)
    return value


def archive_requested_state(request: dict[str, object] | None = None) -> dict[str, object]:
    action = request or archive_request()
    registered = registry.apply_event(
        reconciled_state(),
        event("event-action-request", 3, "external_action_requested", request=action),
    )
    return registry.apply_event(
        registered,
        event(
            "event-archive-request",
            4,
            "archive_requested",
            ticket_id="issue-node-1",
            request_id=action["request_id"],
        ),
    )


def registered_state() -> dict[str, object]:
    initial = registry.initial_state(
        run_id="run-1",
        repo_id="repo-1",
        spec_digest="a" * 64,
        controller_thread_id="controller-1",
        owner_generation=1,
    )
    return registry.apply_event(
        initial,
        event(
            "event-register",
            1,
            "ticket_registered",
            ticket_id="issue-node-1",
            issue_number=1,
            thread_id="worker-1",
            worktree=".codex/worktrees/issue-1",
            branch="codex/issue-1",
            base_sha="b" * 40,
            head_sha="b" * 40,
            lease_generation=1,
        ),
    )


def reconciled_state() -> dict[str, object]:
    return registry.apply_event(
        registered_state(),
        event(
            "event-reconciled",
            2,
            "ticket_reconciled",
            ticket_id="issue-node-1",
            tested_sha="c" * 40,
            merged_sha="d" * 40,
            evidence_digest="e" * 64,
        ),
    )


class LifecycleRegistryTests(unittest.TestCase):
    def test_external_action_request_and_readback_are_durable(self) -> None:
        state = registry.initial_state("run-1", "repo-1", "a" * 64, "controller-1", 1)
        requested = registry.apply_event(
            state,
            event("event-action-request", 1, "external_action_requested", request=create_request()),
        )
        self.assertEqual(requested["actions"]["create-1"]["reconciliation"], "pending")
        self.assertIn("create-1", requested["pending_actions"])

        completed = registry.apply_event(
            requested,
            event(
                "event-action-result", 2, "external_action_result",
                result=create_result(
                    "create-1", "succeeded", thread_id="worker-1", readback=True,
                ),
            ),
        )
        self.assertEqual(completed["actions"]["create-1"]["reconciliation"], "reconciled")
        self.assertNotIn("create-1", completed["pending_actions"])

    def test_unknown_external_action_effect_remains_pending(self) -> None:
        state = registry.initial_state("run-1", "repo-1", "a" * 64, "controller-1", 1)
        requested = registry.apply_event(
            state,
            event("event-action-request", 1, "external_action_requested", request=create_request()),
        )
        unknown = registry.apply_event(
            requested,
            event(
                "event-action-result", 2, "external_action_result",
                result=create_result("create-1", "unknown", code="host-timeout"),
            ),
        )
        self.assertEqual(unknown["actions"]["create-1"]["reconciliation"], "unknown")
        self.assertIn("create-1", unknown["pending_actions"])

    def test_duplicate_and_reordered_replay_converges(self) -> None:
        initial = registry.initial_state("run-1", "repo-1", "a" * 64, "controller-1", 1)
        register = event(
            "event-register", 1, "ticket_registered",
            ticket_id="issue-node-1", issue_number=1, thread_id="worker-1",
            worktree=".codex/worktrees/issue-1", branch="codex/issue-1",
            base_sha="b" * 40, head_sha="b" * 40, lease_generation=1,
        )
        reconciled = event(
            "event-reconciled", 2, "ticket_reconciled",
            ticket_id="issue-node-1", tested_sha="c" * 40,
            merged_sha="d" * 40, evidence_digest="e" * 64,
        )
        ordered = registry.reduce_events(initial, [register, reconciled])
        replayed = registry.reduce_events(initial, [reconciled, register, register])
        self.assertEqual(replayed, ordered)
        self.assertEqual(replayed["tickets"]["issue-node-1"]["lifecycle"], "reconciled")

    def test_same_event_id_with_different_payload_is_conflict(self) -> None:
        state = registered_state()
        conflicting = event(
            "event-register", 1, "ticket_registered",
            ticket_id="issue-node-1", issue_number=99, thread_id="worker-1",
            worktree=".codex/worktrees/issue-1", branch="codex/issue-1",
            base_sha="b" * 40, head_sha="b" * 40, lease_generation=1,
        )
        with self.assertRaisesRegex(registry.RegistryError, "event_conflict"):
            registry.apply_event(state, conflicting)

    def test_stale_owner_generation_fails_closed(self) -> None:
        stale = event(
            "event-stale", 3, "archive_requested", ticket_id="issue-node-1",
            request_id="archive-1",
        )
        stale["owner_generation"] = 2
        stale["event_hash"] = registry.digest_record(
            {key: value for key, value in stale.items() if key != "event_hash"}
        )
        with self.assertRaisesRegex(registry.RegistryError, "stale_owner_generation"):
            registry.apply_event(reconciled_state(), stale)

    def test_archive_failure_is_orthogonal_to_execution(self) -> None:
        request = archive_request()
        requested = archive_requested_state(request)
        failed_action = registry.apply_event(
            requested,
            event(
                "event-action-result",
                5,
                "external_action_result",
                result=archive_result(request, status="failed"),
            ),
        )
        failed = registry.apply_event(
            failed_action,
            event(
                "event-archive-failed", 6, "archive_result",
                ticket_id="issue-node-1", request_id="archive-1",
            ),
        )
        ticket = failed["tickets"]["issue-node-1"]
        self.assertEqual(ticket["lifecycle"], "reconciled")
        self.assertEqual(ticket["archive_state"], "archive_pending")
        self.assertEqual(ticket["cleanup_state"], "not_requested")

    def test_archive_success_needs_readback_and_never_implies_cleanup(self) -> None:
        no_readback_request = archive_request("archive-no-readback")
        requested = archive_requested_state(no_readback_request)
        unreconciled_action = registry.apply_event(
            requested,
            event(
                "event-action-result",
                5,
                "external_action_result",
                result=archive_result(no_readback_request, readback=False),
            ),
        )
        pending = registry.apply_event(
            unreconciled_action,
            event(
                "event-archive-result-1", 6, "archive_result",
                ticket_id="issue-node-1", request_id="archive-no-readback",
            ),
        )
        self.assertEqual(pending["tickets"]["issue-node-1"]["archive_state"], "archive_pending")

        successful_request = archive_request("archive-success")
        requested = archive_requested_state(successful_request)
        reconciled_action = registry.apply_event(
            requested,
            event(
                "event-action-result",
                5,
                "external_action_result",
                result=archive_result(successful_request),
            ),
        )
        archived = registry.apply_event(
            reconciled_action,
            event(
                "event-archive-result-2", 6, "archive_result",
                ticket_id="issue-node-1", request_id="archive-success",
            ),
        )
        ticket = archived["tickets"]["issue-node-1"]
        self.assertEqual(ticket["archive_state"], "archived")
        self.assertEqual(ticket["cleanup_state"], "not_requested")

    def test_current_or_final_controller_is_not_archive_eligible(self) -> None:
        state = reconciled_state()
        state["tickets"]["issue-node-1"]["thread_id"] = "controller-1"
        request = archive_request(thread_id="controller-1")
        state = registry.apply_event(
            state,
            event("event-action-request", 3, "external_action_requested", request=request),
        )
        with self.assertRaisesRegex(registry.RegistryError, "archive_ineligible"):
            registry.apply_event(
                state,
                event("event-archive-request", 4, "archive_requested", ticket_id="issue-node-1", request_id="archive-1"),
            )

    def test_archive_requires_bound_action_and_exact_ticket_thread(self) -> None:
        with self.assertRaisesRegex(registry.RegistryError, "archive_action_missing"):
            registry.apply_event(
                reconciled_state(),
                event(
                    "event-archive-request", 3, "archive_requested",
                    ticket_id="issue-node-1", request_id="archive-1",
                ),
            )

        mismatched = archive_request(thread_id="different-worker")
        state = registry.apply_event(
            reconciled_state(),
            event("event-action-request", 3, "external_action_requested", request=mismatched),
        )
        with self.assertRaisesRegex(registry.RegistryError, "archive_thread_mismatch"):
            registry.apply_event(
                state,
                event(
                    "event-archive-request", 4, "archive_requested",
                    ticket_id="issue-node-1", request_id="archive-1",
                ),
            )

    def test_archive_result_cannot_forge_host_readback(self) -> None:
        requested = archive_requested_state()
        pending = registry.apply_event(
            requested,
            event(
                "event-forged-archive-result", 5, "archive_result",
                ticket_id="issue-node-1", request_id="archive-1",
            ),
        )
        self.assertEqual(pending["tickets"]["issue-node-1"]["archive_state"], "archive_pending")

    def test_registry_rejects_archive_ticket_without_bound_action(self) -> None:
        state = archive_requested_state()
        del state["actions"]["archive-1"]
        with self.assertRaisesRegex(registry.RegistryError, "invalid_archive_action_reference"):
            registry.validate_state(state)

    def test_registry_rejects_transcript_or_secret_fields(self) -> None:
        state = registered_state()
        state["raw_transcript"] = "private"
        with self.assertRaisesRegex(registry.RegistryError, "forbidden_registry_field"):
            registry.validate_state(state)

    def test_registry_rejects_malformed_nested_and_duplicate_ticket_state(self) -> None:
        malformed = registered_state()
        malformed["tickets"]["issue-node-1"] = {}
        with self.assertRaisesRegex(registry.RegistryError, "invalid_ticket_record"):
            registry.validate_state(malformed)

        duplicate = registered_state()
        duplicate["tickets"]["issue-node-2"] = copy.deepcopy(
            duplicate["tickets"]["issue-node-1"]
        )
        with self.assertRaisesRegex(registry.RegistryError, "duplicate_ticket_identity"):
            registry.validate_state(duplicate)

        dangling = registered_state()
        dangling["pending_actions"] = ["missing-action"]
        with self.assertRaisesRegex(registry.RegistryError, "dangling_pending_action"):
            registry.validate_state(dangling)

    def test_registry_rejects_missing_or_stale_pending_action_reference(self) -> None:
        state = registry.apply_event(
            registry.initial_state("run-1", "repo-1", "a" * 64, "controller-1", 1),
            event("event-action-request", 1, "external_action_requested", request=create_request()),
        )
        missing = copy.deepcopy(state)
        missing["pending_actions"] = []
        with self.assertRaisesRegex(registry.RegistryError, "missing_pending_action"):
            registry.validate_state(missing)

        reconciled = registry.apply_event(
            state,
            event(
                "event-action-result", 2, "external_action_result",
                result=create_result(
                    "create-1", "succeeded", thread_id="worker-1", readback=True,
                ),
            ),
        )
        reconciled["pending_actions"] = ["create-1"]
        with self.assertRaisesRegex(registry.RegistryError, "stale_pending_action"):
            registry.validate_state(reconciled)

    def test_registry_rejects_malformed_planning_stage(self) -> None:
        state = registered_state()
        state["planning_stages"] = {"wayfinder/unit-1": {"lifecycle": "unknown"}}
        with self.assertRaisesRegex(registry.RegistryError, "invalid_planning_stage"):
            registry.validate_state(state)

    def test_atomic_store_is_external_restrictive_and_compare_and_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            project = parent / "project"
            project.mkdir(mode=0o700)
            state_root = parent / "host-state"
            state_root.mkdir(mode=0o700)
            state = registered_state()
            store = registry.RegistryStore(state_root, project, "repo-1", "run-1")
            same = registry.RegistryStore(state_root, project, "repo-1", "run-1")
            other = registry.RegistryStore(state_root, project, "repo-1", "run-2")
            self.assertEqual(store.path, same.path)
            self.assertNotEqual(store.path, other.path)
            self.assertEqual(store.path.parent, state_root.resolve())
            first_digest = store.write(state, expected_digest=None)
            self.assertEqual(store.read(), state)
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)

            changed = copy.deepcopy(state)
            changed["status"] = "blocked"
            with self.assertRaisesRegex(registry.RegistryError, "registry_compare_failed"):
                store.write(changed, expected_digest="0" * 64)
            second_digest = store.write(changed, expected_digest=first_digest)
            self.assertNotEqual(second_digest, first_digest)
            self.assertEqual(store.read()["status"], "blocked")

            with self.assertRaisesRegex(registry.RegistryError, "registry_inside_project"):
                registry.RegistryStore(project, project, "repo-1", "run-1")

            wrong_identity = copy.deepcopy(state)
            wrong_identity["run_id"] = "run-2"
            with self.assertRaisesRegex(registry.RegistryError, "registry_identity_mismatch"):
                store.write(wrong_identity, expected_digest=second_digest)


if __name__ == "__main__":
    unittest.main()
