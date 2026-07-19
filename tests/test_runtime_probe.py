from __future__ import annotations

import hashlib
import fcntl
import hmac
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "skills/autonomous-project-run/scripts/runtime_probe.py"
sys.path.insert(0, str(PROBE.parent))
import runtime_probe  # noqa: E402
import runtime_gate  # noqa: E402


SUPERVISOR_CAPABILITY_FD = runtime_gate.SUPERVISOR_CAPABILITY_FD
SUPERVISOR_KEY_FD = runtime_gate.SUPERVISOR_KEY_FD
SUPERVISOR_LOCK_FD = runtime_gate.SUPERVISOR_LOCK_FD
SUPERVISOR_KEY = b"apr-test-supervisor-key-0123456789"


def canonical_hash(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_pipe(fd: int, value: bytes) -> None:
    written = 0
    while written < len(value):
        written += os.write(fd, value[written:])


def _install_supervisor_streams(
    fixture: dict[str, object], lock_stream: object,
) -> tuple[int, int]:
    lock_info = os.fstat(lock_stream.fileno())
    unsigned = {
        "schema_version": 1,
        "project_root": fixture["project_root"],
        "worktree": fixture["worktree"],
        "repo_identity": fixture["repo_identity"],
        "owner": fixture["owner"],
        "scope": fixture["scope"],
        "generation": fixture["generation"],
        "lease_id": fixture["lease_id"],
        "fencing_token": fixture["fencing_token"],
        "lease_expires": fixture["lease_expires"],
        "lock_dev": lock_info.st_dev,
        "lock_ino": lock_info.st_ino,
        "nonce": "test-supervisor-nonce",
    }
    canonical = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    capability = {**unsigned, "mac": hmac.new(SUPERVISOR_KEY, canonical, hashlib.sha256).hexdigest()}
    stream_dir = Path(tempfile.mkdtemp(prefix="apr-supervisor-streams-"))
    capability_path = stream_dir / "capability"
    key_path = stream_dir / "key"
    os.mkfifo(capability_path, 0o600)
    os.mkfifo(key_path, 0o600)
    capability_read = os.open(capability_path, os.O_RDONLY | os.O_NONBLOCK)
    key_read = os.open(key_path, os.O_RDONLY | os.O_NONBLOCK)
    capability_write = os.open(capability_path, os.O_WRONLY | os.O_NONBLOCK)
    key_write = os.open(key_path, os.O_WRONLY | os.O_NONBLOCK)
    try:
        _write_pipe(capability_write, json.dumps(capability, separators=(",", ":")).encode())
        _write_pipe(key_write, SUPERVISOR_KEY)
    finally:
        os.close(capability_write)
        os.close(key_write)
        capability_path.unlink()
        key_path.unlink()
        stream_dir.rmdir()
    return capability_read, key_read


def _replace_fixed_fd(target: int, source: int) -> int | None:
    try:
        saved = os.dup(target)
    except OSError:
        saved = None
    os.dup2(source, target)
    os.set_inheritable(target, True)
    return saved


def _restore_fixed_fd(target: int, saved: int | None) -> None:
    if saved is None:
        try:
            os.close(target)
        except OSError:
            pass
        return
    os.dup2(saved, target)
    os.close(saved)


class RuntimeProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "tracked.txt").write_text("ok\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "tracked.txt"], check=True)
        commit_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "init"], check=True, env=commit_env)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_probe(self, extra: dict | None = None, cwd: Path | None = None) -> tuple[int, dict]:
        cwd = cwd or self.repo
        checkout = Path(subprocess.check_output(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], text=True,
        ).strip()).resolve()
        if checkout.parent.name == "worktrees" and checkout.parent.parent.name == ".codex":
            evidence_project = checkout.parent.parent.parent
        else:
            evidence_project = self.repo.resolve()
        evidence_path = evidence_project / ".codex" / "owner-evidence.json"
        lock_path = Path(tempfile.gettempdir()) / f"apr-probe-lock-{os.getpid()}-{id(self)}.lock"
        lock_path.touch(exist_ok=True)
        os.chmod(lock_path, 0o600)
        facts = runtime_probe.collect(cwd, exclude_paths={str(evidence_path)})
        digest = hashlib.sha256(b"requirements").hexdigest()
        head = facts["head"]
        source = facts["fingerprints"]["source"]
        spec_digest = "2" * 64
        dependency_digest = "4" * 64
        toolchain_digest = "5" * 64
        fingerprints = {
            **facts["fingerprints"],
            "requirements": digest,
            "spec": spec_digest,
            "dependencies": dependency_digest,
            "toolchain": toolchain_digest,
        }
        payload = {
            "action": "pre_mutation",
            "requirements_digest": digest,
            "role": "executor",
            "authority": {"confirmed": True, "scope": "ticket-1"},
            "base": "main",
            "spec_revision": "r1",
            "spec_digest": "2" * 64,
            "source_fingerprint": source,
            "dependency_fingerprint": dependency_digest,
            "toolchain_fingerprint": toolchain_digest,
            "owner": "owner-1",
            "lease": {
                "id": "lease-1", "owner": "owner-1", "generation": 1, "active": True,
                "expires": int(__import__("time").time()) + 3600,
                "runtime_state_path": str(Path(tempfile.gettempdir()) / f"apr-probe-state-{os.getpid()}.json"),
                "owner_lock_path": str(lock_path),
                "fencing_token": "fence-1",
            },
            "checkpoint": {"generation": 1, "head": head, "source_fingerprint": source, "requirements_digest": digest},
            "dirty_state": facts["dirty_state"],
            "fingerprints": fingerprints,
            "owner_evidence_path": str(evidence_path),
            "pending_side_effects": [],
            "unknown_outcomes": [],
        }
        evidence = {
            "schema_version": 1,
            "owner": "owner-1",
            "scope": "ticket-1",
            "generation": 1,
            "lease_id": "lease-1",
            "project_root": facts["project_root"],
            "worktree": facts["worktree"],
            "repo_identity": facts["repo_identity"]["identity"],
            "common_dir": facts["common_dir"],
            "branch": facts["branch"],
            "base": "main",
            "head": head,
            "requirements_digest": digest,
            "spec_revision": "r1",
            "spec_digest": spec_digest,
            "source_fingerprint": source,
            "dependency_fingerprint": dependency_digest,
            "toolchain_fingerprint": toolchain_digest,
            "checkpoint_fingerprint": source,
            "dirty_state": facts["dirty_state"],
            "fingerprints": fingerprints,
            "runtime_state_path": payload["lease"]["runtime_state_path"],
            "owner_lock_path": payload["lease"]["owner_lock_path"],
            "fencing_token": payload["lease"]["fencing_token"],
            "lease_expires": payload["lease"]["expires"],
        }
        evidence["hash"] = canonical_hash(evidence)
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
        os.chmod(evidence_path, 0o600)
        state = {
            "schema_version": 1,
            "project_root": facts["project_root"],
            "worktree": facts["worktree"],
            "repo_identity": facts["repo_identity"]["identity"],
            "owner": "owner-1",
            "scope": "ticket-1",
            "generation": 1,
            "lease_id": "lease-1",
            "fencing_token": payload["lease"]["fencing_token"],
            "lease_expires": payload["lease"]["expires"],
            "owner_lock_path": payload["lease"]["owner_lock_path"],
            "owner_evidence_hash": evidence["hash"],
        }
        state["hash"] = canonical_hash(state)
        state_path = Path(str(payload["lease"]["runtime_state_path"]))
        state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.chmod(state_path, 0o600)
        fixture = {
            "project_root": facts["project_root"],
            "worktree": facts["worktree"],
            "repo_identity": facts["repo_identity"]["identity"],
            "owner": "owner-1",
            "scope": "ticket-1",
            "generation": 1,
            "lease_id": "lease-1",
            "fencing_token": payload["lease"]["fencing_token"],
            "lease_expires": payload["lease"]["expires"],
            "lock_path": str(lock_path),
        }
        payload.update(extra or {})
        lock_stream = open(lock_path, "a+b")
        capability_read = None
        key_read = None
        try:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            capability_read, key_read = _install_supervisor_streams(fixture, lock_stream)
            saved = {
                SUPERVISOR_CAPABILITY_FD: _replace_fixed_fd(
                    SUPERVISOR_CAPABILITY_FD, capability_read
                ),
                SUPERVISOR_KEY_FD: _replace_fixed_fd(SUPERVISOR_KEY_FD, key_read),
                SUPERVISOR_LOCK_FD: _replace_fixed_fd(
                    SUPERVISOR_LOCK_FD, lock_stream.fileno()
                ),
            }
            try:
                result = subprocess.run(
                    ["python3", str(PROBE)], cwd=cwd, input=json.dumps(payload),
                    text=True, capture_output=True, check=False,
                    env={**os.environ, runtime_probe.SUPERVISED_ENV: "1"},
                    pass_fds=(SUPERVISOR_CAPABILITY_FD, SUPERVISOR_KEY_FD, SUPERVISOR_LOCK_FD),
                )
            finally:
                for target, previous in saved.items():
                    _restore_fixed_fd(target, previous)
            return result.returncode, json.loads(result.stdout)
        finally:
            for descriptor in (capability_read, key_read):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
            lock_stream.close()

    def test_rejects_main_checkout_mutation(self) -> None:
        status, output = self.run_probe()
        self.assertEqual(status, 2)
        self.assertEqual(output["decision"], "block")
        self.assertEqual(output["code"], "main_checkout_mutation")

    def test_rejects_spoofed_authoritative_fields(self) -> None:
        for key, value in (
            ("project_root", "/tmp/not-this-repo"),
            ("common_dir", "/tmp/not-this-common-dir"),
            ("head", "0" * 40),
            ("fingerprints", {"repo": "0" * 64}),
        ):
            with self.subTest(key=key):
                status, output = self.run_probe({key: value})
                self.assertEqual(status, 2)
                self.assertEqual(output["code"], "probe_mismatch")

    def test_linked_worktree_has_deterministic_main_project_root(self) -> None:
        linked = Path(self.temp.name) / "linked"
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "-q", "-b", "linked-test", str(linked)],
            check=True,
        )
        status, output = self.run_probe(cwd=linked)
        self.assertEqual(status, 2)
        self.assertEqual(output["code"], "external_worktree")

    def test_managed_worktree_uses_registered_physical_project_root(self) -> None:
        project = Path(self.temp.name) / "linked-project"
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "-q", "-b", "project-test", str(project)],
            check=True,
        )
        managed = project / ".codex" / "worktrees" / "owner"
        managed.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "-C", str(project), "worktree", "add", "-q", "-b", "managed-test", str(managed)],
            check=True,
        )

        facts = runtime_probe.collect(managed)

        self.assertEqual(facts["project_root"], str(project.resolve()))
        self.assertEqual(facts["worktree"], str(managed.resolve()))
        self.assertEqual(facts["common_dir"], str((self.repo / ".git").resolve()))

        status, output = self.run_probe(cwd=managed)
        self.assertEqual(status, 0)
        self.assertEqual(output["decision"], "evidence")
        self.assertEqual(output["code"], "pre_mutation_evidence_valid")

    def test_managed_worktree_rejects_unregistered_physical_project_root(self) -> None:
        project = Path(self.temp.name) / "linked-project"
        managed = project / ".codex" / "worktrees" / "owner"

        with self.assertRaisesRegex(runtime_probe.ProbeError, "ambiguous_project_root"):
            runtime_probe._project_root(
                str(managed),
                str((self.repo / ".git").resolve()),
                [str(self.repo.resolve()), str(managed.resolve())],
            )

    def test_main_probe_excludes_registered_nested_managed_worktree_marker(self) -> None:
        managed = self.repo / ".codex" / "worktrees" / "owner"
        managed.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "-q", "-b", "managed-owner", str(managed)],
            check=True,
        )
        marker = subprocess.check_output(
            ["git", "-C", str(self.repo), "ls-files", "--others", "--exclude-standard", "-z"],
        )
        self.assertEqual(marker, b".codex/worktrees/owner/\0")

        facts = runtime_probe.collect(self.repo)

        self.assertFalse(facts["dirty_state"]["untracked"])

    def test_main_probe_rejects_unregistered_nested_repository_marker(self) -> None:
        nested = self.repo / "unregistered"
        subprocess.run(["git", "init", "-q", str(nested)], check=True)

        with self.assertRaisesRegex(runtime_probe.ProbeError, "untracked_nested_repository"):
            runtime_probe.collect(self.repo)

    def test_rejects_bare_repository(self) -> None:
        bare = Path(self.temp.name) / "bare.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        result = subprocess.run(
            ["python3", str(PROBE)], cwd=bare,
            input=json.dumps({"action": "pre_mutation"}), text=True,
            capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["code"], "bare_worktree")

    def test_content_fingerprint_changes_for_tracked_and_untracked_bytes(self) -> None:
        first = runtime_probe.collect(self.repo)
        (self.repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        second = runtime_probe.collect(self.repo)
        self.assertNotEqual(first["fingerprints"]["content"], second["fingerprints"]["content"])
        self.assertNotEqual(first["fingerprints"]["source"], second["fingerprints"]["source"])
        scratch = self.repo / "scratch.txt"
        scratch.write_text("one\n", encoding="utf-8")
        third = runtime_probe.collect(self.repo)
        self.assertNotEqual(second["fingerprints"]["content"], third["fingerprints"]["content"])
        scratch.write_text("two\n", encoding="utf-8")
        fourth = runtime_probe.collect(self.repo)
        self.assertNotEqual(third["fingerprints"]["content"], fourth["fingerprints"]["content"])

    def test_content_fingerprint_changes_for_executable_mode(self) -> None:
        (self.repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        first = runtime_probe.collect(self.repo)
        original_mode = os.stat(self.repo / "tracked.txt").st_mode & 0o777
        try:
            os.chmod(self.repo / "tracked.txt", original_mode ^ 0o111)
            second = runtime_probe.collect(self.repo)
        finally:
            os.chmod(self.repo / "tracked.txt", original_mode)
        self.assertNotEqual(first["fingerprints"]["source"], second["fingerprints"]["source"])

    def test_content_fingerprint_rejects_intermediate_symlink_escape(self) -> None:
        nested = self.repo / "nested"
        nested.mkdir()
        tracked = nested / "tracked.txt"
        tracked.write_text("inside\n", encoding="utf-8")
        commit_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
        subprocess.run(["git", "-C", str(self.repo), "add", "nested/tracked.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-qm", "nested"],
            check=True,
            env=commit_env,
        )
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "tracked.txt").write_text("outside\n", encoding="utf-8")
        nested.rename(self.repo / "nested-real")
        nested.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(runtime_probe.ProbeError, "^git_probe_failed$"):
            runtime_probe._path_content_digest(str(self.repo), {b"nested/tracked.txt"})

    def test_rejects_json_with_huge_integer_as_invalid_json(self) -> None:
        raw = b'{"action":"pre_mutation","generation":' + (b"9" * 5000) + b"}"
        result = subprocess.run(
            ["python3", str(PROBE)], cwd=self.repo, input=raw,
            capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["code"], "invalid_json")

    def test_git_probe_timeout_is_stable(self) -> None:
        timeout = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
        with mock.patch.object(runtime_probe.subprocess, "run", side_effect=timeout):
            with self.assertRaisesRegex(runtime_probe.ProbeError, "^git_probe_timeout$"):
                runtime_probe._git(self.repo, "status")

    def test_git_probe_oserror_is_stable(self) -> None:
        with mock.patch.object(runtime_probe.subprocess, "run", side_effect=OSError("boom")):
            with self.assertRaisesRegex(runtime_probe.ProbeError, "^git_probe_failed$"):
                runtime_probe._git(self.repo, "status")

    def test_dirty_probe_timeout_is_stable(self) -> None:
        timeout = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
        with mock.patch.object(runtime_probe.subprocess, "run", side_effect=timeout):
            with self.assertRaisesRegex(runtime_probe.ProbeError, "^git_probe_timeout$"):
                runtime_probe._dirty(self.repo, "status")

    def test_dirty_probe_oserror_is_stable(self) -> None:
        with mock.patch.object(runtime_probe.subprocess, "run", side_effect=OSError("boom")):
            with self.assertRaisesRegex(runtime_probe.ProbeError, "^git_probe_failed$"):
                runtime_probe._dirty(self.repo, "status")

    def test_rejects_dirty_indexed_submodule(self) -> None:
        child_source = Path(self.temp.name) / "child-source"
        child_source.mkdir()
        subprocess.run(["git", "init", "-q", str(child_source)], check=True)
        (child_source / "nested.txt").write_text("clean\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(child_source), "add", "nested.txt"], check=True)
        commit_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
        subprocess.run(["git", "-C", str(child_source), "commit", "-qm", "child"], check=True, env=commit_env)
        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "-C", str(self.repo),
             "submodule", "add", "-q", str(child_source), "nested/child"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "submodule"], check=True, env=commit_env)
        (self.repo / "nested" / "child" / "nested.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(runtime_probe.ProbeError, "^dirty_submodule$"):
            runtime_probe.collect(self.repo)

    def test_clean_tracked_fingerprint_uses_git_object_without_reading_worktree(self) -> None:
        with mock.patch.object(
            runtime_probe,
            "_read_path_entry",
            side_effect=AssertionError("clean tracked files must use object ids"),
        ):
            digest = runtime_probe._path_content_digest(
                str(self.repo),
                {b"tracked.txt"},
                tracked_objects={b"tracked.txt": (b"100644", b"a" * 40)},
            )
        self.assertEqual(len(digest), 64)

    def test_dirty_probe_reads_only_changed_tracked_paths(self) -> None:
        large = self.repo / "large-unrelated.bin"
        large.write_bytes(b"x" * 32)
        commit_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
        subprocess.run(["git", "-C", str(self.repo), "add", "large-unrelated.bin"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "large"], check=True, env=commit_env)
        (self.repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        original = runtime_probe._read_path_entry
        calls: list[bytes] = []

        def read_path(*args, **kwargs):
            calls.append(args[1])
            return original(*args, **kwargs)

        with mock.patch.object(runtime_probe, "MAX_TRACKED_FILE_BYTES", 16):
            with mock.patch.object(runtime_probe, "_read_path_entry", side_effect=read_path):
                facts = runtime_probe.collect(self.repo)
        self.assertEqual(facts["dirty_state"]["worktree"], True)
        self.assertEqual(calls, [b"tracked.txt"])

    def test_untracked_probe_limits_count_size_total_and_deadline(self) -> None:
        first = self.repo / "scratch-a.txt"
        second = self.repo / "scratch-b.txt"
        first.write_bytes(b"abcd")
        second.write_bytes(b"efgh")
        paths = {b"scratch-a.txt", b"scratch-b.txt"}

        with mock.patch.object(runtime_probe, "MAX_UNTRACKED_COUNT", 1):
            with self.assertRaisesRegex(runtime_probe.ProbeError, "^untracked_probe_limit$"):
                runtime_probe._path_content_digest(
                    str(self.repo), paths, untracked_paths=paths
                )

        with mock.patch.object(runtime_probe, "MAX_UNTRACKED_FILE_BYTES", 3):
            with self.assertRaisesRegex(runtime_probe.ProbeError, "^untracked_probe_limit$"):
                runtime_probe._path_content_digest(
                    str(self.repo), {b"scratch-a.txt"}, untracked_paths={b"scratch-a.txt"}
                )

        with mock.patch.object(runtime_probe, "MAX_UNTRACKED_TOTAL_BYTES", 7):
            with self.assertRaisesRegex(runtime_probe.ProbeError, "^untracked_probe_limit$"):
                runtime_probe._path_content_digest(
                    str(self.repo), paths, untracked_paths=paths
                )

        with mock.patch.object(runtime_probe.time, "monotonic", side_effect=[100.0, 103.0]):
            with self.assertRaisesRegex(runtime_probe.ProbeError, "^untracked_probe_limit$"):
                runtime_probe._path_content_digest(
                    str(self.repo), {b"scratch-a.txt"}, untracked_paths={b"scratch-a.txt"}
                )

    def test_git_bytes_stream_limit_fails_closed_without_eof_capture(self) -> None:
        fake_bin = Path(self.temp.name) / "fake-git-bin"
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stdout.buffer.write(b'x' * (2 * 1024 * 1024))\n"
            "sys.stdout.buffer.flush()\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}):
            with self.assertRaisesRegex(runtime_probe.ProbeError, "^untracked_probe_limit$"):
                runtime_probe._git_bytes(self.repo, "fake", max_output_bytes=1024)

    def test_git_bytes_timeout_terminates_slow_process(self) -> None:
        fake_bin = Path(self.temp.name) / "slow-git-bin"
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            "time.sleep(1)\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}):
            with mock.patch.object(runtime_probe, "GIT_PROBE_TIMEOUT_SECONDS", 0.05):
                with self.assertRaisesRegex(runtime_probe.ProbeError, "^git_probe_timeout$"):
                    runtime_probe._git_bytes(self.repo, "slow")

    def test_tty_frame_finishes_on_one_newline_without_eof(self) -> None:
        master, slave = os.openpty()
        try:
            os.write(master, b'{"action":"pre_mutation"}\n')
            self.assertEqual(
                runtime_probe._read_tty_frame(slave),
                b'{"action":"pre_mutation"}',
            )
        finally:
            os.close(master)
            os.close(slave)

    def test_tty_frame_rejects_trailing_input_in_the_same_frame(self) -> None:
        master, slave = os.openpty()
        try:
            os.write(master, b"{}\nsecond-frame")
            with self.assertRaisesRegex(runtime_probe.ProbeError, "^trailing_input$"):
                runtime_probe._read_tty_frame(slave)
        finally:
            os.close(master)
            os.close(slave)

    def test_tty_frame_times_out_when_open_frame_has_no_newline(self) -> None:
        master, slave = os.openpty()
        outcome: dict[str, object] = {}

        def read_partial_frame() -> None:
            try:
                with mock.patch.object(
                    runtime_probe, "PTY_FRAME_TIMEOUT_SECONDS", 0.05,
                    create=True,
                ):
                    runtime_probe._read_tty_frame(slave)
            except Exception as error:  # noqa: BLE001 - preserve worker outcome
                outcome["error"] = error

        os.write(master, b"{}")
        reader = threading.Thread(target=read_partial_frame)
        reader.start()
        try:
            reader.join(timeout=0.5)
            completed_before_close = not reader.is_alive()
        finally:
            os.close(master)
            reader.join(timeout=1)
            os.close(slave)
        self.assertTrue(completed_before_close, "PTY read ignored its frame deadline")
        self.assertIsInstance(outcome.get("error"), runtime_probe.ProbeError)
        self.assertEqual(str(outcome["error"]), "input_frame_incomplete")

    def test_tty_frame_rejects_eof_before_newline(self) -> None:
        master, slave = os.openpty()
        try:
            os.write(master, b"x")
            with mock.patch.object(runtime_probe.os, "read", return_value=b""):
                with self.assertRaisesRegex(
                    runtime_probe.ProbeError, "^input_frame_incomplete$"
                ):
                    runtime_probe._read_tty_frame(slave)
        finally:
            os.close(master)
            os.close(slave)

    def test_read_input_rejects_oversized_non_tty_frame(self) -> None:
        stdin = mock.Mock()
        stdin.buffer = io.BytesIO(b"x" * (runtime_probe.MAX_INPUT_BYTES + 1))
        with mock.patch.object(runtime_probe.sys, "stdin", stdin):
            with self.assertRaisesRegex(runtime_probe.ProbeError, "^input_too_large$"):
                runtime_probe._read_input()

    def test_supervisor_context_requires_marker_and_all_fixed_fds(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(runtime_probe._supervisor_context_available())
        with mock.patch.dict(
            os.environ, {runtime_probe.SUPERVISED_ENV: "1"}, clear=True
        ):
            with mock.patch.object(runtime_probe.os, "fstat") as fstat:
                self.assertTrue(runtime_probe._supervisor_context_available())
                self.assertEqual(
                    [call.args[0] for call in fstat.call_args_list],
                    list(runtime_probe.SUPERVISOR_FDS),
                )
            with mock.patch.object(runtime_probe.os, "fstat", side_effect=OSError):
                self.assertFalse(runtime_probe._supervisor_context_available())

    def test_resolve_supervisor_uses_pinned_known_good_fallback(self) -> None:
        runtime_root = Path(self.temp.name) / "runtime-controls"
        runtime_root.mkdir()
        current = runtime_root / "apr_probe_supervisor.py"
        current.write_text("current mismatch\n", encoding="utf-8")
        current.chmod(0o600)
        expected_content = b"reviewed supervisor\n"
        expected_digest = hashlib.sha256(expected_content).hexdigest()
        known_good = (
            runtime_root
            / "releases"
            / "known-good"
            / ("a" * 64)
            / "runtime"
            / current.name
        )
        known_good.parent.mkdir(parents=True)
        known_good.write_bytes(expected_content)
        known_good.chmod(0o600)

        with mock.patch.object(runtime_probe, "INSTALLED_SUPERVISOR", current):
            with mock.patch.object(runtime_probe, "SUPERVISOR_SHA256", expected_digest):
                resolved, descriptor = runtime_probe._resolve_supervisor()
        try:
            self.assertEqual(resolved, known_good.resolve())
        finally:
            os.close(descriptor)

    def test_delegate_executes_verified_supervisor_after_path_replacement(self) -> None:
        runtime_root = Path(self.temp.name) / "runtime-controls"
        runtime_root.mkdir()
        current = runtime_root / "apr_probe_supervisor.py"
        support = runtime_root / "trusted_support.py"
        support.write_text("SOURCE = 'verified'\n", encoding="utf-8")
        support.chmod(0o600)
        verified = (
            b"import json, sys, trusted_support\n"
            b"sys.stdin.buffer.read()\n"
            b"print(json.dumps({'source': trusted_support.SOURCE, 'argv': sys.argv[1:]}))\n"
        )
        replacement = b"print('{\"source\": \"replacement\"}')\n"
        current.write_bytes(verified)
        current.chmod(0o600)
        expected_digest = hashlib.sha256(verified).hexdigest()
        real_run = subprocess.run
        stdout = mock.Mock()
        stdout.buffer = io.BytesIO()
        stderr = mock.Mock()
        stderr.buffer = io.BytesIO()
        attack_dir = Path(self.temp.name) / "attacker-controlled-worktree"
        attack_dir.mkdir()
        (attack_dir / "sitecustomize.py").write_text(
            "print('attacker-sitecustomize-loaded')\n", encoding="utf-8"
        )

        def replace_then_run(
            *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            command = args[0]
            self.assertEqual(command[1:4], ("-I", "-S", "-B"))
            self.assertFalse(
                any(key.startswith("PYTHON") for key in kwargs["env"])
            )
            current.write_bytes(replacement)
            current.chmod(0o600)
            return real_run(*args, **kwargs)

        with mock.patch.dict(os.environ, {"PYTHONPATH": str(attack_dir)}):
            with mock.patch.object(runtime_probe.Path, "cwd", return_value=attack_dir):
                with mock.patch.object(runtime_probe, "INSTALLED_SUPERVISOR", current):
                    with mock.patch.object(runtime_probe, "SUPERVISOR_SHA256", expected_digest):
                        with mock.patch.object(
                            runtime_probe.subprocess, "run", side_effect=replace_then_run
                        ):
                            with mock.patch.object(runtime_probe.sys, "stdout", stdout):
                                with mock.patch.object(runtime_probe.sys, "stderr", stderr):
                                    with self.assertRaisesRegex(SystemExit, "^0$"):
                                        runtime_probe._delegate_to_supervisor(b"{}")
        self.assertEqual(
            json.loads(stdout.buffer.getvalue()),
            {"source": "verified", "argv": ["--discover"]},
        )

    def test_trusted_supervisor_rejects_writable_file_and_symlink(self) -> None:
        supervisor = Path(self.temp.name) / "supervisor.py"
        supervisor.write_text("reviewed\n", encoding="utf-8")
        supervisor.chmod(0o620)
        with self.assertRaisesRegex(OSError, "^unsafe supervisor$"):
            runtime_probe._trusted_supervisor_digest(supervisor)

        supervisor.chmod(0o600)
        link = Path(self.temp.name) / "supervisor-link.py"
        link.symlink_to(supervisor)
        with self.assertRaises(OSError):
            runtime_probe._trusted_supervisor_digest(link)

    def test_release_argument_emits_no_input_evidence(self) -> None:
        with mock.patch.object(
            runtime_probe, "_emit", side_effect=SystemExit(0)
        ) as emit:
            with self.assertRaisesRegex(SystemExit, "^0$"):
                runtime_probe.main([runtime_probe.OWNER_RELEASE_ARGUMENT])
        emit.assert_called_once_with(
            {"decision": "evidence", "code": "apr_owner_release_requested"}, 0
        )

    def test_unknown_argument_fails_closed_before_reading_input(self) -> None:
        with mock.patch.object(
            runtime_probe, "_emit", side_effect=SystemExit(2)
        ) as emit:
            with mock.patch.object(runtime_probe, "_read_input") as read_input:
                with self.assertRaisesRegex(SystemExit, "^2$"):
                    runtime_probe.main(["--unknown"])
        emit.assert_called_once_with(
            {"decision": "block", "code": "invalid_arguments"}, 2
        )
        read_input.assert_not_called()

    def test_managed_worktree_argument_requires_exact_physical_cwd(self) -> None:
        with mock.patch.object(
            runtime_probe, "_emit", side_effect=SystemExit(2)
        ) as emit:
            with self.assertRaisesRegex(SystemExit, "^2$"):
                runtime_probe.main(
                    [runtime_probe.MANAGED_WORKTREE_ARGUMENT, str(self.repo.resolve())]
                )
        emit.assert_called_once_with(
            {
                "decision": "block",
                "code": "managed_worktree_argument_cwd_mismatch",
            },
            2,
        )

    def test_managed_worktree_argument_rejects_intermediate_symlink(self) -> None:
        real_parent = Path(self.temp.name) / "real"
        worktree = real_parent / "managed"
        worktree.mkdir(parents=True)
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(real_parent, target_is_directory=True)
        routed = alias / "managed"
        with mock.patch.object(runtime_probe.Path, "cwd", return_value=worktree):
            with mock.patch.object(
                runtime_probe, "_emit", side_effect=SystemExit(2)
            ) as emit:
                with mock.patch.object(runtime_probe, "_read_input") as read_input:
                    with self.assertRaisesRegex(SystemExit, "^2$"):
                        runtime_probe.main(
                            [runtime_probe.MANAGED_WORKTREE_ARGUMENT, str(routed)]
                        )
        emit.assert_called_once_with(
            {"decision": "block", "code": "invalid_managed_worktree_argument"},
            2,
        )
        read_input.assert_not_called()

    def test_valid_managed_route_delegates_when_host_context_is_absent(self) -> None:
        with mock.patch.object(runtime_probe.Path, "cwd", return_value=self.repo):
            with mock.patch.object(runtime_probe, "_is_installed_probe", return_value=True):
                with mock.patch.object(runtime_probe, "_read_input", return_value=b"{}"):
                    with mock.patch.object(
                        runtime_probe, "_supervisor_context_available", return_value=False
                    ):
                        with mock.patch.object(
                            runtime_probe, "_delegate_to_supervisor", side_effect=SystemExit(7)
                        ) as delegate:
                            with self.assertRaisesRegex(SystemExit, "^7$"):
                                runtime_probe.main(
                                    [
                                        runtime_probe.MANAGED_WORKTREE_ARGUMENT,
                                        str(self.repo.resolve()),
                                    ]
                                )
        delegate.assert_called_once_with(b"{}")


if __name__ == "__main__":
    unittest.main()
