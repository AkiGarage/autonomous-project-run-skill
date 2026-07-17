#!/usr/bin/env python3
"""Deterministic, side-effect-free APR evidence validators.

The helper accepts one JSON object on stdin and emits one bounded JSON result.
It deliberately does not inspect or mutate a repository.  A direct caller can
forge every process-local input, so only a global Codex hook host may authorize
mutation.  This module validates compatibility evidence for that host.
"""

from __future__ import annotations

import hashlib
import fcntl
import hmac
import errno
import json
import os
import re
import selectors
import stat
import sys
import tempfile
import time
from typing import Any, NoReturn


MAX_INPUT_BYTES = 64 * 1024
MAX_PACKET_BYTES = 12 * 1024
MAX_OUTPUT_CAP = 6000
MAX_STRING_LENGTH = 4096
MAX_NESTING_DEPTH = 64
DIGEST_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9._:/@+-]{1,128}\Z")

# Legacy APR evidence uses these fixed descriptors.  A same-UID caller can
# construct all three values, so they are evidence-integrity checks only and
# are never an authorization boundary.
SUPERVISOR_CAPABILITY_FD = 198
SUPERVISOR_KEY_FD = 199
SUPERVISOR_LOCK_FD = 200
SUPERVISOR_CAPABILITY_VERSION = 1
SUPERVISOR_STREAM_TIMEOUT_SECONDS = 2.0
SUPERVISOR_STREAM_CHUNK_BYTES = 4096
SUPERVISOR_CAPABILITY_KEYS = {
    "schema_version", "project_root", "worktree", "repo_identity", "owner", "scope",
    "generation", "lease_id", "fencing_token", "lease_expires", "lock_dev", "lock_ino",
    "nonce", "mac",
}
SUPERVISOR_CAPABILITY_UNSIGNED_KEYS = SUPERVISOR_CAPABILITY_KEYS - {"mac"}


class GateError(ValueError):
    """A stable public decision code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _error(code: str) -> NoReturn:
    raise GateError(code)


def _string(value: Any, code: str = "invalid_field") -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_STRING_LENGTH:
        _error(code)
    return value


def _identifier(value: Any, code: str = "invalid_identifier") -> str:
    """Validate a compact identifier while preserving caller-specific errors."""
    value = _string(value, code)
    if IDENTIFIER_RE.fullmatch(value) is None:
        _error(code)
    return value


def _digest(value: Any, code: str = "invalid_digest") -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        _error(code)
    return value.lower()


def _bool(value: Any, code: str = "invalid_field") -> bool:
    if type(value) is not bool:
        _error(code)
    return value


def _integer(value: Any, code: str = "invalid_field") -> int:
    if type(value) is not int or value < 0:
        _error(code)
    return value


def _map(value: Any, code: str = "invalid_schema") -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(code)
    return value


def _path(value: Any, code: str = "invalid_path") -> str:
    value = _string(value, code)
    if "\x00" in value or not os.path.isabs(value):
        _error(code)
    return os.path.realpath(os.path.normpath(value))


def _within(path: str, parent: str) -> bool:
    """Return true for parent itself or a physical descendant."""
    try:
        return os.path.commonpath((path, parent)) == parent
    except ValueError:
        return False


def _managed_worktree_root(path: str, managed_root: str) -> bool:
    """Accept exactly one worktree id below the managed directory."""
    try:
        relative = os.path.relpath(path, managed_root)
    except ValueError:
        return False
    parts = relative.split(os.sep)
    return len(parts) == 1 and parts[0] not in {"", ".", ".."}


def _first(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _repo_identity(value: Any) -> tuple[str, bool, str | None]:
    """Validate a compact repo identity and return (identity, bare, common_dir)."""
    if isinstance(value, str):
        return _identifier(value), False, None
    repo = _map(value, "missing_repo_identity")
    allowed = {
        "identity", "id", "name", "sha", "repo_id", "is_bare", "bare",
        "common_dir", "path", "git_dir",
    }
    if set(repo) - allowed:
        _error("invalid_repo_identity")
    identity_values = [
        _identifier(repo[key], "invalid_repo_identity")
        for key in ("identity", "id", "name", "sha", "repo_id")
        if key in repo
    ]
    if not identity_values:
        _error("missing_repo_identity")
    if len(set(identity_values)) != 1:
        _error("repo_mismatch")
    identity = identity_values[0]
    bare_values = [
        _bool(repo[key], "invalid_bare_flag")
        for key in ("is_bare", "bare")
        if key in repo
    ]
    if len(set(bare_values)) > 1:
        _error("bare_identity_mismatch")
    bare = bare_values[0] if bare_values else False
    _bool(bare, "invalid_bare_flag")
    common_values = [
        _path(repo[key], "invalid_repo_identity")
        for key in ("common_dir", "git_dir")
        if key in repo
    ]
    if len(set(common_values)) > 1:
        _error("repo_mismatch")
    common_path = common_values[0] if common_values else None
    repo_path = repo.get("path")
    if repo_path is not None:
        _path(repo_path, "invalid_repo_identity")
    return identity, bare, common_path


def _assert_repo_alias_consistency(record: dict[str, Any], selected: Any) -> None:
    """Reject contradictory `repo` and `repo_identity` aliases."""
    if "repo" not in record or "repo_identity" not in record:
        return
    selected_identity = _repo_identity(selected)
    other = record["repo"] if selected is record.get("repo_identity") else record["repo_identity"]
    other_identity = _repo_identity(other)
    if selected_identity != other_identity:
        _error("repo_mismatch")


def _validate_repo_path(value: Any, project: str) -> None:
    """If supplied, a repository path must identify the declared project root."""
    if isinstance(value, dict) and value.get("path") is not None:
        if _path(value["path"], "invalid_repo_identity") != project:
            _error("repo_mismatch")


def _require_pre_fields(record: dict[str, Any]) -> tuple[str, str, str, str, str, str, str, str]:
    names = (
        "project_root",
        "cwd",
        "worktree",
        "repo_identity",
        "branch",
        "head",
        "requirements_digest",
        "role",
    )
    aliases = {"repo_identity": ("repo_identity", "repo")}
    values: list[Any] = []
    for name in names:
        value = _first(record, *(aliases.get(name, (name,))))
        if value is None:
            _error("missing_required")
        values.append(value)
    project = _path(values[0], "projectless")
    cwd = _path(values[1])
    worktree = _path(values[2])
    identity, bare, common_dir = _repo_identity(values[3])
    _validate_repo_path(values[3], project)
    _assert_repo_alias_consistency(record, values[3])
    declared_common = record.get("common_dir")
    if declared_common is not None:
        declared_common = _path(declared_common, "invalid_repo_identity")
        if common_dir is not None and declared_common != common_dir:
            _error("repo_mismatch")
        common_dir = declared_common
    declared_bare = record.get("is_bare", record.get("bare"))
    if declared_bare is not None:
        _bool(declared_bare, "invalid_bare_flag")
        # A legacy string identity carries no bare bit.  Treat an explicit
        # top-level true as authoritative in that case, while rejecting an
        # actual structured identity that contradicts the declaration.
        if declared_bare != bare and not (declared_bare and isinstance(values[3], str)):
            _error("bare_identity_mismatch")
        bare = bare or declared_bare
    branch = _identifier(values[4])
    head = _identifier(values[5])
    requirements = _digest(values[6])
    role = _identifier(values[7])
    # Store the extra identity attributes on the record for the caller without
    # expanding the public return shape.
    record["_repo_bare"] = bare
    record["_repo_common_dir"] = common_dir
    record["_repo_identity"] = identity
    return project, cwd, worktree, identity, branch, head, requirements, role


PRE_FINGERPRINTS = {
    "repo", "project", "worktree", "head", "index", "tree", "untracked",
    "source", "content", "requirements", "spec", "dependencies", "toolchain",
}
PRE_AUTHORITY_KEYS = {"confirmed", "scope"}
PRE_CHECKPOINT_KEYS = {"generation", "head", "source_fingerprint", "requirements_digest"}
OWNER_EVIDENCE_VERSION = 1
OWNER_EVIDENCE_KEYS = {
    "schema_version", "owner", "scope", "generation", "lease_id", "project_root",
    "worktree", "repo_identity", "common_dir", "branch", "base", "head",
    "requirements_digest", "spec_revision", "spec_digest", "source_fingerprint",
    "dependency_fingerprint", "toolchain_fingerprint", "checkpoint_fingerprint",
    "dirty_state", "fingerprints", "runtime_state_path", "owner_lock_path", "fencing_token", "lease_expires", "hash",
}
RUNTIME_STATE_VERSION = 1
RUNTIME_STATE_KEYS = {
    "schema_version", "project_root", "worktree", "repo_identity", "owner", "scope",
    "generation", "lease_id", "fencing_token", "lease_expires", "owner_lock_path", "owner_evidence_hash", "hash",
}

def _owner_evidence_path(value: Any, project: str) -> str:
    """Resolve owner evidence without following a final symlink."""
    raw = _string(value, "missing_owner_evidence")
    if "\x00" in raw or not os.path.isabs(raw):
        _error("invalid_owner_evidence")
    normalized = os.path.normpath(raw)
    if os.path.islink(normalized) or not os.path.isfile(normalized):
        _error("invalid_owner_evidence")
    resolved = os.path.realpath(normalized)
    if not _within(resolved, project) or resolved == project:
        _error("invalid_owner_evidence")
    return resolved


def _runtime_state_path(value: Any, project: str) -> str:
    """Resolve a fencing state file outside the project and below OS temp."""
    raw = _string(value, "invalid_runtime_state")
    if "\x00" in raw or not os.path.isabs(raw):
        _error("invalid_runtime_state")
    normalized = os.path.normpath(raw)
    temp_root = os.path.realpath(tempfile.gettempdir())
    candidate = os.path.abspath(normalized)
    parent = os.path.realpath(os.path.dirname(candidate))
    physical_candidate = os.path.join(parent, os.path.basename(candidate))
    if (
        physical_candidate == temp_root
        or not _within(physical_candidate, temp_root)
        or not _within(parent, temp_root)
        or _within(physical_candidate, project)
        or os.path.islink(normalized)
    ):
        _error("invalid_runtime_state")
    return normalized


def _owner_lock_path(value: Any, project: str) -> str:
    """Resolve the independent owner lock without following a final symlink."""
    raw = _string(value, "missing_owner_lock")
    if "\x00" in raw or not os.path.isabs(raw):
        _error("invalid_owner_lock")
    normalized = os.path.normpath(raw)
    temp_root = os.path.realpath(tempfile.gettempdir())
    candidate = os.path.abspath(normalized)
    parent = os.path.realpath(os.path.dirname(candidate))
    physical_candidate = os.path.join(parent, os.path.basename(candidate))
    if (
        physical_candidate == temp_root
        or not _within(physical_candidate, temp_root)
        or not _within(parent, temp_root)
        or _within(physical_candidate, project)
        or os.path.islink(normalized)
    ):
        _error("invalid_owner_lock")
    return normalized


def _read_runtime_state(evidence: dict[str, Any], project: str) -> dict[str, Any]:
    """Read and authenticate external runtime lease metadata."""
    path = _runtime_state_path(evidence.get("runtime_state_path"), project)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _error("owner_runtime_state_missing")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            _error("invalid_runtime_state")
        try:
            raw = os.read(descriptor, MAX_PACKET_BYTES + 1)
        except OSError:
            _error("invalid_runtime_state")
        if len(raw) > MAX_PACKET_BYTES:
            _error("invalid_runtime_state")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            _error("invalid_runtime_state")
        state = _map(value, "invalid_runtime_state")
        if set(state) != RUNTIME_STATE_KEYS or state.get("schema_version") != RUNTIME_STATE_VERSION:
            _error("invalid_runtime_state")
        unsigned = {key: item for key, item in state.items() if key != "hash"}
        try:
            canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            _error("invalid_runtime_state")
        if _digest(state.get("hash"), "invalid_runtime_state") != hashlib.sha256(canonical).hexdigest():
            _error("runtime_state_hash_mismatch")
        for key in ("project_root", "worktree"):
            _path(state.get(key), "invalid_runtime_state")
        for key in ("repo_identity", "owner", "scope", "lease_id", "fencing_token"):
            _identifier(state.get(key), "invalid_runtime_state")
        _owner_lock_path(state.get("owner_lock_path"), project)
        generation = _integer(state.get("generation"), "invalid_runtime_state")
        if generation < 1:
            _error("invalid_runtime_state")
        expiry = _integer(state.get("lease_expires"), "invalid_runtime_state")
        if expiry <= int(time.time()):
            _error("expired_lease")
        if _digest(state.get("owner_evidence_hash"), "invalid_runtime_state") != evidence["hash"]:
            _error("owner_runtime_state_mismatch")
        expected = {
            "project_root": _path(evidence["project_root"], "invalid_owner_evidence"),
            "worktree": _path(evidence["worktree"], "invalid_owner_evidence"),
            "repo_identity": evidence["repo_identity"],
            "owner": evidence["owner"],
            "scope": evidence["scope"],
            "generation": evidence["generation"],
            "lease_id": evidence["lease_id"],
            "fencing_token": evidence["fencing_token"],
            "lease_expires": evidence["lease_expires"],
            "owner_lock_path": evidence["owner_lock_path"],
        }
        actual = dict(state)
        actual["project_root"] = _path(state["project_root"], "invalid_runtime_state")
        actual["worktree"] = _path(state["worktree"], "invalid_runtime_state")
        if any(actual[key] != value for key, value in expected.items()):
            _error("owner_runtime_state_mismatch")
        return state
    finally:
        os.close(descriptor)


def _read_supervisor_stream(fd: int, limit: int, code: str) -> bytes:
    """Read a bounded, closed supervisor pipe without touching caller paths."""
    try:
        info = os.fstat(fd)
    except OSError:
        _error("supervisor_unavailable")
    if not stat.S_ISFIFO(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        _error(code)
    deadline = time.monotonic() + SUPERVISOR_STREAM_TIMEOUT_SECONDS
    selector = selectors.DefaultSelector()
    original_flags: int | None = None
    try:
        try:
            original_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            if not original_flags & os.O_NONBLOCK:
                fcntl.fcntl(fd, fcntl.F_SETFL, original_flags | os.O_NONBLOCK)
        except OSError:
            _error(code)
        try:
            selector.register(fd, selectors.EVENT_READ)
        except (OSError, ValueError):
            _error(code)
        value = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _error(code)
            try:
                events = selector.select(remaining)
            except OSError:
                _error(code)
            if not events:
                _error(code)
            read_size = min(SUPERVISOR_STREAM_CHUNK_BYTES, limit + 1 - len(value))
            try:
                chunk = os.read(fd, read_size)
            except OSError as error:
                if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
                _error(code)
            if not chunk:
                return bytes(value)
            value.extend(chunk)
            if len(value) > limit:
                _error(code)
            try:
                marker = os.read(fd, 1)
            except OSError as error:
                if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
                _error(code)
            if not marker:
                return bytes(value)
            value.extend(marker)
            if len(value) > limit:
                _error(code)
    finally:
        selector.close()
        if original_flags is not None and not original_flags & os.O_NONBLOCK:
            try:
                fcntl.fcntl(fd, fcntl.F_SETFL, original_flags)
            except OSError:
                pass


def _read_supervisor_capability(
    project: str,
    worktree: str,
    identity: str,
    *,
    expected_owner: str | None = None,
    expected_scope: str | None = None,
    expected_generation: int | None = None,
    expected_lease_id: str | None = None,
    expected_fencing_token: str | None = None,
    expected_expires: int | None = None,
) -> dict[str, Any]:
    """Authenticate canonical owner state issued by the trusted host supervisor."""
    try:
        lock_info = os.fstat(SUPERVISOR_LOCK_FD)
    except OSError:
        _error("supervisor_unavailable")
    if (
        not stat.S_ISREG(lock_info.st_mode)
        or lock_info.st_uid != os.getuid()
        or lock_info.st_mode & 0o077
    ):
        _error("supervisor_unavailable")
    capability_raw = _read_supervisor_stream(
        SUPERVISOR_CAPABILITY_FD, MAX_PACKET_BYTES, "supervisor_capability_invalid"
    )
    key = _read_supervisor_stream(SUPERVISOR_KEY_FD, 128, "supervisor_capability_invalid")
    if len(key) < 16:
        _error("supervisor_capability_invalid")
    try:
        capability_value = json.loads(capability_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _error("supervisor_capability_invalid")
    capability = _map(capability_value, "supervisor_capability_invalid")
    if (
        set(capability) != SUPERVISOR_CAPABILITY_KEYS
        or capability.get("schema_version") != SUPERVISOR_CAPABILITY_VERSION
    ):
        _error("supervisor_capability_invalid")
    unsigned = {name: capability[name] for name in SUPERVISOR_CAPABILITY_UNSIGNED_KEYS}
    try:
        canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        _error("supervisor_capability_invalid")
    supplied_mac_value = capability.get("mac")
    if (
        not isinstance(supplied_mac_value, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", supplied_mac_value) is None
    ):
        _error("supervisor_capability_invalid")
    supplied_mac = bytes.fromhex(supplied_mac_value)
    expected_mac = hmac.new(key, canonical, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_mac, expected_mac):
        _error("supervisor_capability_invalid")
    if _path(capability["project_root"], "supervisor_capability_invalid") != project:
        _error("supervisor_capability_mismatch")
    if _path(capability["worktree"], "supervisor_capability_invalid") != worktree:
        _error("supervisor_capability_mismatch")
    if _identifier(capability["repo_identity"], "supervisor_capability_invalid") != identity:
        _error("supervisor_capability_mismatch")
    owner = _identifier(capability["owner"], "supervisor_capability_invalid")
    scope = _identifier(capability["scope"], "supervisor_capability_invalid")
    generation = _integer(capability["generation"], "supervisor_capability_invalid")
    lease_id = _identifier(capability["lease_id"], "supervisor_capability_invalid")
    fencing_token = _identifier(capability["fencing_token"], "supervisor_capability_invalid")
    expires = _integer(capability["lease_expires"], "supervisor_capability_invalid")
    if generation < 1 or expires <= int(time.time()):
        _error("expired_lease" if expires <= int(time.time()) else "supervisor_capability_invalid")
    if _integer(capability["lock_dev"], "supervisor_capability_invalid") != lock_info.st_dev:
        _error("supervisor_capability_mismatch")
    if _integer(capability["lock_ino"], "supervisor_capability_invalid") != lock_info.st_ino:
        _error("supervisor_capability_mismatch")
    _string(capability["nonce"], "supervisor_capability_invalid")
    if expected_owner is not None and owner != expected_owner:
        _error("owner_evidence_mismatch")
    if expected_scope is not None and scope != expected_scope:
        _error("owner_evidence_mismatch")
    if expected_generation is not None and generation != expected_generation:
        _error("generation_mismatch")
    if expected_lease_id is not None and lease_id != expected_lease_id:
        _error("owner_evidence_mismatch")
    if expected_fencing_token is not None and fencing_token != expected_fencing_token:
        _error("owner_evidence_mismatch")
    if expected_expires is not None and expires != expected_expires:
        _error("owner_evidence_mismatch")
    try:
        fcntl.flock(SUPERVISOR_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as error:
        if isinstance(error, BlockingIOError) or getattr(error, "errno", None) in {11, 35}:
            _error("owner_lock_not_held")
        _error("supervisor_unavailable")
    return capability


def _verify_owner_lock(evidence: dict[str, Any], project: str, descriptor_value: Any) -> None:
    """Validate the fixed supervisor capability; ignore caller-provided FD/path."""
    del descriptor_value
    worktree = _path(evidence.get("worktree"), "invalid_owner_evidence")
    capability = _read_supervisor_capability(
        project,
        worktree,
        evidence["repo_identity"],
        expected_owner=evidence["owner"],
        expected_scope=evidence["scope"],
        expected_generation=evidence["generation"],
        expected_lease_id=evidence["lease_id"],
        expected_fencing_token=evidence["fencing_token"],
        expected_expires=evidence["lease_expires"],
    )
    if (
        _path(capability["project_root"], "supervisor_capability_invalid") != project
        or _path(capability["worktree"], "supervisor_capability_invalid") != worktree
    ):
        _error("supervisor_capability_mismatch")


def load_owner_evidence(path: Any, project: str | None = None) -> dict[str, Any]:
    """Load and authenticate the durable owner evidence file."""
    project_path = _path(project, "invalid_project_root") if project is not None else None
    resolved = _owner_evidence_path(path, project_path) if project_path is not None else _path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError:
        _error("invalid_owner_evidence")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            _error("invalid_owner_evidence")
        raw = os.read(descriptor, MAX_PACKET_BYTES + 1)
        if len(raw) > MAX_PACKET_BYTES:
            _error("invalid_owner_evidence")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _error("invalid_owner_evidence")
    finally:
        os.close(descriptor)
    evidence = _map(value, "invalid_owner_evidence")
    if set(evidence) != OWNER_EVIDENCE_KEYS:
        _error("invalid_owner_evidence")
    if evidence.get("schema_version") != OWNER_EVIDENCE_VERSION:
        _error("unsupported_owner_evidence")
    unsigned = {key: item for key, item in evidence.items() if key != "hash"}
    try:
        canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        _error("invalid_owner_evidence")
    if _digest(evidence.get("hash"), "invalid_owner_evidence") != hashlib.sha256(canonical).hexdigest():
        _error("owner_evidence_hash_mismatch")
    _identifier(evidence.get("owner"), "invalid_owner_evidence")
    _identifier(evidence.get("scope"), "invalid_owner_evidence")
    generation = _integer(evidence.get("generation"), "invalid_owner_evidence")
    if generation < 1:
        _error("invalid_owner_evidence")
    _identifier(evidence.get("lease_id"), "invalid_owner_evidence")
    for key in ("project_root", "worktree", "common_dir"):
        _path(evidence.get(key), "invalid_owner_evidence")
    _identifier(evidence.get("repo_identity"), "invalid_owner_evidence")
    for key in ("branch", "base", "head", "spec_revision"):
        _identifier(evidence.get(key), "invalid_owner_evidence")
    for key in (
        "requirements_digest", "spec_digest", "source_fingerprint", "dependency_fingerprint",
        "toolchain_fingerprint", "checkpoint_fingerprint",
    ):
        _digest(evidence.get(key), "invalid_owner_evidence")
    dirty = _map(evidence.get("dirty_state"), "invalid_owner_evidence")
    if set(dirty) != DIRTY_STATE_KEYS:
        _error("invalid_owner_evidence")
    for item in dirty.values():
        _bool(item, "invalid_owner_evidence")
    fingerprints = _map(evidence.get("fingerprints"), "invalid_owner_evidence")
    if set(fingerprints) != PRE_FINGERPRINTS:
        _error("invalid_owner_evidence")
    for item in fingerprints.values():
        _digest(item, "invalid_owner_evidence")
    _runtime_state_path(evidence.get("runtime_state_path"), project_path or os.path.dirname(resolved))
    _owner_lock_path(evidence.get("owner_lock_path"), project_path or os.path.dirname(resolved))
    _identifier(evidence.get("fencing_token"), "invalid_owner_evidence")
    lease_expires = _integer(evidence.get("lease_expires"), "invalid_owner_evidence")
    if lease_expires <= int(time.time()):
        _error("expired_lease")
    if project_path is not None and _path(evidence["project_root"], "invalid_owner_evidence") != project_path:
        _error("owner_evidence_mismatch")
    return evidence


def _validate_pre_evidence(
    record: dict[str, Any], project: str, worktree: str, identity: str, branch: str,
    head: str, requirements: str,
) -> None:
    """Bind a mutation decision to authority, ownership, and current source state."""
    authority = _map(record.get("authority"), "missing_authority")
    if set(authority) != PRE_AUTHORITY_KEYS or authority.get("confirmed") is not True:
        _error("invalid_authority")
    _identifier(authority.get("scope"), "invalid_authority")
    _identifier(record.get("base"), "missing_base")
    _identifier(record.get("spec_revision"), "missing_spec_revision")
    spec_digest = _digest(record.get("spec_digest"), "missing_spec_digest")
    source = _digest(record.get("source_fingerprint"), "missing_source_fingerprint")
    dependency = _digest(record.get("dependency_fingerprint"), "missing_dependency_fingerprint")
    toolchain = _digest(record.get("toolchain_fingerprint"), "missing_toolchain_fingerprint")
    owner = _identifier(record.get("owner"), "missing_owner")

    # Validate the caller's booleans before opening owner evidence.  This keeps
    # malformed mutable input from being masked by a later ownership check.
    record_dirty = _map(record.get("dirty_state"), "missing_dirty_state")
    if set(record_dirty) != DIRTY_STATE_KEYS:
        _error("invalid_dirty_state")
    for value in record_dirty.values():
        _bool(value, "invalid_dirty_state")

    evidence_path = record.get("owner_evidence_path")
    evidence = load_owner_evidence(evidence_path, project)
    _read_runtime_state(evidence, project)
    if evidence["owner"] != owner or evidence["scope"] != authority["scope"]:
        _error("owner_evidence_mismatch")
    if _path(evidence["worktree"], "invalid_owner_evidence") != worktree:
        _error("owner_evidence_mismatch")
    if evidence["repo_identity"] != identity or evidence["branch"] != branch or evidence["head"] != head:
        _error("owner_evidence_mismatch")
    if _path(evidence["project_root"], "invalid_owner_evidence") != project:
        _error("owner_evidence_mismatch")
    declared_common = record.get("common_dir")
    if declared_common is not None and _path(evidence["common_dir"], "invalid_owner_evidence") != _path(declared_common):
        _error("owner_evidence_mismatch")
    if evidence["base"] != record["base"] or evidence["spec_revision"] != record["spec_revision"]:
        _error("owner_evidence_mismatch")
    if any(evidence[key] != value for key, value in {
        "requirements_digest": requirements,
        "spec_digest": spec_digest,
        "source_fingerprint": source,
        "dependency_fingerprint": dependency,
        "toolchain_fingerprint": toolchain,
    }.items()):
        _error("owner_evidence_mismatch")

    lease = _map(record.get("lease"), "missing_lease")
    if set(lease) - LEASE_KEYS or lease.get("active") is not True:
        _error("invalid_lease")
    if _identifier(lease.get("owner"), "invalid_lease") != owner:
        _error("owner_mismatch")
    generation = _integer(lease.get("generation"), "invalid_lease")
    if generation < 1:
        _error("invalid_lease")
    lease_id = _identifier(lease.get("id"), "invalid_lease")
    if evidence["lease_id"] != lease_id or evidence["generation"] != generation:
        _error("owner_evidence_mismatch")
    lease_expires = _integer(lease.get("expires"), "invalid_lease")
    if lease_expires <= int(time.time()):
        _error("expired_lease")
    if (
        evidence["runtime_state_path"] != _runtime_state_path(lease.get("runtime_state_path"), project)
        or evidence["fencing_token"] != _identifier(lease.get("fencing_token"), "invalid_lease")
        or evidence["lease_expires"] != lease_expires
        or evidence["owner_lock_path"] != _owner_lock_path(lease.get("owner_lock_path"), project)
    ):
        _error("owner_evidence_mismatch")
    _verify_owner_lock(evidence, project, lease.get("owner_lock_fd"))

    checkpoint = _map(record.get("checkpoint"), "missing_checkpoint")
    if set(checkpoint) != PRE_CHECKPOINT_KEYS:
        _error("invalid_checkpoint")
    if _integer(checkpoint.get("generation"), "invalid_checkpoint") != generation:
        _error("generation_mismatch")
    if _identifier(checkpoint.get("head"), "invalid_checkpoint") != head:
        _error("head_mismatch")
    if _digest(checkpoint.get("source_fingerprint"), "invalid_checkpoint") != source:
        _error("fingerprint_mismatch")
    if _digest(checkpoint.get("requirements_digest"), "invalid_checkpoint") != requirements:
        _error("requirements_mismatch")
    if evidence["checkpoint_fingerprint"] != _digest(checkpoint["source_fingerprint"], "invalid_checkpoint"):
        _error("owner_evidence_mismatch")

    if evidence["dirty_state"] != record_dirty:
        _error("owner_evidence_mismatch")

    fingerprints = _map(record.get("fingerprints"), "missing_fingerprints")
    if set(fingerprints) != PRE_FINGERPRINTS:
        _error("missing_fingerprints")
    normalized = {key: _digest(value, "invalid_fingerprints") for key, value in fingerprints.items()}
    expected = {
        "source": source,
        "requirements": requirements,
        "spec": spec_digest,
        "dependencies": dependency,
        "toolchain": toolchain,
    }
    if any(normalized[key] != value for key, value in expected.items()):
        _error("fingerprint_mismatch")
    if evidence["fingerprints"] != normalized:
        _error("owner_evidence_mismatch")
    for key in ("pending_side_effects", "unknown_outcomes"):
        value = record.get(key)
        if value != []:
            _error(f"nonempty_{key}")


def _validate_pre_mutation(record: dict[str, Any]) -> dict[str, Any]:
    project, cwd, worktree, identity, branch, head, requirements, role = _require_pre_fields(record)
    # Preserve the hard bare-repository gate before requiring durable owner
    # evidence; a bare root must never be accepted even for preparation.
    if bool(record.get("_repo_bare", False)):
        _error("bare_worktree")
    _validate_pre_evidence(record, project, worktree, identity, branch, head, requirements)
    action = record.get("action", record.get("command"))
    default_operation = "prepare_worktree" if action == "prepare_worktree" else "mutate"
    operation = record.get("operation", record.get("mutation", default_operation))
    if not isinstance(operation, str) or operation not in {"mutate", "prepare_worktree"}:
        _error("invalid_operation")
    bare = bool(record.get("_repo_bare", False))
    target_raw = _first(record, "target_worktree", "target")
    target = _path(target_raw, "invalid_target") if target_raw is not None else None
    managed_root = os.path.join(project, ".codex", "worktrees")

    # Bare repositories do not provide a physical worktree.  Even a
    # preparation request is blocked because accepting a target would allow a
    # caller to smuggle a non-repository path through this deterministic gate.
    if bare:
        _error("bare_worktree")
    if operation == "prepare_worktree":
        if target is None or not _managed_worktree_root(target, managed_root):
            _error("external_worktree")
        for key in ("command", "commands", "remote", "remote_action", "side_effects"):
            if key in record and record[key] not in (None, False, [], ""):
                _error("other_mutation")
        if not bare and not (_within(worktree, managed_root) or worktree == project):
            _error("external_worktree")
        if not os.path.isdir(worktree):
            _error("external_worktree")
        effective_worktree = target
    else:
        effective_worktree = worktree

    # The project root is the main checkout, not a managed execution worktree.
    # Only the exact preparation operation may use it as the source for a
    # host-validated managed worktree bootstrap.
    if operation != "prepare_worktree" and effective_worktree == project:
        _error("main_checkout_mutation")
    if effective_worktree != project and not _managed_worktree_root(effective_worktree, managed_root):
        _error("external_worktree")
    # During preparation the destination does not exist yet; bind the caller
    # to the existing source worktree and validate the target only by shape.
    if operation != "prepare_worktree" and not _within(cwd, effective_worktree):
        _error("cwd_mismatch")
    if operation == "prepare_worktree" and not _within(cwd, worktree):
        _error("cwd_mismatch")
    common_dir = record.get("_repo_common_dir")
    # Git's common directory is either the project root (bare layout) or a
    # physical descendant such as <project>/.git for linked worktrees.  A
    # parent directory is deliberately rejected: it cannot bind this record
    # to the project named by the caller.
    if common_dir is not None and not _within(common_dir, project):
        _error("repo_mismatch")

    return {
        "decision": "allow",
        "code": "pre_mutation_allowed",
        "action": "prepare_worktree" if operation == "prepare_worktree" else "pre_mutation",
        "role": role,
        "repo_identity": identity,
        "branch": branch,
        "head": head,
        "requirements_digest": requirements,
    }


CHECKPOINT_KEYS = {
    "action", "checkpoint", "handoff", "atomic_handoff", "schema_version",
    "project_root", "cwd", "worktree", "repo", "repo_identity", "common_dir", "branch", "base", "head",
    "requirements_digest", "spec_revision", "spec_digest", "source_fingerprint",
    "dependency_fingerprint", "toolchain_fingerprint", "owner_evidence_path",
    "checkpoint_fingerprint", "fingerprint", "fingerprints", "expected_fingerprints",
    "compaction_count", "compactions", "root_compactions", "stale", "checkpoint_stale", "fresh",
    "generation", "checkpoint_generation", "hash", "checkpoint_hash", "successor_count", "successors",
    "acknowledgement", "ack", "acknowledgement_state", "status", "owner", "lease", "ticket", "phase",
    "pending_action", "unknown_outcome", "dirty_state", "validation", "negative_evidence", "handoff_payload",
}
ATOMIC_KEYS = {
    "generation", "hash", "atomic", "committed", "created", "acknowledgement", "ack", "successor_count",
    "handoff_payload",
}
FINGERPRINT_KEYS = {
    "repo", "project", "worktree", "base", "head", "index", "tree", "untracked", "source",
    "requirements", "spec", "dependencies", "toolchain", "content", "artifacts", "generated",
}


def _reject_transcript_keys(value: Any) -> None:
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_NESTING_DEPTH:
            _error("nested_too_deep")
        if isinstance(current, dict):
            for key, child in current.items():
                if any(token in key.lower() for token in ("transcript", "raw_log", "prompt", "message", "conversation")):
                    _error("forbidden_context")
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)


def _checkpoint_record(payload: dict[str, Any]) -> dict[str, Any]:
    nested = _first(payload, "checkpoint", "handoff")
    if nested is None:
        record = payload
    else:
        nested_record = _map(nested, "invalid_schema")
        # Permit the compact envelope to carry shared metadata alongside a
        # nested checkpoint/handoff, but fail closed on contradictory copies.
        envelope = {key: value for key, value in payload.items() if key not in {"checkpoint", "handoff"}}
        for key in set(envelope).intersection(nested_record):
            if envelope[key] != nested_record[key]:
                _error("contradictory_evidence")
        record = {**envelope, **nested_record}
    if set(payload) - CHECKPOINT_KEYS:
        _error("invalid_schema")
    if nested is not None and set(record) - (CHECKPOINT_KEYS | ATOMIC_KEYS | FINGERPRINT_KEYS):
        _error("invalid_schema")
    _reject_transcript_keys(payload)
    return record


def _digest_field(record: dict[str, Any], *names: str) -> str | None:
    value = _first(record, *names)
    return _digest(value) if value is not None else None


def _fingerprints(record: dict[str, Any]) -> dict[str, str]:
    value = record.get("fingerprints")
    if value is None:
        source = _first(record, "source_fingerprint", "fingerprint")
        if source is None:
            _error("missing_fingerprints")
        return {"source": _digest(source)}
    mapping = _map(value, "invalid_fingerprints")
    if not mapping or set(mapping) - FINGERPRINT_KEYS:
        _error("invalid_fingerprints")
    return {str(key): _digest(item, "invalid_fingerprints") for key, item in mapping.items()}


CHECKPOINT_REQUIRED_FINGERPRINTS = {
    "repo", "project", "worktree", "head", "index", "tree", "untracked",
}
DIRTY_STATE_KEYS = {"index", "worktree", "untracked"}
LEASE_KEYS = {
    "id", "owner", "generation", "active", "expires", "runtime_state_path", "owner_lock_path",
    "owner_lock_fd", "fencing_token",
}
ACK_KEYS = {"state", "owner", "generation", "successor", "handoff_hash"}
HANDOFF_PAYLOAD_KEYS = {
    "schema_version", "generation", "owner", "ticket", "phase", "project_root", "cwd",
    "worktree", "repo_identity", "common_dir", "branch", "base", "head",
    "requirements_digest", "spec_revision", "spec_digest", "source_fingerprint",
    "dependency_fingerprint", "toolchain_fingerprint", "checkpoint_fingerprint",
    "dirty_state", "fingerprints", "lease", "process", "lock", "pr", "ci", "review",
    "unconfirmed", "next_action", "pending_action", "validation", "negative_evidence",
}
HANDOFF_REQUIRED_KEYS = {
    "schema_version", "generation", "owner", "ticket", "phase", "project_root", "cwd",
    "worktree", "repo_identity", "common_dir", "branch", "base", "head",
    "requirements_digest", "spec_revision", "spec_digest", "source_fingerprint",
    "dependency_fingerprint", "toolchain_fingerprint", "checkpoint_fingerprint",
    "dirty_state", "fingerprints", "lease", "process", "lock", "pr", "ci", "review",
    "unconfirmed",
}

HANDOFF_LEASE_KEYS = {
    "id", "owner", "generation", "active", "expires", "runtime_state_path",
    "owner_lock_path", "fencing_token",
}
HANDOFF_PROCESS_KEYS = {"pid", "state"}
HANDOFF_LOCK_KEYS = {"path", "held", "fencing_token"}


def _reject_sensitive_keys(value: Any) -> None:
    """Keep durable handoffs bounded and free of credentials or secret blobs."""
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.lower()
            if any(token in lowered for token in ("secret", "password", "api_key", "credential")):
                _error("forbidden_context")
            _reject_sensitive_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_keys(child)


def _validate_handoff_sections(record: dict[str, Any], handoff: dict[str, Any], generation: int) -> None:
    """Validate recoverable lease/process/lock and review state in a handoff."""
    lease = _map(handoff["lease"], "invalid_handoff_payload")
    if set(lease) != HANDOFF_LEASE_KEYS:
        _error("invalid_handoff_payload")
    outer_lease = _map(record.get("lease"), "invalid_handoff_payload")
    for key in ("id", "owner", "runtime_state_path", "owner_lock_path", "fencing_token"):
        if not isinstance(lease.get(key), str) or lease[key] != outer_lease.get(key):
            _error("handoff_binding_mismatch")
    if _identifier(lease["owner"], "invalid_handoff_payload") != handoff["owner"]:
        _error("handoff_binding_mismatch")
    if _integer(lease["generation"], "invalid_handoff_payload") != generation:
        _error("generation_mismatch")
    if _bool(lease["active"], "invalid_handoff_payload") is not True:
        _error("invalid_handoff_payload")
    if _integer(lease["expires"], "invalid_handoff_payload") != _integer(outer_lease.get("expires"), "invalid_handoff_payload"):
        _error("handoff_binding_mismatch")
    _runtime_state_path(lease["runtime_state_path"], _path(record["project_root"], "invalid_handoff_payload"))
    _owner_lock_path(lease["owner_lock_path"], _path(record["project_root"], "invalid_handoff_payload"))
    process = _map(handoff["process"], "invalid_handoff_payload")
    if set(process) != HANDOFF_PROCESS_KEYS:
        _error("invalid_handoff_payload")
    if _integer(process["pid"], "invalid_handoff_payload") < 1:
        _error("invalid_handoff_payload")
    _identifier(process["state"], "invalid_handoff_payload")
    lock = _map(handoff["lock"], "invalid_handoff_payload")
    if set(lock) != HANDOFF_LOCK_KEYS:
        _error("invalid_handoff_payload")
    if _path(lock["path"], "invalid_handoff_payload") != _path(outer_lease["owner_lock_path"], "invalid_handoff_payload"):
        _error("handoff_binding_mismatch")
    if _bool(lock["held"], "invalid_handoff_payload") is not True:
        _error("owner_lock_not_held")
    if _identifier(lock["fencing_token"], "invalid_handoff_payload") != _identifier(outer_lease.get("fencing_token"), "invalid_handoff_payload"):
        _error("handoff_binding_mismatch")
    for key in ("pr", "ci", "review", "unconfirmed"):
        _string_list(handoff[key], "invalid_handoff_payload", allow_empty=True)
    for item in handoff["unconfirmed"]:
        if not item.startswith("UNCONFIRMED:"):
            _error("invalid_handoff_payload")


def _validate_handoff_payload(
    record: dict[str, Any], handoff: dict[str, Any], generation: int,
    identity: str, owner: str, ticket: str, phase: str,
) -> None:
    """Validate the versioned payload before its canonical hash is trusted."""
    if set(handoff) - HANDOFF_PAYLOAD_KEYS or not HANDOFF_REQUIRED_KEYS.issubset(handoff):
        _error("invalid_handoff_payload")
    if handoff.get("schema_version") != 1 or _integer(handoff.get("generation"), "invalid_handoff_payload") != generation:
        _error("generation_mismatch")
    for key, expected in (("owner", owner), ("ticket", ticket), ("phase", phase)):
        if _identifier(handoff.get(key), "invalid_handoff_payload") != expected:
            _error("handoff_binding_mismatch")
    for key in ("project_root", "cwd", "worktree"):
        outer = _path(record[key], "invalid_handoff_payload")
        if _path(handoff[key], "invalid_handoff_payload") != outer:
            _error("handoff_binding_mismatch")
    outer_common = record.get("common_dir")
    if outer_common is None:
        _error("missing_common_dir")
    if _path(handoff["common_dir"], "invalid_handoff_payload") != _path(outer_common, "invalid_handoff_payload"):
        _error("handoff_binding_mismatch")
    if _repo_identity(handoff["repo_identity"])[0] != identity:
        _error("handoff_binding_mismatch")
    for key in ("branch", "base", "head", "spec_revision"):
        if _identifier(handoff[key], "invalid_handoff_payload") != _identifier(record[key], "invalid_handoff_payload"):
            _error("handoff_binding_mismatch")
    for key in (
        "requirements_digest", "spec_digest", "source_fingerprint", "dependency_fingerprint",
        "toolchain_fingerprint", "checkpoint_fingerprint",
    ):
        if _digest(handoff[key], "invalid_handoff_payload") != _digest(record[key], "invalid_handoff_payload"):
            _error("handoff_binding_mismatch")
    dirty = _map(handoff["dirty_state"], "invalid_handoff_payload")
    if dirty != record["dirty_state"]:
        _error("handoff_binding_mismatch")
    outer_fingerprints = _fingerprints(record)
    payload_fingerprints = _map(handoff["fingerprints"], "invalid_handoff_payload")
    if set(payload_fingerprints) != set(outer_fingerprints):
        _error("handoff_binding_mismatch")
    for key, value in payload_fingerprints.items():
        if _digest(value, "invalid_handoff_payload") != outer_fingerprints[key]:
            _error("handoff_binding_mismatch")
    for key in ("next_action", "pending_action"):
        if key in handoff:
            _string(handoff[key], "invalid_handoff_payload")
    for key in ("validation", "negative_evidence"):
        if key in handoff:
            _string_list(handoff[key], "invalid_handoff_payload", allow_empty=True)
    _validate_handoff_sections(record, handoff, generation)
    _reject_transcript_keys(handoff)
    _reject_sensitive_keys(handoff)


def _validate_checkpoint_binding(record: dict[str, Any]) -> tuple[str, str, str, str, int]:
    """Require physical identity and ownership evidence before accepting a checkpoint."""
    project_value = record.get("project_root")
    cwd_value = record.get("cwd")
    worktree_value = record.get("worktree")
    if project_value is None:
        _error("missing_project_root")
    if cwd_value is None:
        _error("missing_cwd")
    if worktree_value is None:
        _error("missing_worktree")
    project = _path(project_value, "invalid_project_root")
    cwd = _path(cwd_value, "invalid_cwd")
    worktree = _path(worktree_value, "invalid_worktree")
    managed_root = os.path.join(project, ".codex", "worktrees")
    if worktree != project and not _managed_worktree_root(worktree, managed_root):
        _error("external_worktree")
    if not _within(cwd, worktree):
        _error("cwd_mismatch")

    repo_value = _first(record, "repo_identity", "repo")
    if repo_value is None:
        _error("missing_repo_identity")
    _assert_repo_alias_consistency(record, repo_value)
    identity, bare, common_dir = _repo_identity(repo_value)
    _validate_repo_path(repo_value, project)
    if bare:
        _error("bare_worktree")
    declared_common = record.get("common_dir")
    if declared_common is not None:
        declared_common = _path(declared_common, "invalid_repo_identity")
        if common_dir is not None and common_dir != declared_common:
            _error("repo_mismatch")
        common_dir = declared_common
    if common_dir is None:
        _error("missing_common_dir")
    if not _within(common_dir, project):
        _error("repo_mismatch")

    _identifier(record.get("spec_revision"), "missing_spec_revision")
    _digest(record.get("spec_digest"), "missing_spec_digest")
    for key, code in (("branch", "invalid_branch"), ("base", "invalid_base"), ("head", "invalid_head")):
        if key not in record:
            _error(f"missing_{key}")
        _identifier(record[key], code)
    owner = _identifier(record.get("owner"), "missing_owner")
    ticket = _identifier(record.get("ticket"), "missing_ticket")
    phase = _identifier(record.get("phase"), "missing_phase")

    lease = record.get("lease")
    if lease is None:
        _error("missing_lease")
    lease_generation: int | None = None
    if isinstance(lease, dict):
        if set(lease) - LEASE_KEYS:
            _error("invalid_lease")
        lease_id = _first(lease, "id", "owner")
        if lease_id is None:
            _error("invalid_lease")
        _identifier(lease_id, "invalid_lease")
        if "owner" in lease and _identifier(lease["owner"], "invalid_lease") != owner:
            _error("owner_mismatch")
        if "generation" not in lease or "active" not in lease or "expires" not in lease:
            _error("invalid_lease")
        lease_generation = _integer(lease["generation"], "invalid_lease")
        if lease_generation < 1 or _bool(lease["active"], "invalid_lease") is not True:
            _error("invalid_lease")
        if _integer(lease["expires"], "invalid_lease") <= int(time.time()):
            _error("expired_lease")
    else:
        _identifier(lease, "invalid_lease")

    dirty = record.get("dirty_state")
    dirty = _map(dirty, "missing_dirty_state")
    if set(dirty) != DIRTY_STATE_KEYS:
        _error("invalid_dirty_state")
    for value in dirty.values():
        if type(value) is bool:
            continue
        _digest(value, "invalid_dirty_state")

    fingerprints = _fingerprints(record)
    if not CHECKPOINT_REQUIRED_FINGERPRINTS.issubset(fingerprints):
        _error("missing_fingerprints")
    if "requirements_digest" not in record:
        _error("missing_requirements_digest")
    _digest(record["requirements_digest"], "invalid_requirements_digest")
    for key in ("dependency_fingerprint", "toolchain_fingerprint"):
        if key not in record:
            _error(f"missing_{key}")
        _digest(record[key], f"invalid_{key}")
    source = record.get("source_fingerprint")
    checkpoint_source = record.get("checkpoint_fingerprint")
    if source is None or checkpoint_source is None:
        _error("missing_checkpoint_fingerprint")
    source = _digest(source, "invalid_source_fingerprint")
    checkpoint_source = _digest(checkpoint_source, "invalid_checkpoint_fingerprint")
    if source != checkpoint_source:
        _error("fingerprint_mismatch")
    return identity, owner, ticket, phase, lease_generation if lease_generation is not None else 0


def _validate_checkpoint_owner(
    record: dict[str, Any], project_identity: str, owner: str, ticket: str,
    generation: int,
) -> None:
    """Bind a checkpoint to authenticated persistent owner and runtime state."""
    project = _path(record.get("project_root"), "invalid_project_root")
    evidence = load_owner_evidence(record.get("owner_evidence_path"), project)
    _read_runtime_state(evidence, project)
    lease = _map(record.get("lease"), "invalid_lease")
    if evidence["owner"] != owner or evidence["scope"] != ticket:
        _error("owner_evidence_mismatch")
    expected_paths = {
        "project_root": project,
        "worktree": _path(record["worktree"], "invalid_worktree"),
        "common_dir": _path(record["common_dir"], "invalid_common_dir"),
    }
    for key, expected in expected_paths.items():
        if _path(evidence[key], "invalid_owner_evidence") != expected:
            _error("owner_evidence_mismatch")
    if evidence["repo_identity"] != project_identity:
        _error("owner_evidence_mismatch")
    for key in ("branch", "base", "head", "spec_revision"):
        if evidence[key] != record[key]:
            _error("owner_evidence_mismatch")
    for key in (
        "requirements_digest", "spec_digest", "source_fingerprint", "dependency_fingerprint",
        "toolchain_fingerprint", "checkpoint_fingerprint",
    ):
        if evidence[key] != _digest(record[key], "owner_evidence_mismatch"):
            _error("owner_evidence_mismatch")
    if evidence["dirty_state"] != _map(record["dirty_state"], "invalid_dirty_state"):
        _error("owner_evidence_mismatch")
    if evidence["fingerprints"] != _fingerprints(record):
        _error("owner_evidence_mismatch")
    if set(lease) - LEASE_KEYS:
        _error("invalid_lease")
    if _identifier(lease.get("owner"), "invalid_lease") != owner:
        _error("owner_mismatch")
    if _integer(lease.get("generation"), "invalid_lease") != generation:
        _error("generation_mismatch")
    if evidence["lease_id"] != _identifier(lease.get("id"), "invalid_lease"):
        _error("owner_evidence_mismatch")
    expires = _integer(lease.get("expires"), "invalid_lease")
    if evidence["lease_expires"] != expires:
        _error("owner_evidence_mismatch")
    runtime_path = _runtime_state_path(lease.get("runtime_state_path"), project)
    lock_path = _owner_lock_path(lease.get("owner_lock_path"), project)
    if evidence["runtime_state_path"] != runtime_path or evidence["owner_lock_path"] != lock_path:
        _error("owner_evidence_mismatch")
    if evidence["fencing_token"] != _identifier(lease.get("fencing_token"), "invalid_lease"):
        _error("owner_evidence_mismatch")
    _verify_owner_lock(evidence, project, lease.get("owner_lock_fd"))


def _validate_atomic(record: dict[str, Any]) -> tuple[int, str]:
    atomic = _first(record, "atomic_handoff", "handoff")
    if atomic is None:
        atomic_map = record
    else:
        atomic_map = _map(atomic, "invalid_handoff")
        if set(atomic_map) - ATOMIC_KEYS:
            _error("invalid_handoff")
    generation = _first(atomic_map, "generation", "checkpoint_generation")
    digest = _first(atomic_map, "hash", "checkpoint_hash")
    handoff_payload = atomic_map.get("handoff_payload", record.get("handoff_payload"))
    if generation is None or digest is None or handoff_payload is None:
        _error("missing_handoff_metadata")
    generation = _integer(generation, "invalid_generation")
    if generation < 1:
        _error("invalid_generation")
    digest = _digest(digest, "invalid_handoff_hash")
    handoff_payload = _map(handoff_payload, "invalid_handoff_payload")
    if not handoff_payload or any(key in handoff_payload for key in ("hash", "checkpoint_hash")):
        _error("invalid_handoff_payload")
    _reject_transcript_keys(handoff_payload)
    try:
        canonical = json.dumps(
            handoff_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError):
        _error("invalid_handoff_payload")
    if len(canonical) > MAX_PACKET_BYTES:
        _error("handoff_too_large")
    if hashlib.sha256(canonical).hexdigest() != digest:
        _error("handoff_hash_mismatch")
    return generation, digest


def _validate_acknowledgement(
    value: Any, owner: str, generation: int, handoff_hash: str, successor: str | None,
) -> str:
    if not isinstance(value, dict) or set(value) != ACK_KEYS:
        _error("invalid_acknowledgement")
    state = value.get("state")
    if _identifier(value.get("owner"), "invalid_acknowledgement") != owner:
        _error("owner_mismatch")
    if _integer(value.get("generation"), "invalid_acknowledgement") != generation:
        _error("generation_mismatch")
    if _digest(value.get("handoff_hash"), "invalid_acknowledgement") != handoff_hash:
        _error("handoff_hash_mismatch")
    acknowledged_successor = value.get("successor")
    if successor is None:
        if acknowledged_successor is not None:
            _error("successor_mismatch")
    elif _identifier(acknowledged_successor, "invalid_acknowledgement") != successor:
        _error("successor_mismatch")
    if state not in {"pending", "acknowledged", "accepted"}:
        _error("invalid_acknowledgement")
    return state


def _validate_checkpoint(payload: dict[str, Any], action: str) -> dict[str, Any]:
    record = _checkpoint_record(payload)
    version = record.get("schema_version", 1)
    if type(version) is not int or version != 1:
        _error("unsupported_schema")
    expected = record.get("expected_fingerprints")
    if expected is not None:
        expected_map = _map(expected, "invalid_fingerprints")
        if set(expected_map) - FINGERPRINT_KEYS:
            _error("invalid_fingerprints")
        actual = _fingerprints(record)
        for key, value in expected_map.items():
            if key not in actual or actual[key] != _digest(value, "invalid_fingerprints"):
                _error("fingerprint_mismatch")

    compaction_fields = [
        record[key] for key in ("compaction_count", "root_compactions", "compactions")
        if key in record
    ]
    if not compaction_fields:
        _error("missing_compaction_count")
    if len(compaction_fields) > 1:
        _error("invalid_compactions")
    compactions = compaction_fields[0] if compaction_fields else None
    if isinstance(compactions, dict):
        if not compactions or set(compactions) - {"root", "primary", "implementation"}:
            _error("invalid_compactions")
        for value in compactions.values():
            _integer(value, "invalid_compactions")
        authorities = [key for key in ("root", "primary") if key in compactions]
        if len(authorities) != 1:
            _error("invalid_compactions")
        compactions = compactions[authorities[0]]
    if compactions is not None and _integer(compactions, "invalid_compactions") > 1:
        _error("second_compaction")
    for stale_key in ("stale", "checkpoint_stale", "fresh"):
        if stale_key in record and type(record[stale_key]) is not bool:
            _error("invalid_stale")
    if record.get("stale") is True or record.get("checkpoint_stale") is True or record.get("fresh") is False:
        _error("stale_checkpoint")
    status = record.get("status")
    if status is not None:
        _string(status, "invalid_status")
    if status in {"stale", "invalid", "unknown"}:
        _error("stale_checkpoint")

    identity, owner, ticket, phase, lease_generation = _validate_checkpoint_binding(record)
    generation, handoff_hash = _validate_atomic(record)
    envelope_generation = _first(record, "checkpoint_generation", "generation")
    if envelope_generation is None or _integer(envelope_generation, "invalid_generation") != generation:
        _error("generation_mismatch")
    _validate_checkpoint_owner(record, identity, owner, ticket, generation)
    atomic_payload = _first(record, "handoff_payload")
    if atomic_payload is None:
        atomic = _first(record, "atomic_handoff", "handoff")
        if isinstance(atomic, dict):
            atomic_payload = atomic.get("handoff_payload")
    _validate_handoff_payload(record, _map(atomic_payload, "missing_handoff_metadata"), generation, identity, owner, ticket, phase)
    if lease_generation != generation:
        _error("generation_mismatch")
    successor_count = record.get("successor_count")
    successors = record.get("successors")
    if successors is not None:
        if not isinstance(successors, list):
            _error("invalid_successors")
        for successor in successors:
            _identifier(successor, "invalid_successors")
    if successor_count is None and isinstance(successors, list):
        successor_count = len(successors)
    if successor_count is None:
        _error("missing_successor_count")
    successor_count = _integer(successor_count, "invalid_successor_count")
    if successor_count > 1 or (isinstance(successors, list) and len(successors) > 1):
        _error("duplicate_successor")
    if isinstance(successors, list) and len(successors) != successor_count:
        _error("successor_mismatch")
    if action == "handoff":
        if successor_count != 1 or not isinstance(successors, list) or len(successors) != 1:
            _error("missing_successor")
    elif successor_count == 1 and not isinstance(successors, list):
        _error("successor_mismatch")
    acknowledgement_value = _first(record, "acknowledgement", "ack", "acknowledgement_state")
    if acknowledgement_value is None:
        _error("missing_acknowledgement")
    expected_successor = successors[0] if isinstance(successors, list) and successors else None
    acknowledgement = _validate_acknowledgement(
        acknowledgement_value, owner, generation, handoff_hash, expected_successor,
    )
    if action == "handoff" and acknowledgement == "pending":
        _error("successor_not_acknowledged")
    return {
        "decision": "allow",
        "code": f"{action}_allowed",
        "action": action,
        "generation": generation,
        "handoff_hash": handoff_hash,
        "successor_count": successor_count,
        "acknowledgement": acknowledgement,
        "repo_identity": identity,
        "owner": owner,
        "ticket": ticket,
        "phase": phase,
    }


PACKET_KEYS = {
    "model", "reasoning", "reasoning_effort", "fork_turns", "objective", "query", "acceptance",
    "paths", "allowlisted_paths", "verification", "requirements_digest", "spec_revision", "spec_digest",
    "role", "task", "task_id", "ticket", "phase", "project_root", "cwd", "worktree", "repo",
    "repo_identity", "branch", "base", "head", "tree", "read_scope", "write_scope", "output_cap",
    "citation_format", "source_fingerprint", "dependency_fingerprint", "toolchain_fingerprint",
    "artifact_fingerprint", "negative_evidence", "constraints", "handoff_path",
    "owner", "checkpoint_fingerprint", "hash_format",
    "generation",
}
LUNA_ENVELOPE_KEYS = {"action", "command", "packet"}
HANDOFF_FILE_VERSION = 1
HANDOFF_FILE_KEYS = {
    "schema_version", "generation", "owner", "ticket", "phase", "project_root", "worktree",
    "head", "source_fingerprint", "checkpoint_fingerprint", "requirements_digest",
    "packet_fingerprint", "hash",
}


def _safe_handoff_path(value: Any, *, forbidden_roots: tuple[str, ...] = ()) -> str:
    """Validate only the lexical handoff location before same-FD opening."""
    raw = _string(value, "invalid_handoff_path")
    if "\x00" in raw or not os.path.isabs(raw):
        _error("invalid_handoff_path")
    normalized = os.path.normpath(raw)
    temp_root = os.path.realpath(tempfile.gettempdir())
    candidate = os.path.abspath(normalized)
    parent = os.path.realpath(os.path.dirname(candidate))
    physical_candidate = os.path.join(parent, os.path.basename(candidate))
    if (
        physical_candidate == temp_root
        or not _within(physical_candidate, temp_root)
        or not _within(parent, temp_root)
    ):
        _error("invalid_handoff_path")
    for root in forbidden_roots:
        forbidden = os.path.realpath(os.path.abspath(root))
        if _within(physical_candidate, forbidden) or _within(parent, forbidden):
            _error("invalid_handoff_path")
    return normalized


def _packet_digest(packet: dict[str, Any]) -> str:
    try:
        canonical = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        _error("invalid_packet")
    return hashlib.sha256(canonical).hexdigest()


def _read_handoff_binding(
    path: Any,
    packet: dict[str, Any],
    task: str,
    *,
    forbidden_roots: tuple[str, ...] = (),
) -> None:
    """Open the handoff once without following the final symlink, then bind it."""
    resolved = _safe_handoff_path(path, forbidden_roots=forbidden_roots)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError:
        _error("invalid_handoff_path")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            _error("invalid_handoff_path")
        try:
            raw = os.read(descriptor, MAX_PACKET_BYTES + 1)
        except OSError:
            _error("invalid_handoff_path")
        if len(raw) > MAX_PACKET_BYTES:
            _error("handoff_too_large")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _error("invalid_handoff_payload")
        handoff = _map(value, "invalid_handoff_payload")
        if set(handoff) != HANDOFF_FILE_KEYS or handoff.get("schema_version") != HANDOFF_FILE_VERSION:
            _error("invalid_handoff_payload")
        unsigned = {key: item for key, item in handoff.items() if key != "hash"}
        try:
            canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            _error("invalid_handoff_payload")
        if _digest(handoff.get("hash"), "invalid_handoff_payload") != hashlib.sha256(canonical).hexdigest():
            _error("handoff_hash_mismatch")
        if _integer(handoff.get("generation"), "invalid_handoff_payload") != _integer(packet.get("generation"), "invalid_generation"):
            _error("generation_mismatch")
        if _identifier(handoff.get("owner"), "invalid_handoff_payload") != packet["owner"]:
            _error("handoff_binding_mismatch")
        if _identifier(handoff.get("ticket"), "invalid_handoff_payload") != task:
            _error("handoff_binding_mismatch")
        if _identifier(handoff.get("phase"), "invalid_handoff_payload") != packet["phase"]:
            _error("handoff_binding_mismatch")
        for key in ("project_root", "worktree"):
            if _path(handoff[key], "invalid_handoff_payload") != _path(packet[key], "invalid_handoff_payload"):
                _error("handoff_binding_mismatch")
        for key in ("head", "source_fingerprint", "checkpoint_fingerprint", "requirements_digest"):
            if handoff[key] != packet[key]:
                _error("handoff_binding_mismatch")
        if _digest(handoff.get("packet_fingerprint"), "invalid_handoff_payload") != _packet_digest(packet):
            _error("handoff_binding_mismatch")
    finally:
        os.close(descriptor)


def _string_list(value: Any, code: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        _error(code)
    for item in value:
        _string(item, code)
    return value


def _packet_path_list(value: Any, code: str) -> list[str]:
    paths = _string_list(value, code)
    for item in paths:
        if item.startswith("~") or "\x00" in item:
            _error(code)
        normalized = os.path.normpath(item)
        if normalized == ".." or normalized.startswith(f"..{os.sep}"):
            _error(code)
    return paths


def _scoped_packet_paths(value: Any, worktree: str, code: str, *, allow_empty: bool = False) -> set[str]:
    paths = _string_list(value, code, allow_empty=allow_empty)
    resolved: set[str] = set()
    for item in paths:
        if item.startswith("~") or "\x00" in item:
            _error(code)
        candidate = item if os.path.isabs(item) else os.path.join(worktree, item)
        candidate = os.path.realpath(os.path.normpath(candidate))
        if not _within(candidate, worktree):
            _error(code)
        resolved.add(candidate)
    return resolved


def _validate_luna(payload: dict[str, Any]) -> dict[str, Any]:
    if "packet" in payload:
        if set(payload) - LUNA_ENVELOPE_KEYS:
            _error("invalid_packet_schema")
        packet_value = payload["packet"]
    else:
        packet_value = payload
    packet_value = _map(packet_value, "invalid_packet")
    # Direct packets may carry the dispatch selector; it is envelope metadata,
    # never part of the Luna packet schema itself.  A nested packet is already
    # the exact packet object, so action/command are rejected there as extras.
    if "packet" in payload:
        packet = dict(packet_value)
    else:
        packet = {
            key: value for key, value in packet_value.items()
            if key not in {"action", "command"}
        }
    _reject_transcript_keys(packet)
    if set(packet) - PACKET_KEYS:
        _error("invalid_packet_schema")
    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_PACKET_BYTES:
        _error("packet_too_large")
    if packet.get("model") != "gpt-5.6-luna":
        _error("invalid_model")
    reasoning_values = [packet[key] for key in ("reasoning", "reasoning_effort") if key in packet]
    if not reasoning_values or any(value != "xhigh" for value in reasoning_values):
        _error("invalid_reasoning")
    if packet.get("fork_turns") != "none":
        _error("invalid_fork_turns")
    required = (
        "role", "owner", "phase", "objective", "project_root", "cwd", "worktree",
        "repo_identity", "branch", "base", "head", "tree", "paths", "read_scope",
        "write_scope", "acceptance", "verification", "constraints", "requirements_digest",
        "spec_revision", "spec_digest", "source_fingerprint", "dependency_fingerprint",
        "toolchain_fingerprint", "artifact_fingerprint", "checkpoint_fingerprint",
        "output_cap", "citation_format", "hash_format", "handoff_path", "generation",
    )
    for key in required:
        if key not in packet or packet[key] in (None, "", {}) or (key != "write_scope" and packet[key] == []):
            _error(f"missing_{key}")
    task = _first(packet, "task", "task_id", "ticket")
    if task is None:
        _error("missing_task")
    _identifier(task)
    for key in ("role", "owner", "phase", "branch", "base", "head", "tree", "spec_revision"):
        _identifier(packet[key])
    project_root = _path(packet["project_root"], "invalid_project_root")
    cwd = _path(packet["cwd"], "invalid_cwd")
    worktree = _path(packet["worktree"], "invalid_worktree")
    forbidden_handoff_roots = (project_root, worktree)
    handoff_path = _safe_handoff_path(
        packet["handoff_path"], forbidden_roots=forbidden_handoff_roots
    )
    managed_root = os.path.join(project_root, ".codex", "worktrees")
    if worktree != project_root and not _managed_worktree_root(worktree, managed_root):
        _error("external_worktree")
    if not _within(cwd, worktree):
        _error("cwd_mismatch")
    identity, bare, common_dir = _repo_identity(packet["repo_identity"])
    _validate_repo_path(packet["repo_identity"], project_root)
    if bare:
        _error("bare_worktree")
    if common_dir is None:
        _error("missing_common_dir")
    if not _within(common_dir, project_root):
        _error("repo_mismatch")
    _string(packet["objective"], "invalid_objective")
    allowlisted = _scoped_packet_paths(packet["paths"], worktree, "invalid_paths")
    read_scope = _scoped_packet_paths(packet["read_scope"], worktree, "invalid_read_scope")
    write_scope = _scoped_packet_paths(packet["write_scope"], worktree, "invalid_write_scope", allow_empty=True)
    if not read_scope.issubset(allowlisted) or not write_scope.issubset(allowlisted):
        _error("scope_not_allowlisted")
    for key in ("acceptance", "verification", "constraints"):
        _string_list(packet[key], f"invalid_{key}")
    for key in (
        "requirements_digest", "spec_digest", "source_fingerprint", "dependency_fingerprint",
        "toolchain_fingerprint", "artifact_fingerprint", "checkpoint_fingerprint",
    ):
        _digest(packet[key], f"invalid_{key}")
    output_cap = _integer(packet["output_cap"], "invalid_output_cap")
    if output_cap == 0 or output_cap > MAX_OUTPUT_CAP:
        _error("invalid_output_cap")
    _string(packet["citation_format"], "invalid_citation_format")
    _string(packet["hash_format"], "invalid_hash_format")
    generation = _integer(packet["generation"], "invalid_generation")
    if generation < 1:
        _error("invalid_generation")
    _read_handoff_binding(
        handoff_path,
        packet,
        task,
        forbidden_roots=forbidden_handoff_roots,
    )
    _read_supervisor_capability(
        project_root,
        worktree,
        identity,
        expected_owner=packet["owner"],
        expected_scope=task,
        expected_generation=generation,
    )
    return {
        "decision": "allow",
        "code": "luna_bootstrap_allowed",
        "action": "luna_bootstrap",
        "model": "gpt-5.6-luna",
        "reasoning": "xhigh",
        "fork_turns": "none",
        "generation": generation,
    }


def evaluate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        _error("invalid_schema")
    action = payload.get("action", payload.get("command"))
    if action == "validate_pre_mutation_evidence":
        checked = dict(payload)
        checked["action"] = "pre_mutation"
        result = _validate_pre_mutation(checked)
        return {
            **result,
            "decision": "evidence",
            "code": "pre_mutation_evidence_valid",
            "action": "validate_pre_mutation_evidence",
        }
    if action in {"pre_mutation", "pre-mutation", "pre_action", "prepare_worktree"}:
        _validate_pre_mutation(payload)
        _error("host_event_required")
    if action in {"checkpoint", "handoff", "checkpoint_handoff", "validate_handoff"}:
        result = _validate_checkpoint(
            payload, "handoff" if action in {"handoff", "validate_handoff"} else "checkpoint",
        )
        return {**result, "decision": "evidence", "code": f"{result['action']}_evidence_valid"}
    if action in {"luna", "luna_bootstrap", "bootstrap_luna", "bootstrap"}:
        result = _validate_luna(payload)
        return {**result, "decision": "evidence", "code": "luna_bootstrap_evidence_valid"}
    if action is None and "packet" in payload:
        result = _validate_luna(payload)
        return {**result, "decision": "evidence", "code": "luna_bootstrap_evidence_valid"}
    _error("unknown_action")


def _emit(result: dict[str, Any], status: int) -> NoReturn:
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    raise SystemExit(status)


def main() -> None:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        _emit({"decision": "block", "code": "input_too_large"}, 2)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        _emit({"decision": "block", "code": "invalid_json"}, 2)
    try:
        result = evaluate(payload)
    except GateError as error:
        _emit({"decision": "block", "code": error.code}, 2)
    # Keep this digest calculation local and deterministic; it is not used as
    # authorization and avoids exposing the input or any path in the output.
    _ = hashlib.sha256(raw).digest()
    _emit(result, 0)


if __name__ == "__main__":
    main()
