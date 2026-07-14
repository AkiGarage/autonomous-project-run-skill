#!/usr/bin/env python3
"""Validate repository-relative inline links in tracked Markdown files."""

from __future__ import annotations

import re
import posixpath
import subprocess
from pathlib import Path
from urllib.parse import unquote


LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
EXTERNAL_PREFIXES = ("https://", "http://", "mailto:", "#")


def tracked_markdown() -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "*.md"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in output.splitlines() if line]


def target_path(source: str, raw_target: str) -> str | None:
    target = raw_target.strip().strip("<>")
    if target.startswith(EXTERNAL_PREFIXES):
        return None
    target = unquote(target.split("#", 1)[0])
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), target))


def cached_text(source: str) -> str:
    return subprocess.run(
        ["git", "show", f":{source}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def missing_links(source: str, text: str, existing: set[str], scope: str) -> list[str]:
    failures: list[str] = []
    for raw_target in LINK.findall(text):
        target = target_path(source, raw_target)
        if target is not None and target not in existing:
            failures.append(f"{source} ({scope}): missing link target: {raw_target}")
    return failures


def main() -> int:
    markdown = tracked_markdown()
    index_existing = set(
        subprocess.run(
            ["git", "ls-files"], check=True, capture_output=True, text=True
        ).stdout.splitlines()
    )
    worktree_existing = {path for path in index_existing if Path(path).exists()}
    failures: list[str] = []
    for source in markdown:
        failures.extend(missing_links(source, cached_text(source), index_existing, "index"))
        failures.extend(
            missing_links(
                source,
                Path(source).read_text(encoding="utf-8"),
                worktree_existing,
                "worktree",
            )
        )
    if failures:
        print("\n".join(failures))
        return 1
    print("markdown links passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
