#!/usr/bin/env python3
"""Collect and validate APR repository evidence without granting authority."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import sys
import termios
import tempfile
import time
from typing import Any, NoReturn

import runtime_gate


MAX_INPUT_BYTES = runtime_gate.MAX_INPUT_BYTES
SUPERVISOR_FDS = (198, 199, 200)
SUPERVISED_ENV = "CODEX_APR_PROBE_SUPERVISED"
INSTALLED_PROBE = Path.home() / ".codex" / "skills" / "autonomous-project-run" / "scripts" / "runtime_probe.py"
INSTALLED_SUPERVISOR = Path.home() / ".codex" / "runtime-controls" / "apr_probe_supervisor.py"
SUPERVISOR_SHA256 = "228df79d02489052e93569f964a6ff53bbf10495da798f3a569d355b5ca3d947"
MAX_SUPERVISOR_BYTES = 2 * 1024 * 1024
PTY_FRAME_TIMEOUT_SECONDS = 30.0
SUPERVISOR_LOADER = """\
import os
import sys

path = sys.argv[1]
descriptor = int(sys.argv[2])
remaining = int(sys.argv[3]) + 1
parent = os.path.dirname(path)
if not os.path.isabs(path) or not parent:
    raise SystemExit("verified supervisor path is invalid")
sys.path.insert(0, parent)
chunks = []
while remaining > 0:
    chunk = os.read(descriptor, min(128 * 1024, remaining))
    if not chunk:
        break
    chunks.append(chunk)
    remaining -= len(chunk)
os.close(descriptor)
if remaining <= 0:
    raise SystemExit("verified supervisor exceeds size limit")
sys.argv = [path, *sys.argv[4:]]
namespace = {"__name__": "__main__", "__file__": path}
exec(compile(b"".join(chunks), path, "exec"), namespace, namespace)
"""
AUTHORITATIVE_KEYS = {
    "project_root", "cwd", "worktree", "repo", "repo_identity",
    "common_dir", "is_bare", "bare", "branch", "head", "dirty_state", "fingerprints",
}
OWNER_RELEASE_ARGUMENT = "--release-lease"
MANAGED_WORKTREE_ARGUMENT = "--managed-worktree"

# Untracked files are attacker-controlled input.  Keep their enumeration and
# content reads deterministic so a FIFO/huge tree cannot hold the gate open.
MAX_TRACKED_FILE_BYTES = 64 * 1024 * 1024
MAX_UNTRACKED_FILE_BYTES = 8 * 1024 * 1024
MAX_UNTRACKED_TOTAL_BYTES = 32 * 1024 * 1024
MAX_UNTRACKED_COUNT = 4096
MAX_UNTRACKED_LIST_BYTES = 4 * 1024 * 1024
MAX_GIT_OUTPUT_BYTES = 64 * 1024 * 1024
GIT_PROBE_TIMEOUT_SECONDS = 10.0
UNTRACKED_DEADLINE_SECONDS = 2.0


class ProbeError(ValueError):
    pass


def _git(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise ProbeError("git_probe_timeout") from error
    except OSError as error:
        raise ProbeError("git_probe_failed") from error
    if result.returncode != 0:
        raise ProbeError("git_probe_failed")
    return result.stdout.strip()


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    """Stop a timed-out/over-limit read and reap the child process."""
    try:
        if process.poll() is None:
            process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _git_bytes(cwd: Path, *args: str, max_output_bytes: int | None = None) -> bytes:
    """Read bounded Git output without waiting for an unbounded EOF."""
    output_limit = max_output_bytes if max_output_bytes is not None else MAX_GIT_OUTPUT_BYTES
    limit_code = "untracked_probe_limit" if max_output_bytes is not None else "git_probe_limit"
    deadline = time.monotonic() + GIT_PROBE_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(
            ["git", "-C", str(cwd), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as error:
        raise ProbeError("git_probe_failed") from error

    if process.stdout is None:
        _terminate_process(process)
        raise ProbeError("git_probe_failed")
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        output = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError("git_probe_timeout")
            try:
                events = selector.select(remaining)
            except OSError as error:
                raise ProbeError("git_probe_failed") from error
            if not events:
                raise ProbeError("git_probe_timeout")
            read_size = min(1024 * 1024, max(1, output_limit - len(output) + 1))
            try:
                chunk = os.read(process.stdout.fileno(), read_size)
            except OSError as error:
                raise ProbeError("git_probe_failed") from error
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > output_limit:
                raise ProbeError(limit_code)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("git_probe_timeout")
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise ProbeError("git_probe_timeout") from error
        if return_code != 0:
            raise ProbeError("git_probe_failed")
        return bytes(output)
    except ProbeError:
        _terminate_process(process)
        raise
    finally:
        selector.close()
        process.stdout.close()


def _digest(value: bytes | str) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _indexed_paths(index_bytes: bytes) -> list[bytes]:
    """Extract raw tracked paths from `ls-files --stage -z` output."""
    paths: list[bytes] = []
    for entry in index_bytes.split(b"\0"):
        if not entry:
            continue
        try:
            # `ls-files --stage` uses a tab between the stage metadata and
            # the raw path; splitting on the final space corrupts paths and
            # omits the tracked file content from the manifest.
            paths.append(entry.split(b"\t", 1)[1])
        except IndexError as error:
            raise ProbeError("git_probe_failed") from error
    return paths


def _indexed_objects(index_bytes: bytes) -> dict[bytes, tuple[bytes, bytes]]:
    """Extract mode/object-id pairs without reading clean tracked files."""
    objects: dict[bytes, tuple[bytes, bytes]] = {}
    for entry in index_bytes.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            mode, object_id, _stage = metadata.split(b" ", 2)
        except ValueError as error:
            raise ProbeError("git_probe_failed") from error
        if len(object_id) != 40 or any(char not in b"0123456789abcdef" for char in object_id):
            raise ProbeError("git_probe_failed")
        objects[raw_path] = (mode, object_id)
    return objects


def _changed_tracked_paths(cwd: Path, tracked_paths: set[bytes]) -> set[bytes]:
    """Return tracked paths whose worktree/index content may differ from HEAD."""
    changed: set[bytes] = set()
    for diff_args in (
        ("diff", "--cached", "--name-only", "-z", "--no-renames", "--"),
        ("diff", "--name-only", "-z", "--no-renames", "--"),
    ):
        raw = _git_bytes(cwd, *diff_args)
        changed.update(entry for entry in raw.split(b"\0") if entry in tracked_paths)
    return changed


def _indexed_submodule_paths(index_bytes: bytes) -> list[bytes]:
    """Extract gitlink paths so nested repositories cannot hide dirty state."""
    paths: list[bytes] = []
    for entry in index_bytes.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            mode = metadata.split(b" ", 1)[0]
        except ValueError as error:
            raise ProbeError("git_probe_failed") from error
        if mode == b"160000":
            paths.append(raw_path)
    return paths


def _ensure_submodules_clean(worktree: str, index_bytes: bytes) -> None:
    """Fail closed when an indexed submodule is dirty or unavailable."""
    root = _real(worktree)
    for raw_path in _indexed_submodule_paths(index_bytes):
        relative = os.fsdecode(raw_path)
        submodule = _real(os.path.join(root, relative))
        if not _within_path(submodule, root) or not os.path.isdir(submodule):
            raise ProbeError("submodule_probe_failed")
        try:
            if _dirty(Path(root), "diff", "--cached", "--quiet", "--ignore-submodules=none", "--", relative):
                raise ProbeError("dirty_submodule")
            if _dirty(Path(root), "diff", "--quiet", "--ignore-submodules=none", "--", relative):
                raise ProbeError("dirty_submodule")
            status = _git_bytes(Path(submodule), "status", "--porcelain=v1", "--ignore-submodules=none", "--untracked-files=all")
        except ProbeError as error:
            if str(error) == "git_probe_failed":
                raise ProbeError("submodule_probe_failed") from error
            raise
        if status:
            raise ProbeError("dirty_submodule")


def _within_path(path: str, parent: str) -> bool:
    try:
        return os.path.commonpath((path, parent)) == parent
    except ValueError:
        return False


def _untracked_paths(untracked_bytes: bytes) -> list[bytes]:
    return [entry for entry in untracked_bytes.split(b"\0") if entry]


def _path_components(raw_path: bytes) -> tuple[bytes, ...]:
    if not raw_path or raw_path.startswith(b"/"):
        raise ProbeError("git_probe_failed")
    components = tuple(raw_path.split(b"/"))
    if any(component in {b"", b".", b".."} for component in components):
        raise ProbeError("git_probe_failed")
    return components


def _close_fd(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _open_path_parent(worktree: str, components: tuple[bytes, ...]) -> tuple[int, int]:
    """Open a path's parent with no-follow directory components."""
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open(worktree, directory_flags | getattr(os, "O_CLOEXEC", 0))
    except (AttributeError, OSError) as error:
        raise ProbeError("git_probe_failed") from error
    current_fd = root_fd
    for component in components[:-1]:
        try:
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
        except OSError as error:
            if current_fd != root_fd:
                _close_fd(current_fd)
            _close_fd(root_fd)
            raise ProbeError("git_probe_failed") from error
        if current_fd != root_fd:
            _close_fd(current_fd)
        current_fd = next_fd
    return root_fd, current_fd


def _read_regular_entry(
    directory_fd: int,
    leaf: bytes,
    info: os.stat_result,
    *,
    max_bytes: int | None = None,
    total_budget: list[int] | None = None,
    deadline: float | None = None,
    limit_code: str = "source_probe_limit",
) -> tuple[bytes, bytes]:
    """Read a regular leaf and bind the descriptor to its initial inode."""
    # O_NONBLOCK keeps a replacement FIFO/device from stalling the probe while
    # the descriptor is being bound to the original regular inode.
    leaf_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if deadline is not None and time.monotonic() > deadline:
        raise ProbeError(limit_code)
    try:
        descriptor = os.open(leaf, leaf_flags, dir_fd=directory_fd)
    except OSError as error:
        raise ProbeError("git_probe_failed") from error
    try:
        descriptor_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(descriptor_info.st_mode)
            or descriptor_info.st_dev != info.st_dev
            or descriptor_info.st_ino != info.st_ino
        ):
            raise ProbeError("git_probe_failed")
        digest = hashlib.sha256()
        bytes_read = 0
        while True:
            if deadline is not None and time.monotonic() > deadline:
                raise ProbeError(limit_code)
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            bytes_read += len(chunk)
            if max_bytes is not None and bytes_read > max_bytes:
                raise ProbeError(limit_code)
            if total_budget is not None:
                total_budget[0] += len(chunk)
                if total_budget[0] > MAX_UNTRACKED_TOTAL_BYTES:
                    raise ProbeError("untracked_probe_limit")
            digest.update(chunk)
        final_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(final_info.st_mode)
            or final_info.st_dev != info.st_dev
            or final_info.st_ino != info.st_ino
        ):
            raise ProbeError("git_probe_failed")
        mode_bits = str(descriptor_info.st_mode & 0o777).encode("ascii")
        return b"file", mode_bits + b"\0" + digest.digest()
    except OSError as error:
        raise ProbeError("git_probe_failed") from error
    finally:
        _close_fd(descriptor)


def _read_path_entry(
    worktree: str,
    raw_path: bytes,
    *,
    max_bytes: int | None = None,
    total_budget: list[int] | None = None,
    deadline: float | None = None,
    limit_code: str = "source_probe_limit",
) -> tuple[bytes, bytes]:
    """Read one path through a no-follow directory-fd walk rooted at worktree."""
    components = _path_components(raw_path)
    root_fd, current_fd = _open_path_parent(worktree, components)
    try:
        leaf = components[-1]

        try:
            info = os.lstat(leaf, dir_fd=current_fd)
        except FileNotFoundError:
            return b"missing", b""
        except OSError as error:
            raise ProbeError("git_probe_failed") from error

        if stat.S_ISLNK(info.st_mode):
            try:
                return b"symlink", os.readlink(leaf, dir_fd=current_fd)
            except OSError as error:
                raise ProbeError("git_probe_failed") from error
        if not stat.S_ISREG(info.st_mode):
            return b"other", str(info.st_mode & 0o170000).encode("ascii")
        return _read_regular_entry(
            current_fd,
            leaf,
            info,
            max_bytes=max_bytes,
            total_budget=total_budget,
            deadline=deadline,
            limit_code=limit_code,
        )
    finally:
        if current_fd != root_fd:
            _close_fd(current_fd)
        _close_fd(root_fd)


def _path_content_digest(
    worktree: str,
    paths: set[bytes],
    *,
    tracked_objects: dict[bytes, tuple[bytes, bytes]] | None = None,
    untracked_paths: set[bytes] | None = None,
    changed_tracked_paths: set[bytes] | None = None,
) -> str:
    """Hash a stable, sorted path+content manifest for tracked/untracked files."""
    manifest: list[bytes] = []
    untracked = untracked_paths or set()
    changed = changed_tracked_paths or set()
    if len(untracked) > MAX_UNTRACKED_COUNT:
        raise ProbeError("untracked_probe_limit")
    deadline = time.monotonic() + UNTRACKED_DEADLINE_SECONDS if untracked else None
    total_budget = [0]
    for raw_path in sorted(paths):
        if deadline is not None and time.monotonic() > deadline:
            raise ProbeError("untracked_probe_limit")
        indexed = tracked_objects.get(raw_path) if tracked_objects is not None else None
        if indexed is not None and raw_path not in untracked and raw_path not in changed:
            mode, object_id = indexed
            kind, content = b"git-object", mode + b"\0" + object_id
        else:
            is_untracked = raw_path in untracked
            kind, content = _read_path_entry(
                worktree,
                raw_path,
                max_bytes=MAX_UNTRACKED_FILE_BYTES if is_untracked else MAX_TRACKED_FILE_BYTES,
                total_budget=total_budget if is_untracked else None,
                deadline=deadline,
                limit_code="untracked_probe_limit" if is_untracked else "source_probe_limit",
            )
        manifest.extend((raw_path, b"\0", kind, b"\0", _digest(content).encode("ascii"), b"\0"))
    return _digest(b"".join(manifest))


def _dirty(cwd: Path, *args: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            timeout=GIT_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise ProbeError("git_probe_timeout") from error
    except OSError as error:
        raise ProbeError("git_probe_failed") from error
    if result.returncode not in (0, 1):
        raise ProbeError("git_probe_failed")
    return result.returncode == 1


def _real(value: str) -> str:
    return os.path.realpath(os.path.abspath(value))


def _registered_worktree_roots(cwd: Path) -> list[str]:
    fields = _git_bytes(cwd, "worktree", "list", "--porcelain", "-z").split(b"\0")
    roots = [
        _real(os.fsdecode(field.removeprefix(b"worktree ")))
        for field in fields
        if field.startswith(b"worktree ")
    ]
    if not roots:
        raise ProbeError("git_probe_failed")
    return roots


def _project_root(worktree: str, common_dir: str, roots: list[str]) -> str:
    managed_marker = f"{os.sep}.codex{os.sep}worktrees{os.sep}"
    normalized_worktree = _real(worktree)
    normalized_roots = {_real(root) for root in roots}
    managed_parent = Path(normalized_worktree).parent
    if managed_parent.name == "worktrees" and managed_parent.parent.name == ".codex":
        candidate = _real(str(managed_parent.parent.parent))
        if managed_marker in candidate or candidate not in normalized_roots:
            raise ProbeError("ambiguous_project_root")
        return candidate
    common_path = Path(common_dir)
    common_candidate = _real(str(common_path.parent)) if common_path.name == ".git" else None
    candidate = common_candidate if common_candidate in normalized_roots else _real(roots[0])
    if managed_marker in candidate:
        raise ProbeError("ambiguous_project_root")
    return candidate


def _filter_untracked(
    worktree: str,
    raw: bytes,
    excluded_paths: set[str],
    registered_worktrees: set[str],
) -> bytes:
    excluded = {_real(path) for path in excluded_paths}
    kept: list[bytes] = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        if entry.endswith(b"/"):
            marker = entry[:-1]
            _path_components(marker)
            marker_path = _real(os.path.join(worktree, os.fsdecode(marker)))
            if marker_path in registered_worktrees and marker_path != _real(worktree):
                continue
            raise ProbeError("untracked_nested_repository")
        _path_components(entry)
        relative = os.fsdecode(entry)
        absolute = _real(os.path.join(worktree, relative))
        if absolute not in excluded:
            kept.append(entry)
    return b"\0".join(kept) + (b"\0" if kept else b"")


def collect(cwd: Path | None = None, *, exclude_paths: set[str] | None = None) -> dict[str, Any]:
    cwd = (cwd or Path.cwd()).resolve()
    bare = _git(cwd, "rev-parse", "--is-bare-repository") == "true"
    if bare:
        raise ProbeError("bare_worktree")
    worktree = _real(_git(cwd, "rev-parse", "--show-toplevel"))
    common_raw = _git(cwd, "rev-parse", "--git-common-dir")
    common_dir = _real(common_raw if os.path.isabs(common_raw) else os.path.join(worktree, common_raw))
    registered_worktrees = _registered_worktree_roots(cwd)
    project = _project_root(worktree, common_dir, registered_worktrees)
    head = _git(cwd, "rev-parse", "HEAD")
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    identity = hashlib.sha256(f"{project}\0{common_dir}".encode()).hexdigest()
    index_bytes = _git_bytes(cwd, "ls-files", "--stage", "-z")
    _ensure_submodules_clean(worktree, index_bytes)
    untracked_bytes = _filter_untracked(
        worktree,
        _git_bytes(
            cwd,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            max_output_bytes=MAX_UNTRACKED_LIST_BYTES,
        ),
        exclude_paths or set(),
        set(registered_worktrees),
    )
    tree = _git(cwd, "rev-parse", "HEAD^{tree}")
    index_digest = _digest(index_bytes)
    tree_digest = _digest(tree)
    untracked_digest = _digest(untracked_bytes)
    dirty_state = {
        "index": _dirty(cwd, "diff", "--cached", "--quiet"),
        "worktree": _dirty(cwd, "diff", "--quiet"),
        "untracked": bool(untracked_bytes),
    }
    indexed_objects = _indexed_objects(index_bytes)
    tracked_paths = set(indexed_objects)
    changed_tracked_paths = _changed_tracked_paths(cwd, tracked_paths)
    content_digest = _path_content_digest(
        worktree,
        tracked_paths.union(_untracked_paths(untracked_bytes)),
        tracked_objects=indexed_objects,
        untracked_paths=set(_untracked_paths(untracked_bytes)),
        changed_tracked_paths=changed_tracked_paths,
    )
    fingerprints = {
        "repo": _digest(f"{project}\0{common_dir}"),
        "project": _digest(project),
        "worktree": _digest(worktree),
        "head": _digest(head),
        "index": index_digest,
        "tree": tree_digest,
        "untracked": untracked_digest,
        "content": content_digest,
        "source": _digest(f"{index_digest}\0{tree_digest}\0{untracked_digest}\0{content_digest}"),
    }
    return {
        "project_root": project,
        "cwd": str(cwd),
        "worktree": worktree,
        "repo_identity": {
            "identity": identity,
            "path": project,
            "is_bare": False,
            "common_dir": common_dir,
        },
        "common_dir": common_dir,
        "is_bare": False,
        "branch": branch,
        "head": head,
        "dirty_state": dirty_state,
        "fingerprints": fingerprints,
    }


def _compare_claims(payload: dict[str, Any], facts: dict[str, Any]) -> None:
    for key in AUTHORITATIVE_KEYS.intersection(payload):
        if key == "fingerprints":
            supplied = payload[key]
            if not isinstance(supplied, dict):
                raise ProbeError("probe_mismatch")
            # The gate's pre-mutation schema also carries requirement/spec and
            # dependency/toolchain digests.  Only the Git-derived subset is
            # authoritative here; runtime_gate binds the remaining fields to
            # the durable owner evidence before allowing mutation.
            for fingerprint, expected in facts["fingerprints"].items():
                if supplied.get(fingerprint) != expected:
                    raise ProbeError("probe_mismatch")
            continue
        expected: Any = facts.get(key)
        if key == "repo":
            expected = facts["repo_identity"]
        if key in {"bare", "is_bare"}:
            expected = False
        if payload[key] != expected:
            raise ProbeError("probe_mismatch")


def _compare_owner_evidence(payload: dict[str, Any], facts: dict[str, Any]) -> None:
    evidence = runtime_gate.load_owner_evidence(payload.get("owner_evidence_path"), facts["project_root"])
    repo_identity = facts["repo_identity"]["identity"]
    for key, expected in {
        "project_root": facts["project_root"],
        "worktree": facts["worktree"],
        "common_dir": facts["common_dir"],
        "repo_identity": repo_identity,
        "branch": facts["branch"],
        "head": facts["head"],
    }.items():
        actual = evidence[key]
        if key in {"project_root", "worktree", "common_dir"}:
            actual = _real(actual)
        if actual != expected:
            raise ProbeError("probe_mismatch")
    if evidence["dirty_state"] != facts["dirty_state"]:
        raise ProbeError("probe_mismatch")
    for key, expected in facts["fingerprints"].items():
        if evidence["fingerprints"].get(key) != expected:
            raise ProbeError("probe_mismatch")
    if evidence["source_fingerprint"] != facts["fingerprints"]["source"]:
        raise ProbeError("probe_mismatch")


def evaluate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProbeError("invalid_schema")
    owner_evidence_path = payload.get("owner_evidence_path")
    excluded = {owner_evidence_path} if isinstance(owner_evidence_path, str) else set()
    facts = collect(exclude_paths=excluded)
    _compare_claims(payload, facts)
    _compare_owner_evidence(payload, facts)
    checked = {key: value for key, value in payload.items() if key not in AUTHORITATIVE_KEYS}
    if checked.get("action") in {"pre_mutation", "pre-mutation", "pre_action"}:
        checked["action"] = "validate_pre_mutation_evidence"
    checked.update(facts)
    supplied_fingerprints = payload.get("fingerprints")
    if isinstance(supplied_fingerprints, dict):
        # Preserve gate-required requirement/spec/dependency/toolchain entries
        # while retaining the probe's Git-derived values as authoritative.
        checked["fingerprints"] = {
            **supplied_fingerprints,
            **facts["fingerprints"],
        }
    return runtime_gate.evaluate(checked)


def _emit(value: dict[str, Any], status: int) -> NoReturn:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    raise SystemExit(status)


def _read_tty_frame(descriptor: int) -> bytes:
    """Read one newline-delimited PTY frame within a fixed deadline."""
    try:
        original = termios.tcgetattr(descriptor)
        configured = list(original)
        configured[6] = list(original[6])
        configured[3] &= ~(termios.ICANON | termios.ECHO)
        configured[6][termios.VMIN] = 1
        configured[6][termios.VTIME] = 0
        termios.tcsetattr(descriptor, termios.TCSANOW, configured)
    except (OSError, termios.error) as error:
        raise ProbeError("input_terminal_invalid") from error
    raw = bytearray()
    selector = selectors.DefaultSelector()
    try:
        try:
            selector.register(descriptor, selectors.EVENT_READ)
        except (OSError, ValueError) as error:
            raise ProbeError("input_terminal_invalid") from error
        deadline = time.monotonic() + PTY_FRAME_TIMEOUT_SECONDS
        while True:
            remaining = MAX_INPUT_BYTES + 2 - len(raw)
            if remaining <= 0:
                raise ProbeError("input_too_large")
            wait = deadline - time.monotonic()
            if wait <= 0 or not selector.select(wait):
                raise ProbeError("input_frame_incomplete")
            try:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
            except OSError as error:
                raise ProbeError("input_frame_incomplete") from error
            if not chunk:
                raise ProbeError("input_frame_incomplete")
            newline = chunk.find(b"\n")
            if newline >= 0:
                raw.extend(chunk[:newline])
                trailing = chunk[newline + 1:]
                if len(raw) > MAX_INPUT_BYTES:
                    raise ProbeError("input_too_large")
                if trailing:
                    raise ProbeError("trailing_input")
                break
            raw.extend(chunk)
            if len(raw) > MAX_INPUT_BYTES:
                raise ProbeError("input_too_large")
    finally:
        selector.close()
        try:
            termios.tcsetattr(descriptor, termios.TCSANOW, original)
        except (OSError, termios.error) as error:
            raise ProbeError("input_terminal_restore_failed") from error
    return bytes(raw)


def _read_input() -> bytes:
    stream = sys.stdin.buffer
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        descriptor = -1
    raw = (
        _read_tty_frame(descriptor)
        if descriptor >= 0 and os.isatty(descriptor)
        else stream.read(MAX_INPUT_BYTES + 1)
    )
    if len(raw) > MAX_INPUT_BYTES:
        raise ProbeError("input_too_large")
    return raw


def _supervisor_context_available() -> bool:
    if os.environ.get(SUPERVISED_ENV) != "1":
        return False
    try:
        for descriptor in SUPERVISOR_FDS:
            os.fstat(descriptor)
    except OSError:
        return False
    return True


def _is_installed_probe() -> bool:
    candidate = Path(__file__)
    try:
        if candidate.is_symlink() or INSTALLED_PROBE.is_symlink():
            return False
        return candidate.resolve(strict=True) == INSTALLED_PROBE.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return False


def _open_trusted_supervisor(path: Path) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o022
            or metadata.st_size > MAX_SUPERVISOR_BYTES
        ):
            raise OSError("unsafe supervisor")
        digest = hashlib.sha256()
        remaining = MAX_SUPERVISOR_BYTES + 1
        with tempfile.TemporaryFile(prefix="apr-pinned-supervisor-") as snapshot:
            while remaining > 0:
                chunk = os.read(descriptor, min(128 * 1024, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                snapshot.write(chunk)
                remaining -= len(chunk)
            if remaining <= 0:
                raise OSError("oversize supervisor")
            snapshot.flush()
            snapshot.seek(0)
            return os.dup(snapshot.fileno()), digest.hexdigest()
    finally:
        os.close(descriptor)


def _trusted_supervisor_digest(path: Path) -> str:
    descriptor, digest = _open_trusted_supervisor(path)
    os.close(descriptor)
    return digest


def _resolve_supervisor() -> tuple[Path, int]:
    try:
        runtime_root = INSTALLED_SUPERVISOR.parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise OSError("missing runtime root") from error
    current = runtime_root / INSTALLED_SUPERVISOR.name
    try:
        descriptor, digest = _open_trusted_supervisor(current)
        if digest == SUPERVISOR_SHA256:
            return current, descriptor
        os.close(descriptor)
    except (OSError, RuntimeError, ValueError):
        pass
    known_good = runtime_root / "releases" / "known-good"
    try:
        releases = sorted(known_good.iterdir())
    except OSError as error:
        raise OSError("missing known-good supervisor") from error
    for release in releases:
        if (
            len(release.name) != 64
            or any(character not in "0123456789abcdef" for character in release.name)
            or release.is_symlink()
            or not release.is_dir()
        ):
            continue
        candidate = release / "runtime" / INSTALLED_SUPERVISOR.name
        try:
            descriptor, digest = _open_trusted_supervisor(candidate)
            if digest == SUPERVISOR_SHA256:
                return candidate, descriptor
            os.close(descriptor)
        except (OSError, RuntimeError, ValueError):
            continue
    raise OSError("matching supervisor unavailable")


def _delegate_to_supervisor(raw: bytes) -> NoReturn:
    """Use the host-issued current ticket without changing the approval-reviewed command."""
    descriptor: int | None = None
    try:
        supervisor, descriptor = _resolve_supervisor()
        completed = subprocess.run(
            (
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                SUPERVISOR_LOADER,
                str(supervisor),
                str(descriptor),
                str(MAX_SUPERVISOR_BYTES),
                "--discover",
            ),
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=Path.cwd(),
            check=False,
            pass_fds=(descriptor,),
            env={
                key: value
                for key, value in os.environ.items()
                if not key.startswith("PYTHON")
            },
        )
    except (OSError, RuntimeError, ValueError):
        _emit({"decision": "block", "code": "probe_supervisor_unavailable"}, 2)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    sys.stdout.buffer.write(completed.stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(completed.stderr)
    sys.stderr.buffer.flush()
    raise SystemExit(completed.returncode)


def main(argv: list[str] | None = None) -> None:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == [OWNER_RELEASE_ARGUMENT]:
        # The host hook already armed the owner/session/worktree-bound
        # two-phase release before this harmless evidence command can run.
        _emit({"decision": "evidence", "code": "apr_owner_release_requested"}, 0)
    if arguments:
        if len(arguments) != 2 or arguments[0] != MANAGED_WORKTREE_ARGUMENT:
            _emit({"decision": "block", "code": "invalid_arguments"}, 2)
        raw_target = Path(arguments[1]).expanduser()
        try:
            target = raw_target.resolve(strict=True)
            cwd = Path.cwd().resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            _emit({"decision": "block", "code": "invalid_managed_worktree_argument"}, 2)
        if (
            not raw_target.is_absolute()
            or raw_target.is_symlink()
            or raw_target != target
            or not target.is_dir()
        ):
            _emit({"decision": "block", "code": "invalid_managed_worktree_argument"}, 2)
        if cwd != target:
            _emit({"decision": "block", "code": "managed_worktree_argument_cwd_mismatch"}, 2)
    try:
        raw = _read_input()
    except ProbeError as error:
        _emit({"decision": "block", "code": str(error)}, 2)
    if not _supervisor_context_available() and _is_installed_probe():
        _delegate_to_supervisor(raw)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        _emit({"decision": "block", "code": "invalid_json"}, 2)
    try:
        result = evaluate(payload)
    except ProbeError as error:
        _emit({"decision": "block", "code": str(error)}, 2)
    except runtime_gate.GateError as error:
        _emit({"decision": "block", "code": error.code}, 2)
    _emit(result, 0)


if __name__ == "__main__":
    main()
