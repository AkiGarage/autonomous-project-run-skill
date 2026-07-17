#!/usr/bin/env python3
"""Report whether Matt Pocock's per-repository skill setup is complete."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import NoReturn


SCHEMA_VERSION = 1
MAX_INSTRUCTION_BYTES = 256 * 1024
MAX_SETUP_DOC_BYTES = 1024 * 1024
SETUP_DOCS = (
    "docs/agents/domain.md",
    "docs/agents/issue-tracker.md",
    "docs/agents/triage-labels.md",
)
AGENT_SKILLS_HEADING = re.compile(r"(?m)^## Agent skills\s*$")


class PreflightError(ValueError):
    """A stable public preflight failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def emit(value: dict[str, object], status: int = 0) -> NoReturn:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    raise SystemExit(status)


def git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PreflightError("git_unavailable") from error
    if result.returncode != 0:
        raise PreflightError("not_git_repository")
    return result.stdout.strip()


def repository_root(repo: Path) -> Path:
    if not repo.is_dir():
        raise PreflightError("invalid_repository_path")
    root = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    if git(root, "rev-parse", "--is-bare-repository") != "false":
        raise PreflightError("bare_repository")
    return root


def read_bounded_regular_file(root: Path, relative: str, maximum: int) -> bytes | None:
    """Read a repo-local file without following symlinks in any path component."""
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    descriptors: list[int] = []
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        directory = os.open(root, flags | os.O_DIRECTORY)
        descriptors.append(directory)
        for part in parts[:-1]:
            directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            descriptors.append(directory)
        descriptor = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=directory)
        descriptors.append(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > maximum:
            return None
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        return content if 0 < len(content) <= maximum else None
    except OSError:
        return None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def selected_instruction_file(root: Path) -> tuple[str | None, Path | None]:
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = root / name
        try:
            path.lstat()
        except OSError:
            continue
        else:
            return name, path
    return None, None


def instruction_setup_complete(root: Path, name: str) -> bool:
    raw = read_bounded_regular_file(root, name, MAX_INSTRUCTION_BYTES)
    if raw is None:
        return False
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return AGENT_SKILLS_HEADING.search(content) is not None and all(
        marker in content for marker in SETUP_DOCS
    )


def inspect(repo: Path) -> dict[str, object]:
    root = repository_root(repo.resolve())
    missing = [
        name for name in SETUP_DOCS
        if read_bounded_regular_file(root, name, MAX_SETUP_DOC_BYTES) is None
    ]
    instruction_name, instruction_path = selected_instruction_file(root)
    if instruction_path is None:
        missing.append("AGENTS.md|CLAUDE.md:Agent skills")
    elif not instruction_setup_complete(root, instruction_name):
        missing.append(f"{instruction_name}:Agent skills")
    missing.sort()
    configured = not missing
    return {
        "schema_version": SCHEMA_VERSION,
        "decision": "evidence",
        "code": "setup_complete" if configured else "setup_required",
        "configured": configured,
        "instruction_file": instruction_name,
        "missing": missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        result = inspect(args.repo)
    except PreflightError as error:
        emit({"schema_version": SCHEMA_VERSION, "decision": "block", "code": error.code}, 2)
    emit(result)


if __name__ == "__main__":
    main()
