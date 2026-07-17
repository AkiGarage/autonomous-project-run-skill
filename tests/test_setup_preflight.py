#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "skills/autonomous-project-run/scripts/setup_preflight.py"
SETUP_DOCS_FOR_TEST = (
    "docs/agents/domain.md",
    "docs/agents/issue-tracker.md",
    "docs/agents/triage-labels.md",
)


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)


def run_preflight(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(PREFLIGHT), "--repo", str(repo)],
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )


def configure_repo(repo: Path, instructions: str = "AGENTS.md") -> None:
    docs = repo / "docs/agents"
    docs.mkdir(parents=True)
    (docs / "issue-tracker.md").write_text("# Issue tracker\n\nGitHub.\n")
    (docs / "triage-labels.md").write_text("# Triage labels\n\nDefaults.\n")
    (docs / "domain.md").write_text("# Domain docs\n\nSingle-context.\n")
    (repo / instructions).write_text(
        "# Instructions\n\n"
        "## Agent skills\n\n"
        "See `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`, "
        "and `docs/agents/domain.md`.\n"
    )


class SetupPreflightTests(unittest.TestCase):
    def test_complete_setup_returns_bounded_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            configure_repo(repo)

            result = run_preflight(repo)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "code": "setup_complete",
                "configured": True,
                "decision": "evidence",
                "instruction_file": "AGENTS.md",
                "missing": [],
                "schema_version": 1,
            },
        )

    def test_missing_setup_reports_exact_required_surface(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)

            result = run_preflight(repo)

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "evidence")
        self.assertEqual(output["code"], "setup_required")
        self.assertFalse(output["configured"])
        self.assertEqual(output["instruction_file"], None)
        self.assertEqual(
            output["missing"],
            [
                "AGENTS.md|CLAUDE.md:Agent skills",
                "docs/agents/domain.md",
                "docs/agents/issue-tracker.md",
                "docs/agents/triage-labels.md",
            ],
        )

    def test_claude_instructions_take_precedence_when_both_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            configure_repo(repo)
            (repo / "CLAUDE.md").write_text("# Claude instructions\n")

            result = run_preflight(repo)

        output = json.loads(result.stdout)
        self.assertFalse(output["configured"])
        self.assertEqual(output["instruction_file"], "CLAUDE.md")
        self.assertIn("CLAUDE.md:Agent skills", output["missing"])

    def test_symlinked_setup_document_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            configure_repo(repo)
            target = repo / "domain-target.md"
            target.write_text("# Domain docs\n")
            (repo / "docs/agents/domain.md").unlink()
            (repo / "docs/agents/domain.md").symlink_to(target)

            result = run_preflight(repo)

        output = json.loads(result.stdout)
        self.assertFalse(output["configured"])
        self.assertIn("docs/agents/domain.md", output["missing"])

    def test_symlinked_setup_directory_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            external = Path(directory) / "external"
            repo.mkdir()
            external.mkdir()
            init_repo(repo)
            configure_repo(external)
            (repo / "docs").symlink_to(external / "docs", target_is_directory=True)
            (repo / "AGENTS.md").write_text((external / "AGENTS.md").read_text())

            result = run_preflight(repo)

        output = json.loads(result.stdout)
        self.assertFalse(output["configured"])
        self.assertEqual(
            [item for item in output["missing"] if item.startswith("docs/agents/")],
            list(SETUP_DOCS_FOR_TEST),
        )

    def test_symlinked_or_oversized_instruction_file_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            configure_repo(repo)
            external = repo / "external-instructions.md"
            external.write_text((repo / "AGENTS.md").read_text())
            (repo / "AGENTS.md").unlink()
            (repo / "AGENTS.md").symlink_to(external)
            symlinked = json.loads(run_preflight(repo).stdout)
            self.assertIn("AGENTS.md:Agent skills", symlinked["missing"])

            (repo / "AGENTS.md").unlink()
            (repo / "AGENTS.md").write_text("## Agent skills\n" + "x" * (300 * 1024))
            oversized = json.loads(run_preflight(repo).stdout)

        self.assertIn("AGENTS.md:Agent skills", oversized["missing"])

    def test_fifo_setup_document_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            configure_repo(repo)
            fifo = repo / "docs/agents/domain.md"
            fifo.unlink()
            os.mkfifo(fifo)

            result = run_preflight(repo)

        output = json.loads(result.stdout)
        self.assertFalse(output["configured"])
        self.assertIn("docs/agents/domain.md", output["missing"])

    def test_non_repository_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_preflight(Path(directory))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout),
            {"code": "not_git_repository", "decision": "block", "schema_version": 1},
        )


if __name__ == "__main__":
    unittest.main()
