import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest


GATE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "skills"
    / "autonomous-project-run"
    / "scripts"
    / "runtime_gate.py"
)
SPEC = importlib.util.spec_from_file_location("apr_runtime_gate_policy", GATE_PATH)
GATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GATE)


class RuntimeGatePolicyTests(unittest.TestCase):
    def test_valid_trigger_is_canonical_and_deterministic(self):
        trigger = {
            "reason": "ineffective_compaction",
            "evidence": {"source": "p0_telemetry", "reduction": "unknown"},
            "safe_checkpoint": True,
        }
        first = GATE.validate_handoff_trigger(trigger)
        self.assertEqual(first, GATE.validate_handoff_trigger(trigger))
        self.assertEqual(len(first["evidence_digest"]), 64)

    def test_trigger_rejects_mechanical_unsafe_and_sensitive_reasons(self):
        base = {"evidence": {"source": "phase"}, "safe_checkpoint": True}
        with self.assertRaisesRegex(GATE.GateError, "invalid_handoff_trigger_reason"):
            GATE.validate_handoff_trigger({**base, "reason": "second_compaction"})
        with self.assertRaisesRegex(GATE.GateError, "unsafe_handoff_checkpoint"):
            GATE.validate_handoff_trigger(
                {**base, "reason": "natural_phase_boundary", "safe_checkpoint": False}
            )
        with self.assertRaisesRegex(GATE.GateError, "forbidden_context"):
            GATE.validate_handoff_trigger(
                {
                    "reason": "natural_phase_boundary",
                    "evidence": {"access_token": "redacted"},
                    "safe_checkpoint": True,
                }
            )

    def test_deep_trigger_fails_closed_without_traceback(self):
        nested = {"leaf": "value"}
        for _ in range(GATE.MAX_NESTED_DEPTH + 2):
            nested = [nested]
        payload = {
            "action": "handoff",
            "handoff_trigger": {
                "reason": "natural_phase_boundary",
                "evidence": {"nested": nested},
                "safe_checkpoint": True,
            },
        }
        completed = subprocess.run(
            [sys.executable, str(GATE_PATH)],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr, b"")
        self.assertEqual(
            json.loads(completed.stdout),
            {"decision": "block", "code": "context_too_deep"},
        )


if __name__ == "__main__":
    unittest.main()
