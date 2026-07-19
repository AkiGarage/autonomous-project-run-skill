#!/usr/bin/env python3

import json
import fcntl
import hashlib
import hmac
import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "skills/autonomous-project-run/scripts/runtime_gate.py"
sys_path = str(GATE.parent)
import sys
sys.path.insert(0, sys_path)
import runtime_gate  # noqa: E402
DIGEST = "a" * 64
SUPERVISOR_CAPABILITY_FD = runtime_gate.SUPERVISOR_CAPABILITY_FD
SUPERVISOR_KEY_FD = runtime_gate.SUPERVISOR_KEY_FD
SUPERVISOR_LOCK_FD = runtime_gate.SUPERVISOR_LOCK_FD
SUPERVISOR_KEY = b"apr-test-supervisor-key-0123456789"
SUPERVISOR_FIXTURES_BY_LOCK: dict[str, dict[str, object]] = {}
SUPERVISOR_FIXTURES_BY_PROJECT: dict[str, dict[str, object]] = {}


def canonical_hash(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _supervisor_fixture_for(payload: dict[str, object]) -> dict[str, object] | None:
    lease = payload.get("lease")
    if isinstance(lease, dict) and isinstance(lease.get("owner_lock_path"), str):
        fixture = SUPERVISOR_FIXTURES_BY_LOCK.get(lease["owner_lock_path"])
        if fixture is not None:
            return fixture
    packet = payload.get("packet")
    if isinstance(packet, dict) and isinstance(packet.get("project_root"), str):
        return SUPERVISOR_FIXTURES_BY_PROJECT.get(packet["project_root"])
    return None


def _write_pipe(fd: int, value: bytes) -> None:
    written = 0
    while written < len(value):
        written += os.write(fd, value[written:])


def _install_supervisor_streams(
    fixture: dict[str, object], lock_stream: object, *, tamper: bool,
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
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    mac = hmac.new(SUPERVISOR_KEY, canonical, hashlib.sha256).hexdigest()
    capability = {**unsigned, "mac": "0" * 64 if tamper else mac}
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


def run_gate(
    payload: dict[str, object] | bytes,
    *,
    lock_held: bool = False,
    inherited_fd: bool = False,
    competing: bool = False,
    tamper_capability: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    if not isinstance(payload, dict):
        return subprocess.run(["python3", str(GATE)], input=raw, capture_output=True, check=False)
    fixture = _supervisor_fixture_for(payload)
    if fixture is None:
        return subprocess.run(["python3", str(GATE)], input=raw, capture_output=True, check=False)
    stream = None
    holder = None
    capability_read = None
    key_read = None
    lock_path = str(fixture["lock_path"])
    owner_bound_actions = {
        "pre_mutation", "validate_pre_mutation_evidence", "checkpoint", "handoff",
        "checkpoint_handoff", "validate_handoff", "luna_bootstrap",
    }
    should_inherit = payload.get("action") in owner_bound_actions or inherited_fd
    try:
        if competing:
            holder = open(lock_path, "a+b")
            os.chmod(lock_path, 0o600)
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            stream = open(lock_path, "a+b")
        elif lock_held or should_inherit:
            stream = open(lock_path, "a+b")
            os.chmod(lock_path, 0o600)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if stream is None:
            return subprocess.run(["python3", str(GATE)], input=raw, capture_output=True, check=False)
        capability_read, key_read = _install_supervisor_streams(
            fixture, stream, tamper=tamper_capability,
        )
        saved = {
            SUPERVISOR_CAPABILITY_FD: _replace_fixed_fd(SUPERVISOR_CAPABILITY_FD, capability_read),
            SUPERVISOR_KEY_FD: _replace_fixed_fd(SUPERVISOR_KEY_FD, key_read),
            SUPERVISOR_LOCK_FD: _replace_fixed_fd(SUPERVISOR_LOCK_FD, stream.fileno()),
        }
        kwargs: dict[str, object] = {}
        kwargs["pass_fds"] = (SUPERVISOR_CAPABILITY_FD, SUPERVISOR_KEY_FD, SUPERVISOR_LOCK_FD)
        try:
            return subprocess.run(
                ["python3", str(GATE)], input=raw,
                capture_output=True, check=False, **kwargs,
            )
        finally:
            for target, previous in saved.items():
                _restore_fixed_fd(target, previous)
    except OSError:
        return subprocess.run(["python3", str(GATE)], input=raw, capture_output=True, check=False)
    finally:
        for descriptor in (capability_read, key_read):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if stream is not None:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            stream.close()
        if holder is not None:
            try:
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            holder.close()


def decision(
    payload: dict[str, object] | bytes, *, lock_held: bool = False, inherited_fd: bool = False,
    competing: bool = False, tamper_capability: bool = False,
) -> dict[str, object]:
    result = run_gate(
        payload, lock_held=lock_held, inherited_fd=inherited_fd,
        competing=competing, tamper_capability=tamper_capability,
    )
    return json.loads(result.stdout)


def pre_record(root: Path, **overrides: object) -> dict[str, object]:
    worktree = root / ".codex" / "worktrees" / "ticket-1"
    state_path = Path(tempfile.gettempdir()) / (
        f"apr-runtime-state-{os.getpid()}-{hashlib.sha256(str(root).encode()).hexdigest()[:16]}.json"
    )
    lock_path = Path(tempfile.gettempdir()) / (
        f"apr-owner-lock-{os.getpid()}-{hashlib.sha256(str(root).encode()).hexdigest()[:16]}.lock"
    )
    record: dict[str, object] = {
        "action": "pre_mutation",
        "project_root": str(root),
        "cwd": str(worktree),
        "worktree": str(worktree),
        "repo_identity": "repo-1",
        "branch": "codex/ticket-1",
        "head": "deadbeef",
        "requirements_digest": DIGEST,
        "role": "implementation",
        "is_bare": False,
        "authority": {"confirmed": True, "scope": "ticket-1"},
        "base": "main",
        "spec_revision": "r1",
        "spec_digest": "2" * 64,
        "source_fingerprint": "3" * 64,
        "dependency_fingerprint": "4" * 64,
        "toolchain_fingerprint": "5" * 64,
        "owner": "owner-1",
        "lease": {
            "id": "lease-1", "owner": "owner-1", "generation": 1, "active": True,
            "expires": int(time.time()) + 3600,
            "runtime_state_path": str(state_path), "owner_lock_path": str(lock_path),
            "fencing_token": "fence-1",
        },
        "checkpoint": {
            "generation": 1,
            "head": "deadbeef",
            "source_fingerprint": "3" * 64,
            "requirements_digest": DIGEST,
        },
        "dirty_state": {"index": False, "worktree": False, "untracked": False},
        "fingerprints": {
            "repo": "6" * 64, "project": "7" * 64, "worktree": "8" * 64,
            "head": "9" * 64, "index": "a" * 64, "tree": "b" * 64,
            "untracked": "c" * 64, "content": "d" * 64, "source": "3" * 64, "requirements": DIGEST,
            "spec": "2" * 64, "dependencies": "4" * 64, "toolchain": "5" * 64,
        },
        "pending_side_effects": [],
        "unknown_outcomes": [],
    }
    record.update(overrides)
    # The production gate requires a durable, owner-only evidence file.  Test
    # fixtures write one from the final record so path-focused cases still
    # exercise their intended gate rather than an unrelated evidence mismatch.
    project_value = record.get("project_root")
    if isinstance(project_value, str) and project_value and os.path.isabs(project_value):
        write_owner_evidence(record)
    else:
        write_owner_evidence({**record, "project_root": str(root), "worktree": str(worktree), "cwd": str(worktree)})
    return record


def write_owner_evidence(record: dict[str, object]) -> None:
    project = Path(str(record["project_root"]))
    worktree = Path(str(record["worktree"]))
    project.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)
    common_dir = Path(str(record.get("common_dir", project / ".git")))
    common_dir.mkdir(parents=True, exist_ok=True)
    authority = record["authority"]
    assert isinstance(authority, dict)
    lease = record["lease"]
    assert isinstance(lease, dict)
    checkpoint = record["checkpoint"]
    assert isinstance(checkpoint, dict)
    expires = int(lease.get("expires", int(time.time()) + 3600))
    runtime_state_path = str(lease.get(
        "runtime_state_path",
        Path(tempfile.gettempdir()) / f"apr-runtime-state-{os.getpid()}-fallback.json",
    ))
    fencing_token = str(lease.get("fencing_token", "fence-1"))
    owner_lock_path = str(lease.get(
        "owner_lock_path",
        Path(tempfile.gettempdir()) / f"apr-owner-lock-{os.getpid()}-fallback.lock",
    ))
    lock_path = Path(owner_lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    os.chmod(lock_path, 0o600)
    evidence: dict[str, object] = {
        "schema_version": 1,
        "owner": record["owner"],
        "scope": authority["scope"],
        "generation": lease["generation"],
        "lease_id": lease["id"],
        "project_root": str(project),
        "worktree": str(worktree),
        "repo_identity": record["repo_identity"],
        "common_dir": str(common_dir),
        "branch": record["branch"],
        "base": record["base"],
        "head": record["head"],
        "requirements_digest": record["requirements_digest"],
        "spec_revision": record["spec_revision"],
        "spec_digest": record["spec_digest"],
        "source_fingerprint": record["source_fingerprint"],
        "dependency_fingerprint": record["dependency_fingerprint"],
        "toolchain_fingerprint": record["toolchain_fingerprint"],
        "checkpoint_fingerprint": checkpoint["source_fingerprint"],
        "dirty_state": record["dirty_state"],
        "fingerprints": record["fingerprints"],
        "runtime_state_path": runtime_state_path,
        "owner_lock_path": owner_lock_path,
        "fencing_token": fencing_token,
        "lease_expires": expires,
    }
    evidence["hash"] = canonical_hash(evidence)
    path = project / ".codex" / "owner-evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
    os.chmod(path, 0o600)
    record["owner_evidence_path"] = str(path)
    state = {
        "schema_version": 1,
        "project_root": str(project),
        "worktree": str(worktree),
        "repo_identity": record["repo_identity"],
        "owner": record["owner"],
        "scope": authority["scope"],
        "generation": lease["generation"],
        "lease_id": lease["id"],
        "fencing_token": fencing_token,
        "lease_expires": expires,
        "owner_lock_path": owner_lock_path,
        "owner_evidence_hash": evidence["hash"],
    }
    state["hash"] = canonical_hash(state)
    state_path = Path(runtime_state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.chmod(state_path, 0o600)
    repo_identity = record["repo_identity"]
    if isinstance(repo_identity, dict):
        repo_identity = repo_identity.get("identity")
    assert isinstance(repo_identity, str)
    fixture = {
        "project_root": str(project),
        "worktree": str(worktree),
        "repo_identity": repo_identity,
        "owner": record["owner"],
        "scope": authority["scope"],
        "generation": lease["generation"],
        "lease_id": lease["id"],
        "fencing_token": fencing_token,
        "lease_expires": expires,
        "lock_path": owner_lock_path,
    }
    SUPERVISOR_FIXTURES_BY_LOCK[owner_lock_path] = fixture
    SUPERVISOR_FIXTURES_BY_PROJECT[str(project)] = fixture


def handoff_record(**overrides: object) -> dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix=f"apr-handoff-{os.getpid()}-"))
    project = root / "project"
    worktree = project / ".codex" / "worktrees" / "ticket-1"
    common_dir = project / ".git"
    project.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)
    common_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = "b" * 64
    state_path = root / "runtime-state.json"
    lock_path = root / "owner.lock"
    lock_path.touch()
    os.chmod(lock_path, 0o600)
    full_fingerprints = {
        "repo": DIGEST,
        "project": fingerprint,
        "worktree": "c" * 64,
        "head": "d" * 64,
        "index": "e" * 64,
        "tree": "f" * 64,
        "untracked": "1" * 64,
        "content": "2" * 64,
        "source": "d" * 64,
        "requirements": DIGEST,
        "spec": "2" * 64,
        "dependencies": "3" * 64,
        "toolchain": "4" * 64,
    }
    handoff_payload = {
        "schema_version": 1,
        "generation": 1,
        "owner": "owner-1",
        "ticket": "ticket-1",
        "phase": "implementation",
        "project_root": str(project),
        "cwd": str(worktree),
        "worktree": str(worktree),
        "repo_identity": "repo-1",
        "common_dir": str(common_dir),
        "branch": "codex/ticket-1",
        "base": "main",
        "head": "deadbeef",
        "requirements_digest": DIGEST,
        "spec_revision": "r1",
        "spec_digest": "2" * 64,
        "source_fingerprint": "d" * 64,
        "dependency_fingerprint": "3" * 64,
        "toolchain_fingerprint": "4" * 64,
        "checkpoint_fingerprint": "d" * 64,
        "dirty_state": {"index": False, "worktree": False, "untracked": False},
        "fingerprints": full_fingerprints,
        "lease": {
            "id": "lease-1", "owner": "owner-1", "generation": 1, "active": True,
            "expires": int(time.time()) + 3600, "runtime_state_path": str(state_path),
            "owner_lock_path": str(lock_path), "fencing_token": "fence-1",
        },
        "process": {"pid": os.getpid(), "state": "running"},
        "lock": {"path": str(lock_path), "held": True, "fencing_token": "fence-1"},
        "pr": [],
        "ci": [],
        "review": [],
        "unconfirmed": ["UNCONFIRMED: no remote review evidence"],
        "validation": [],
        "negative_evidence": [],
        "next_action": "resume-ticket-1",
    }
    record: dict[str, object] = {
        "action": "handoff",
        "schema_version": 1,
        "project_root": str(project),
        "cwd": str(worktree),
        "worktree": str(worktree),
        "repo_identity": "repo-1",
        "common_dir": str(common_dir),
        "branch": "codex/ticket-1",
        "base": "main",
        "head": "deadbeef",
        "requirements_digest": DIGEST,
        "spec_revision": "r1",
        "spec_digest": "2" * 64,
        "fingerprints": full_fingerprints,
        "source_fingerprint": "d" * 64,
        "dependency_fingerprint": "3" * 64,
        "toolchain_fingerprint": "4" * 64,
        "checkpoint_fingerprint": "d" * 64,
        "owner": "owner-1",
        "lease": handoff_payload["lease"],
        "ticket": "ticket-1",
        "phase": "implementation",
        "dirty_state": {"index": False, "worktree": False, "untracked": False},
        "generation": 1,
        "handoff_payload": handoff_payload,
        "successors": ["ticket-2"],
        "successor_count": 1,
        "compaction_count": 1,
        "handoff_trigger": {
            "reason": "ineffective_compaction",
            "evidence": {"source": "p0_telemetry", "reduction": "unknown"},
            "safe_checkpoint": True,
        },
        "authority": {"confirmed": True, "scope": "ticket-1"},
        "checkpoint": {
            "generation": 1, "head": "deadbeef", "source_fingerprint": "d" * 64,
            "requirements_digest": DIGEST,
        },
    }
    write_owner_evidence(record)
    record.pop("authority")
    record.pop("checkpoint")
    handoff_hash = canonical_hash(handoff_payload)
    record["hash"] = handoff_hash
    record["acknowledgement"] = {
        "state": "acknowledged", "owner": "owner-1", "generation": 1,
        "successor": "ticket-2", "handoff_hash": handoff_hash,
    }
    record.update(overrides)
    return record


def luna_packet(**overrides: object) -> dict[str, object]:
    handoff_path = Path(tempfile.gettempdir()) / f"apr-luna-test-{os.getpid()}.json"
    packet: dict[str, object] = {
        "model": "gpt-5.6-luna",
        "reasoning": "xhigh",
        "fork_turns": "none",
        "objective": "Implement one bounded task",
        "role": "executor",
        "owner": "ticket-1",
        "ticket": "ticket-1",
        "phase": "implementation",
        "project_root": "/repo",
        "cwd": "/repo/.codex/worktrees/ticket-1",
        "worktree": "/repo/.codex/worktrees/ticket-1",
        "repo_identity": {"identity": "repo-1", "is_bare": False, "common_dir": "/repo/.git"},
        "branch": "codex/ticket-1",
        "base": "main",
        "head": "deadbeef",
        "tree": "tree-1",
        "acceptance": ["Tests pass"],
        "paths": ["src/example.py", "tests/test_example.py"],
        "read_scope": ["src/example.py", "tests/test_example.py"],
        "write_scope": ["src/example.py"],
        "verification": ["python3 -m unittest"],
        "constraints": ["Do not modify unrelated files"],
        "requirements_digest": DIGEST,
        "spec_revision": "r1",
        "spec_digest": "b" * 64,
        "source_fingerprint": "c" * 64,
        "dependency_fingerprint": "d" * 64,
        "toolchain_fingerprint": "e" * 64,
        "artifact_fingerprint": "f" * 64,
        "checkpoint_fingerprint": "1" * 64,
        "output_cap": 6000,
        "citation_format": "path:line plus source digest",
        "hash_format": "sha256",
        "handoff_path": str(handoff_path),
        "generation": 1,
    }
    packet.update(overrides)
    handoff = {
        "schema_version": 1,
        "generation": packet["generation"],
        "owner": packet["owner"],
        "ticket": packet["ticket"],
        "phase": packet["phase"],
        "project_root": packet["project_root"],
        "worktree": packet["worktree"],
        "head": packet["head"],
        "source_fingerprint": packet["source_fingerprint"],
        "checkpoint_fingerprint": packet["checkpoint_fingerprint"],
        "requirements_digest": packet["requirements_digest"],
        "packet_fingerprint": canonical_hash(packet),
    }
    handoff["hash"] = canonical_hash(handoff)
    handoff_path = Path(str(packet["handoff_path"]))
    handoff_path.write_text(json.dumps(handoff, sort_keys=True), encoding="utf-8")
    os.chmod(handoff_path, 0o600)
    repo_identity = packet["repo_identity"]
    if isinstance(repo_identity, dict):
        repo_identity = repo_identity.get("identity")
    lock_path = Path(tempfile.gettempdir()) / (
        f"apr-luna-supervisor-{os.getpid()}-"
        f"{hashlib.sha256(str(packet['project_root']).encode()).hexdigest()[:16]}.lock"
    )
    lock_path.touch(exist_ok=True)
    os.chmod(lock_path, 0o600)
    scope = next(
        (
            packet[key]
            for key in ("task", "task_id", "ticket")
            if packet.get(key) not in (None, "")
        ),
        None,
    )
    fixture = {
        "project_root": packet["project_root"],
        "worktree": packet["worktree"],
        "repo_identity": repo_identity,
        "owner": packet["owner"],
        "scope": scope,
        "generation": packet["generation"],
        "lease_id": f"luna-{packet['ticket']}",
        "fencing_token": f"luna-fence-{packet['ticket']}",
        "lease_expires": int(time.time()) + 3600,
        "lock_path": str(lock_path),
    }
    SUPERVISOR_FIXTURES_BY_PROJECT[str(packet["project_root"])] = fixture
    return {"action": "luna_bootstrap", "packet": packet}


class RuntimeGateTests(unittest.TestCase):
    def test_direct_pre_mutation_requires_global_host_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_gate(pre_record(Path(temporary)))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(decision(pre_record(Path(temporary)))["code"], "host_event_required")

    def test_pre_mutation_compatibility_evidence_is_not_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = pre_record(Path(temporary))
            record["action"] = "validate_pre_mutation_evidence"
            result = run_gate(record)
            output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output["decision"], "evidence")
        self.assertEqual(output["code"], "pre_mutation_evidence_valid")

    def test_pre_mutation_requires_boolean_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = pre_record(Path(temporary))
            record["dirty_state"] = {
                "index": "a" * 64,
                "worktree": False,
                "untracked": False,
            }
            result = decision(record)
        self.assertEqual(result["code"], "invalid_dirty_state")

    def test_owner_runtime_lease_is_singleton_fenced_and_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = pre_record(root)
            self.assertEqual(decision(record)["code"], "host_event_required")
            self.assertEqual(
                decision(record, inherited_fd=True, competing=True)["code"],
                "owner_lock_not_held",
            )

            expired = pre_record(root)
            expired["lease"]["expires"] = int(time.time()) - 1
            write_owner_evidence(expired)
            self.assertEqual(decision(expired)["code"], "expired_lease")

    def test_inherited_owner_lock_survives_gate_for_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = pre_record(root)
            lock_path = Path(str(record["lease"]["owner_lock_path"]))
            stream = open(lock_path, "a+b")
            capability_read = key_read = None
            saved: dict[int, int | None] = {}
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fixture = _supervisor_fixture_for(record)
                assert fixture is not None
                capability_read, key_read = _install_supervisor_streams(
                    fixture, stream, tamper=False,
                )
                saved = {
                    SUPERVISOR_CAPABILITY_FD: _replace_fixed_fd(SUPERVISOR_CAPABILITY_FD, capability_read),
                    SUPERVISOR_KEY_FD: _replace_fixed_fd(SUPERVISOR_KEY_FD, key_read),
                    SUPERVISOR_LOCK_FD: _replace_fixed_fd(SUPERVISOR_LOCK_FD, stream.fileno()),
                }
                result = subprocess.run(
                    ["python3", str(GATE)], input=json.dumps(record).encode(),
                    capture_output=True, check=False,
                    pass_fds=(SUPERVISOR_CAPABILITY_FD, SUPERVISOR_KEY_FD, SUPERVISOR_LOCK_FD),
                )
                output = json.loads(result.stdout)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(output["code"], "host_event_required")

                contender = open(lock_path, "a+b")
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    contender.close()
            finally:
                for target, previous in saved.items():
                    _restore_fixed_fd(target, previous)
                for descriptor in (capability_read, key_read):
                    if descriptor is not None:
                        os.close(descriptor)
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                stream.close()

            contender = open(lock_path, "a+b")
            try:
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                fcntl.flock(contender.fileno(), fcntl.LOCK_UN)
                contender.close()

    def test_missing_supervisor_contract_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = pre_record(Path(temporary))
            result = subprocess.run(
                ["python3", str(GATE)],
                input=json.dumps(record).encode(),
                capture_output=True,
                check=False,
                close_fds=True,
            )
        self.assertEqual(json.loads(result.stdout)["code"], "supervisor_unavailable")

    def test_supervisor_capability_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = pre_record(Path(temporary))
            result = decision(record, tamper_capability=True)
        self.assertEqual(result["code"], "supervisor_capability_invalid")

    def test_supervisor_stream_reads_split_chunks_until_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stream"
            os.mkfifo(path, 0o600)
            read_fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            write_fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            first_chunk_written = threading.Event()
            writer_errors: list[BaseException] = []

            def write_chunks() -> None:
                try:
                    os.write(write_fd, b"abc")
                    first_chunk_written.set()
                    time.sleep(0.01)
                    os.write(write_fd, b"def")
                except BaseException as error:
                    writer_errors.append(error)
                    first_chunk_written.set()
                finally:
                    os.close(write_fd)

            writer = threading.Thread(target=write_chunks)
            writer.start()
            try:
                self.assertTrue(first_chunk_written.wait(timeout=2))
                self.assertEqual(writer_errors, [])
                # Public-surface validation runs this suite repeatedly. Give the
                # second chunk enough scheduling margin under load; the separate
                # timeout test keeps the production bound covered.
                with mock.patch.object(runtime_gate, "SUPERVISOR_STREAM_TIMEOUT_SECONDS", 10.0):
                    self.assertEqual(
                        runtime_gate._read_supervisor_stream(read_fd, 16, "stream_invalid"),
                        b"abcdef",
                    )
            finally:
                os.close(read_fd)
                writer.join(timeout=1)
            self.assertFalse(writer.is_alive())
            self.assertEqual(writer_errors, [])

    def test_supervisor_stream_split_read_replay_is_stable(self) -> None:
        for attempt in range(40):
            with self.subTest(attempt=attempt), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "stream"
                os.mkfifo(path, 0o600)
                read_fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                write_fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)

                def write_chunks() -> None:
                    try:
                        os.write(write_fd, b"abc")
                        time.sleep(0.001)
                        os.write(write_fd, b"def")
                    finally:
                        os.close(write_fd)

                writer = threading.Thread(target=write_chunks)
                writer.start()
                try:
                    with mock.patch.object(runtime_gate, "SUPERVISOR_STREAM_TIMEOUT_SECONDS", 1.0):
                        self.assertEqual(
                            runtime_gate._read_supervisor_stream(read_fd, 16, "stream_invalid"),
                            b"abcdef",
                        )
                finally:
                    os.close(read_fd)
                    writer.join(timeout=1)
                self.assertFalse(writer.is_alive())

    def test_supervisor_stream_rejects_oversized_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stream"
            os.mkfifo(path, 0o600)
            read_fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            write_fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(write_fd, b"x" * 9)
                os.close(write_fd)
                with self.assertRaisesRegex(runtime_gate.GateError, "^stream_invalid$"):
                    runtime_gate._read_supervisor_stream(read_fd, 8, "stream_invalid")
            finally:
                os.close(read_fd)

    def test_supervisor_stream_timeout_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stream"
            os.mkfifo(path, 0o600)
            read_fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            write_fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            try:
                with mock.patch.object(runtime_gate, "SUPERVISOR_STREAM_TIMEOUT_SECONDS", 0.01):
                    with self.assertRaisesRegex(runtime_gate.GateError, "^stream_invalid$"):
                        runtime_gate._read_supervisor_stream(read_fd, 16, "stream_invalid")
            finally:
                os.close(read_fd)
                os.close(write_fd)

    def test_caller_owner_lock_descriptor_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = pre_record(Path(temporary))
            lease = record["lease"]
            assert isinstance(lease, dict)
            lease["owner_lock_fd"] = 999999
            result = decision(record)
        self.assertEqual(result["code"], "host_event_required")

    def test_owner_evidence_is_parsed_from_the_validated_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = pre_record(root)
            evidence_path = Path(str(record["owner_evidence_path"]))
            original_open = runtime_gate.os.open

            def replace_after_open(path: str, flags: int, *args: object) -> int:
                descriptor = original_open(path, flags, *args)
                if os.path.realpath(path) == os.path.realpath(evidence_path):
                    replacement = {"schema_version": 1, "owner": "spoofed"}
                    replacement_path = evidence_path.with_name("owner-evidence-replacement.json")
                    replacement_path.write_text(json.dumps(replacement), encoding="utf-8")
                    os.chmod(replacement_path, 0o600)
                    os.replace(replacement_path, evidence_path)
                return descriptor

            with mock.patch.object(runtime_gate.os, "open", side_effect=replace_after_open):
                evidence = runtime_gate.load_owner_evidence(str(evidence_path), str(root))
            self.assertEqual(evidence["owner"], "owner-1")

    def test_owner_runtime_state_rewrite_cannot_authorize_or_revoke(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = pre_record(root)
            state_path = Path(str(record["lease"]["runtime_state_path"]))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["owner"] = "other-owner"
            state["hash"] = canonical_hash({key: value for key, value in state.items() if key != "hash"})
            state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
            self.assertEqual(decision(record)["code"], "owner_runtime_state_mismatch")

    def test_pre_mutation_requires_durable_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = pre_record(Path(temporary))
            state_path = Path(str(record["lease"]["runtime_state_path"]))
            state_path.unlink()
            self.assertEqual(decision(record)["code"], "owner_runtime_state_missing")

    def test_checkpoint_requires_durable_runtime_state(self) -> None:
        record = handoff_record()
        state_path = Path(str(record["lease"]["runtime_state_path"]))
        state_path.unlink()
        self.assertEqual(decision(record)["code"], "owner_runtime_state_missing")

    def test_durable_runtime_state_expiry_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = pre_record(Path(temporary))
            state_path = Path(str(record["lease"]["runtime_state_path"]))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["lease_expires"] = int(time.time()) - 1
            state["hash"] = canonical_hash({key: value for key, value in state.items() if key != "hash"})
            state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
            self.assertEqual(decision(record)["code"], "expired_lease")

    def test_huge_integer_in_owner_evidence_or_runtime_state_fails_closed(self) -> None:
        huge_integer = b"9" * 5000
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = pre_record(root)
            evidence_path = Path(str(record["owner_evidence_path"]))
            evidence_path.write_bytes(b'{"generation":' + huge_integer + b"}")
            self.assertEqual(decision(record)["code"], "invalid_owner_evidence")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = pre_record(root)
            state_path = Path(str(record["lease"]["runtime_state_path"]))
            state_path.write_bytes(b'{"generation":' + huge_integer + b"}")
            self.assertEqual(decision(record)["code"], "invalid_runtime_state")

    def test_kairos_bare_root_is_always_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo.git"
            record = pre_record(root, is_bare=True)
            blocked = decision(record)
            self.assertEqual(blocked, {"decision": "block", "code": "bare_worktree"})
            target = root / ".codex" / "worktrees" / "ticket-1"
            blocked_prepare = decision(
                pre_record(root, is_bare=True, operation="prepare_worktree", target_worktree=str(target))
            )
            self.assertEqual(blocked_prepare["code"], "bare_worktree")

    def test_same_common_dir_external_worktree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "project"
            external = base / "other-worktree"
            result = decision(pre_record(root, worktree=str(external), cwd=str(external)))
            self.assertEqual(result["code"], "external_worktree")

    def test_linked_project_accepts_external_common_dir_bound_to_repo_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "linked-project"
            common_dir = base / "physical-repository" / ".git"
            identity = hashlib.sha256(
                f"{project.resolve()}\0{common_dir.resolve()}".encode()
            ).hexdigest()
            result = decision(pre_record(
                project,
                action="validate_pre_mutation_evidence",
                repo_identity=identity,
                common_dir=str(common_dir),
            ))

            self.assertEqual(result["code"], "pre_mutation_evidence_valid")

    def test_linked_project_rejects_external_common_dir_not_bound_to_repo_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "linked-project"
            common_dir = base / "unrelated" / ".git"
            result = decision(pre_record(
                project,
                action="validate_pre_mutation_evidence",
                repo_identity="6" * 64,
                common_dir=str(common_dir),
            ))

            self.assertEqual(result["code"], "repo_mismatch")

    def test_nested_managed_worktree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            nested = root / ".codex" / "worktrees" / "ticket-1" / "nested"
            result = decision(pre_record(root, worktree=str(nested), cwd=str(nested)))
            self.assertEqual(result["code"], "external_worktree")
            prepared = decision(pre_record(
                root,
                is_bare=True,
                operation="prepare_worktree",
                target_worktree=str(nested),
            ))
            self.assertEqual(prepared["code"], "bare_worktree")

    def test_projectless_and_cwd_mismatch_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            self.assertEqual(decision(pre_record(root, project_root=""))["code"], "projectless")
            self.assertEqual(decision(pre_record(root, cwd=str(root)))["code"], "cwd_mismatch")

    def test_main_checkout_mutation_is_blocked_but_preparation_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            main_checkout = pre_record(root, worktree=str(root), cwd=str(root))
            self.assertEqual(decision(main_checkout)["code"], "main_checkout_mutation")

            target = root / ".codex" / "worktrees" / "ticket-1"
            prepared = pre_record(
                root,
                action="validate_pre_mutation_evidence",
                operation="prepare_worktree",
                worktree=str(root),
                cwd=str(root),
                target_worktree=str(target),
            )
            self.assertEqual(decision(prepared)["code"], "pre_mutation_evidence_valid")

            managed = pre_record(root, action="validate_pre_mutation_evidence")
            self.assertEqual(decision(managed)["code"], "pre_mutation_evidence_valid")

    def test_checkpoint_rejects_compaction_staleness_duplicates_and_context(self) -> None:
        self.assertEqual(decision(handoff_record(compaction_count=2))["decision"], "evidence")
        self.assertEqual(decision(handoff_record(stale=True))["code"], "stale_checkpoint")
        self.assertEqual(decision(handoff_record(schema_version=True))["code"], "unsupported_schema")
        self.assertEqual(decision(handoff_record(compactions={"other": 1}))["code"], "invalid_compactions")
        per_owner = handoff_record()
        per_owner.pop("compaction_count")
        per_owner["compactions"] = {"root": 0, "implementation": 2}
        self.assertEqual(decision(per_owner)["decision"], "evidence")
        ambiguous = handoff_record()
        ambiguous.pop("compaction_count")
        ambiguous["compactions"] = {"root": 0, "primary": 0}
        self.assertEqual(decision(ambiguous)["code"], "invalid_compactions")
        self.assertEqual(decision(handoff_record(successors="ticket-2"))["code"], "invalid_successors")
        self.assertEqual(decision(handoff_record(successor_count=2))["code"], "duplicate_successor")
        self.assertEqual(decision(handoff_record(transcript="do not copy"))["code"], "invalid_schema")
        self.assertEqual(decision(handoff_record(successor_count=0, successors=[]))["code"], "missing_successor")
        pending = handoff_record()
        pending["acknowledgement"] = {
            "state": "pending", "owner": "owner-1", "generation": 1,
            "successor": "ticket-2", "handoff_hash": pending["hash"],
        }
        self.assertEqual(decision(pending)["code"], "successor_not_acknowledged")

    def test_handoff_requires_bounded_safe_trigger_but_checkpoint_does_not(self) -> None:
        missing = handoff_record()
        missing.pop("handoff_trigger")
        self.assertEqual(decision(missing)["code"], "missing_handoff_trigger")
        unsafe = handoff_record()
        unsafe["handoff_trigger"] = {
            "reason": "natural_phase_boundary",
            "evidence": {"source": "phase"},
            "safe_checkpoint": False,
        }
        self.assertEqual(decision(unsafe)["code"], "unsafe_handoff_checkpoint")
        record = handoff_record(action="checkpoint", successor_count=0, successors=[])
        record.pop("handoff_trigger")
        record["acknowledgement"] = {
            "state": "pending", "owner": "owner-1", "generation": 1,
            "successor": None, "handoff_hash": record["hash"],
        }
        self.assertEqual(decision(record)["decision"], "evidence")

    def test_checkpoint_without_successor_is_valid_only_for_checkpoint_action(self) -> None:
        record = handoff_record(action="checkpoint", successor_count=0, successors=[])
        record["acknowledgement"] = {
            "state": "pending", "owner": "owner-1", "generation": 1,
            "successor": None, "handoff_hash": record["hash"],
        }
        self.assertEqual(decision(record)["decision"], "evidence")

    def test_handoff_ack_and_payload_are_cryptographically_bound(self) -> None:
        self.assertEqual(decision(handoff_record(acknowledgement="acknowledged"))["code"], "invalid_acknowledgement")
        wrong_successor = handoff_record()
        wrong_successor["acknowledgement"] = {
            "state": "acknowledged", "owner": "owner-1", "generation": 1,
            "successor": "ticket-3", "handoff_hash": wrong_successor["hash"],
        }
        self.assertEqual(decision(wrong_successor)["code"], "successor_mismatch")
        tampered = handoff_record()
        tampered["handoff_payload"] = {"generation": 1, "next_action": "different"}
        self.assertEqual(decision(tampered)["code"], "handoff_hash_mismatch")

    def test_checkpoint_owner_evidence_and_fencing_are_bound_to_actor(self) -> None:
        record = handoff_record()
        evidence_path = Path(str(record["owner_evidence_path"]))
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["owner"] = "other-owner"
        evidence["hash"] = canonical_hash({key: value for key, value in evidence.items() if key != "hash"})
        evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
        self.assertEqual(decision(record)["code"], "owner_runtime_state_mismatch")

        fenced = handoff_record()
        fenced["lease"] = {**fenced["lease"], "fencing_token": "fence-other"}
        self.assertEqual(decision(fenced)["code"], "owner_evidence_mismatch")
        self.assertEqual(decision(handoff_record(), competing=True)["code"], "owner_lock_not_held")

    def test_handoff_payload_sections_are_strict_and_hash_bound(self) -> None:
        missing_section = handoff_record()
        missing_section["handoff_payload"] = {
            key: value for key, value in missing_section["handoff_payload"].items() if key != "ci"
        }
        missing_section["hash"] = canonical_hash(missing_section["handoff_payload"])
        self.assertEqual(decision(missing_section)["code"], "invalid_handoff_payload")

        unconfirmed = handoff_record()
        unconfirmed["handoff_payload"] = {
            **unconfirmed["handoff_payload"], "unconfirmed": ["review evidence unavailable"],
        }
        unconfirmed["hash"] = canonical_hash(unconfirmed["handoff_payload"])
        self.assertEqual(decision(unconfirmed)["code"], "invalid_handoff_payload")

        tampered = handoff_record()
        tampered["handoff_payload"] = {**tampered["handoff_payload"], "pr": ["changed"]}
        self.assertEqual(decision(tampered)["code"], "handoff_hash_mismatch")

        oversized = handoff_record()
        oversized["handoff_payload"] = {
            **oversized["handoff_payload"], "validation": ["x" * 4096] * 4,
        }
        oversized["hash"] = canonical_hash(oversized["handoff_payload"])
        self.assertEqual(decision(oversized)["code"], "handoff_too_large")

    def test_pre_mutation_requires_complete_fresh_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for key in ("authority", "checkpoint", "fingerprints", "pending_side_effects", "unknown_outcomes"):
                record = pre_record(root)
                record.pop(key)
                with self.subTest(key=key):
                    self.assertEqual(decision(record)["decision"], "block")
                    if key in {"pending_side_effects", "unknown_outcomes"}:
                        self.assertEqual(decision(record)["code"], f"missing_{key}")
            self.assertEqual(decision(pre_record(root, pending_side_effects=["unknown-write"]))["code"], "nonempty_pending_side_effects")

            missing_and_nonempty = pre_record(root, unknown_outcomes=["unresolved"])
            missing_and_nonempty.pop("pending_side_effects")
            self.assertEqual(
                decision(missing_and_nonempty)["code"], "nonempty_unknown_outcomes",
            )

            for overrides, expected in (
                ({"operation": "invalid"}, "invalid_operation"),
                ({"cwd": str(root)}, "cwd_mismatch"),
                ({"operation": "prepare_worktree", "target_worktree": str(root / "outside")}, "external_worktree"),
            ):
                record = pre_record(root, **overrides)
                record.pop("pending_side_effects")
                with self.subTest(overrides=overrides):
                    self.assertEqual(decision(record)["code"], expected)

    def test_checkpoint_identity_and_lease_bindings_are_required(self) -> None:
        missing_common = handoff_record(repo_identity="repo-1")
        missing_common.pop("common_dir")
        self.assertEqual(decision(missing_common)["code"], "missing_common_dir")
        missing_spec_revision = handoff_record()
        missing_spec_revision.pop("spec_revision")
        self.assertEqual(decision(missing_spec_revision)["code"], "missing_spec_revision")
        missing_spec_digest = handoff_record()
        missing_spec_digest.pop("spec_digest")
        self.assertEqual(decision(missing_spec_digest)["code"], "missing_spec_digest")
        self.assertEqual(
            decision(handoff_record(
                repo_identity={"identity": "repo-1", "is_bare": True, "common_dir": "/repo"},
                common_dir="/repo",
            ))["code"],
            "bare_worktree",
        )
        self.assertEqual(decision(handoff_record(lease={"id": "lease-1", "owner": "other"}))["code"], "owner_mismatch")

    def test_rejects_json_with_huge_integer_as_invalid_json(self) -> None:
        raw = b'{"action":"checkpoint","generation":' + (b"9" * 5000) + b"}"
        result = run_gate(raw)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["code"], "invalid_json")

    def test_rejects_excessively_nested_json_without_traceback(self) -> None:
        nested = {"leaf": "ok"}
        for _ in range(1200):
            nested = {"nested": nested}
        result = run_gate({"action": "checkpoint", "handoff_payload": nested})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["code"], "context_too_deep")
        self.assertEqual(result.stderr, b"")

    def test_valid_and_invalid_luna_bootstrap(self) -> None:
        result = run_gate(luna_packet())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["decision"], "evidence")
        packet = luna_packet(ticket="ticket-scoped")
        self.assertEqual(decision(packet)["decision"], "evidence")
        self.assertEqual(
            decision(luna_packet(ticket="ticket-scoped", phase="different-phase"))["code"],
            "luna_bootstrap_evidence_valid",
        )
        phase_scoped = luna_packet()
        phase_scoped_fixture = SUPERVISOR_FIXTURES_BY_PROJECT["/repo"]
        phase_scoped_fixture["scope"] = phase_scoped["packet"]["phase"]
        self.assertEqual(decision(phase_scoped)["code"], "owner_evidence_mismatch")
        self.assertEqual(decision({"action": "luna_bootstrap", "packet": luna_packet()["packet"], "prompt": "extra"})["code"], "invalid_packet_schema")
        self.assertEqual(
            decision(luna_packet(repo_identity={"identity": "repo-1", "is_bare": True, "common_dir": "/repo"}))["code"],
            "bare_worktree",
        )
        self.assertEqual(decision(luna_packet(model="gpt-5.5"))["code"], "invalid_model")
        self.assertEqual(decision(luna_packet(reasoning="high"))["code"], "invalid_reasoning")
        self.assertEqual(
            decision(luna_packet(reasoning="xhigh", reasoning_effort="low"))["code"],
            "invalid_reasoning",
        )
        self.assertEqual(decision(luna_packet(fork_turns="all"))["code"], "invalid_fork_turns")
        self.assertEqual(decision(luna_packet(owner=""))["code"], "missing_owner")
        self.assertEqual(decision(luna_packet(ticket=None))["code"], "missing_task")
        self.assertEqual(decision(luna_packet(paths={"read": ["src/example.py"]}))["code"], "invalid_paths")
        self.assertEqual(decision(luna_packet(paths=["/etc/passwd"]))["code"], "invalid_paths")
        self.assertEqual(decision(luna_packet(write_scope=["../outside.py"]))["code"], "invalid_write_scope")
        self.assertEqual(decision(luna_packet(worktree="/private/tmp/external"))["code"], "external_worktree")
        self.assertEqual(decision(luna_packet(requirements_digest=None))["code"], "missing_requirements_digest")
        self.assertEqual(decision(luna_packet(output_cap=6001))["code"], "invalid_output_cap")
        oversized = luna_packet(objective="x" * (12 * 1024))
        self.assertEqual(decision(oversized)["code"], "packet_too_large")

    def test_luna_handoff_file_is_bound_to_packet_and_generation(self) -> None:
        payload = luna_packet()
        handoff_path = Path(str(payload["packet"]["handoff_path"]))
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        handoff["packet_fingerprint"] = "0" * 64
        handoff["hash"] = canonical_hash({key: value for key, value in handoff.items() if key != "hash"})
        handoff_path.write_text(json.dumps(handoff, sort_keys=True), encoding="utf-8")
        self.assertEqual(decision(payload)["code"], "handoff_binding_mismatch")

        payload = luna_packet()
        handoff_path = Path(str(payload["packet"]["handoff_path"]))
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        handoff["generation"] = 2
        handoff["hash"] = canonical_hash({key: value for key, value in handoff.items() if key != "hash"})
        handoff_path.write_text(json.dumps(handoff, sort_keys=True), encoding="utf-8")
        self.assertEqual(decision(payload)["code"], "generation_mismatch")

        payload = luna_packet()
        real_path = Path(str(payload["packet"]["handoff_path"]))
        target_path = real_path.with_name(f"{real_path.stem}-target.json")
        target_path.write_bytes(real_path.read_bytes())
        symlink_path = real_path.with_name(f"{real_path.stem}-symlink.json")
        symlink_path.symlink_to(target_path)
        payload["packet"]["handoff_path"] = str(symlink_path)
        self.assertEqual(decision(payload)["code"], "invalid_handoff_path")

    def test_luna_handoff_under_project_or_worktree_is_rejected(self) -> None:
        payload = luna_packet()
        project_handoff = Path(payload["packet"]["project_root"]) / ".codex" / "handoff.json"
        payload["packet"]["handoff_path"] = str(project_handoff)
        self.assertEqual(decision(payload)["code"], "invalid_handoff_path")

        payload = luna_packet()
        worktree_handoff = Path(payload["packet"]["worktree"]) / "handoff.json"
        payload["packet"]["handoff_path"] = str(worktree_handoff)
        self.assertEqual(decision(payload)["code"], "invalid_handoff_path")


if __name__ == "__main__":
    unittest.main()
