from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/autonomous-project-run/scripts"
sys.path.insert(0, str(SCRIPTS))
import host_actions  # noqa: E402


def digest(value: dict[str, object]) -> str:
    return hashlib.sha256(host_actions.canonical_json(value)).hexdigest()


def create_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "schema_version": 1,
        "request_id": "request-create-1",
        "logical_key": "run-1:controller-successor:2",
        "action_kind": "create_thread",
        "tool_identity": "codex_app__create_thread",
        "owner_generation": 2,
        "expected_state": {"controller": "checkpoint_prepared"},
        "arguments": {
            "task_kind": "controller_successor",
            "prompt_digest": "a" * 64,
            "target": {
                "type": "project",
                "project_id": "project-1",
                "environment": {
                    "type": "worktree",
                    "starting_state": {"type": "branch", "branch_name": "codex/run-1"},
                },
            },
        },
    }
    request.update(overrides)
    request["payload_hash"] = digest(request)
    return request


def archive_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "schema_version": 1,
        "request_id": "request-archive-1",
        "logical_key": "run-1:ticket-1:archive:1",
        "action_kind": "set_thread_archived",
        "tool_identity": "codex_app__set_thread_archived",
        "owner_generation": 1,
        "expected_state": {"archive_state": "eligible"},
        "arguments": {"thread_id": "thread-worker-1", "host_id": "local", "archived": True},
    }
    request.update(overrides)
    request["payload_hash"] = digest(request)
    return request


def result_for(request: dict[str, object], **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": 1,
        "request_id": request["request_id"],
        "logical_key": request["logical_key"],
        "action_kind": request["action_kind"],
        "owner_generation": request["owner_generation"],
        "status": "succeeded",
        "observed": {"thread_id": "thread-2", "host_id": "local", "readback": True},
    }
    result.update(overrides)
    result["result_hash"] = digest(result)
    return result


class HostActionTests(unittest.TestCase):
    def test_fixture_freezes_exact_namespaced_host_identities(self) -> None:
        fixture = json.loads((ROOT / "tests/fixtures/host-actions-v1.json").read_text())
        observed = {
            entry["identity"]: entry["canonical"] for entry in fixture["tools"]
        }
        self.assertEqual(
            {identity: host_actions.canonicalize_tool(identity) for identity in observed},
            observed,
        )
        self.assertEqual(
            {
                identity: host_actions.canonicalize_tool(identity)
                for identity in fixture["pretooluse_identities"]
            },
            fixture["pretooluse_identities"],
        )
        self.assertEqual(fixture["capabilities"]["trusted_owner_evidence"], "unavailable")

    def test_unknown_or_bare_mutation_aliases_fail_closed(self) -> None:
        for identity in ("create_thread", "codex_app_create_thread", "fork_thread"):
            with self.subTest(identity=identity), self.assertRaisesRegex(
                host_actions.ProtocolError, "unknown_tool_identity"
            ):
                host_actions.canonicalize_tool(identity)

    def test_create_request_is_hash_and_owner_bound_without_raw_prompt(self) -> None:
        request = create_request()
        checked = host_actions.validate_request(request)
        self.assertEqual(checked["owner_generation"], 2)
        self.assertEqual(checked["arguments"]["task_kind"], "controller_successor")

        tampered = json.loads(json.dumps(request))
        tampered["owner_generation"] = 3
        with self.assertRaisesRegex(host_actions.ProtocolError, "request_hash_mismatch"):
            host_actions.validate_request(tampered)

        raw_prompt = create_request()
        raw_prompt["arguments"]["prompt"] = "retired transcript"
        raw_prompt["payload_hash"] = digest({k: v for k, v in raw_prompt.items() if k != "payload_hash"})
        with self.assertRaisesRegex(host_actions.ProtocolError, "invalid_create_arguments"):
            host_actions.validate_request(raw_prompt)

    def test_archive_request_requires_exact_thread_and_archive_true(self) -> None:
        self.assertEqual(
            host_actions.validate_request(archive_request())["arguments"]["thread_id"],
            "thread-worker-1",
        )
        request = archive_request()
        request["arguments"]["archived"] = False
        request["payload_hash"] = digest({k: v for k, v in request.items() if k != "payload_hash"})
        with self.assertRaisesRegex(host_actions.ProtocolError, "invalid_archive_arguments"):
            host_actions.validate_request(request)

    def test_create_result_requires_readback_to_reconcile(self) -> None:
        request = create_request()
        submitted = result_for(
            request,
            observed={"client_thread_id": "client-2", "readback": False},
        )
        self.assertEqual(host_actions.reconcile(request, submitted), "submitted")

        reconciled = result_for(request)
        self.assertEqual(host_actions.reconcile(request, reconciled), "reconciled")

        mismatched = result_for(request, request_id="another-request")
        with self.assertRaisesRegex(host_actions.ProtocolError, "result_request_mismatch"):
            host_actions.reconcile(request, mismatched)

    def test_archive_failure_or_missing_readback_is_pending(self) -> None:
        request = archive_request()
        failed = result_for(request, status="failed", observed={"code": "host_unavailable"})
        self.assertEqual(host_actions.reconcile(request, failed), "archive_pending")

        unknown = result_for(request, status="unknown", observed={})
        self.assertEqual(host_actions.reconcile(request, unknown), "archive_pending")

        no_readback = result_for(
            request, observed={"thread_id": "thread-worker-1", "archived": True, "readback": False}
        )
        self.assertEqual(host_actions.reconcile(request, no_readback), "archive_pending")

        complete = result_for(
            request, observed={"thread_id": "thread-worker-1", "archived": True, "readback": True}
        )
        self.assertEqual(host_actions.reconcile(request, complete), "reconciled")


if __name__ == "__main__":
    unittest.main()
