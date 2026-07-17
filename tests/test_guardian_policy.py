#!/usr/bin/env python3

import json
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY = REPO_ROOT / "skills/autonomous-project-run/scripts/guardian_policy.py"


def valid_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "schema_version": 1,
        "project_key": "project-1",
        "guardian_id": "guardian-1",
        "singleton_owner": "guardian-1",
        "poll_id": "poll-2",
        "last_poll_id": "poll-1",
        "lifecycle": "active",
        "state_digest": "a" * 64,
        "previous_digest": "b" * 64,
        "metrics": {
            "guardian": {"tokens": 12, "compactions": 0},
            "implementation": {"tokens": 345, "compactions": 2},
        },
    }
    state.update(overrides)
    return state


def run_policy(state: dict[str, object] | bytes) -> subprocess.CompletedProcess[str]:
    payload = state if isinstance(state, bytes) else json.dumps(state).encode()
    return subprocess.run(
        ["python3", str(POLICY)],
        input=payload,
        capture_output=True,
        check=False,
        text=False,
    )


class GuardianPolicyTests(unittest.TestCase):
    def assert_silent(self, state: dict[str, object]) -> None:
        result = run_policy(state)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_changed_state_emits_only_bounded_delta(self) -> None:
        result = run_policy(valid_state())
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        output = json.loads(result.stdout)
        self.assertEqual(set(output), {"kind", "project_key", "state_digest", "metrics"})
        self.assertEqual(output["kind"], "delta")

    def test_unchanged_state_is_silent(self) -> None:
        self.assert_silent(valid_state(previous_digest="a" * 64))

    def test_duplicate_poll_and_non_owner_are_silent(self) -> None:
        self.assert_silent(valid_state(last_poll_id="poll-2"))
        self.assert_silent(valid_state(singleton_owner="guardian-2"))

    def test_terminal_states_are_silent(self) -> None:
        self.assert_silent(valid_state(lifecycle="complete"))
        self.assert_silent(valid_state(lifecycle="blocked"))

    def test_oversized_or_transcript_input_returns_only_blocker(self) -> None:
        oversized = run_policy(b"{" + b" " * 4096 + b"}")
        self.assertNotEqual(oversized.returncode, 0)
        self.assertEqual(json.loads(oversized.stdout)["kind"], "blocker")

        with_transcript = valid_state(transcript="must not be inherited")
        rejected = run_policy(with_transcript)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(set(json.loads(rejected.stdout)), {"kind", "code"})

    def test_boolean_schema_version_is_rejected(self) -> None:
        rejected = run_policy(valid_state(schema_version=True))
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(json.loads(rejected.stdout)["code"], "unsupported_schema")

    def test_metrics_keep_guardian_and_implementation_separate(self) -> None:
        output = json.loads(run_policy(valid_state()).stdout)
        self.assertEqual(set(output["metrics"]), {"guardian", "implementation"})
        self.assertEqual(output["metrics"]["guardian"]["tokens"], 12)
        self.assertEqual(output["metrics"]["implementation"]["compactions"], 2)


if __name__ == "__main__":
    unittest.main()
