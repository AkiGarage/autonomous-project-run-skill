import pathlib
import unittest


SKILL_PATH = pathlib.Path(__file__).parents[1] / "skills" / "autonomous-project-run" / "SKILL.md"


class AprPolicyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = SKILL_PATH.read_text(encoding="utf-8")

    def test_policy_removes_fixed_routing_and_compaction_rules(self):
        self.assertNotRegex(self.skill, r"64k")
        self.assertNotRegex(self.skill, r"50[–-]70")
        self.assertNotIn("60%", self.skill)
        self.assertNotIn(
            "After the first automatic root compaction, stop broad exploration",
            self.skill,
        )

    def test_policy_preserves_bootstrap_and_uses_roi_risk_triggers(self):
        self.assertIn("## Bootstrap the repository workflow", self.skill)
        self.assertRegex(self.skill, r"(?i)ROI")
        self.assertRegex(self.skill, r"(?i)risk")
        for reason in (
            "natural_phase_boundary",
            "ineffective_compaction",
            "unrecoverable_context_pressure",
        ):
            self.assertIn(reason, self.skill)
        self.assertIn("safe checkpoint", self.skill)

    def test_native_host_timeout_is_bounded_and_readback_gated(self):
        self.assertIn("bounded host-call deadline", self.skill)
        self.assertIn("end the wrapper without waiting indefinitely", self.skill)
        self.assertIn("unknown mutation outcome", self.skill)
        self.assertIn("list/read reconciliation before retry", self.skill)

    def test_successor_transfer_and_terminal_release_are_host_bound(self):
        self.assertIn(
            "approval-reviewed `--managed-worktree <absolute-path>` route",
            self.skill,
        )
        self.assertIn(
            "never ask the user to reopen the project or repeat the request",
            self.skill,
        )
        self.assertIn("two-phase release", self.skill)
        self.assertIn("write-ahead transaction under the lease lock", self.skill)
        self.assertIn("continues automatically under APR protection", self.skill)

    def test_host_action_and_registry_contracts_remain_explicit(self):
        self.assertIn("Use `scripts/host_actions.py` only", self.skill)
        self.assertIn(
            "Exact fixture-backed tool identities are accepted; unmatched aliases fail closed",
            self.skill,
        )
        self.assertIn("Use `scripts/lifecycle_registry.py` to reduce", self.skill)
        self.assertIn("Archive failure or unknown outcome remains `archive_pending`", self.skill)


if __name__ == "__main__":
    unittest.main()
