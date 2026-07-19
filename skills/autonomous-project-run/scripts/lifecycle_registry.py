#!/usr/bin/env python3
"""Versioned APR lifecycle reducer and restrictive atomic registry store."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

import host_actions


SCHEMA_VERSION = 1
MAX_REGISTRY_BYTES = 256 * 1024
MAX_EVENTS = 4096
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_WORKTREE = re.compile(r"^[.]codex/worktrees/[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_FORBIDDEN_KEYS = {
    "transcript", "raw_transcript", "prompt", "messages", "conversation", "raw_log",
    "secret", "password", "api_key", "credential", "private_key",
}
_TOP_LEVEL = {
    "schema_version", "run_id", "project", "controller", "planning_stages", "tickets",
    "actions", "events", "pending_actions", "status", "last_sequence",
}
_TICKET_KEYS = {
    "issue_number", "thread_id", "worktree", "branch", "lease_generation",
    "base_sha", "head_sha", "tested_sha", "merged_sha", "evidence_digest",
    "lifecycle", "archive_state", "archive_request_id", "cleanup_state",
    "pending_effects", "unknown_effects",
}
_PLANNING_STAGE_KEYS = {
    "request_id", "thread_id", "input_digest", "output_digest", "publish_state",
    "lifecycle",
}


class RegistryError(ValueError):
    """A deterministic lifecycle registry validation or transition error."""


def _error(code: str) -> None:
    raise RegistryError(code)


def canonical_json(value: Any) -> bytes:
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise RegistryError("invalid_registry_json") from error
    if len(raw) > MAX_REGISTRY_BYTES:
        _error("registry_too_large")
    return raw


def digest_record(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def state_digest(state: dict[str, Any]) -> str:
    validate_state(state)
    return digest_record(state)


def _identifier(value: Any, code: str = "invalid_identifier") -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        _error(code)
    return value


def _digest(value: Any, code: str = "invalid_digest") -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _error(code)
    return value


def _sha(value: Any, code: str = "invalid_sha") -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _error(code)
    return value


def _positive(value: Any, code: str = "invalid_integer") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _error(code)
    return value


def _scan_private(value: Any, depth: int = 0) -> None:
    if depth > 16:
        _error("registry_too_deep")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _error("invalid_registry_key")
            normalized = key.lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                _error("forbidden_registry_field")
            _scan_private(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _scan_private(item, depth + 1)
    elif isinstance(value, str) and "\x00" in value:
        _error("invalid_registry_string")


def _optional_sha(value: Any, code: str) -> None:
    if value is not None:
        _sha(value, code)


def _optional_digest(value: Any, code: str) -> None:
    if value is not None:
        _digest(value, code)


def _identifier_list(value: Any, code: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 1024:
        _error(code)
    checked = [_identifier(item, code) for item in value]
    if len(set(checked)) != len(checked):
        _error(code)
    return checked


def _validate_planning_stages(value: Any) -> None:
    if not isinstance(value, dict) or len(value) > 128:
        _error("invalid_planning_stages")
    for stage_id, stage in value.items():
        _identifier(stage_id, "invalid_planning_stage")
        if not isinstance(stage, dict):
            _error("invalid_planning_stage")
        if set(stage) == {"lifecycle"}:
            if stage["lifecycle"] != "pending":
                _error("invalid_planning_stage")
            continue
        if set(stage) != _PLANNING_STAGE_KEYS:
            _error("invalid_planning_stage")
        _identifier(stage["request_id"], "invalid_planning_stage")
        _identifier(stage["thread_id"], "invalid_planning_stage")
        _digest(stage["input_digest"], "invalid_planning_stage")
        _digest(stage["output_digest"], "invalid_planning_stage")
        if stage["publish_state"] not in {"pending", "submitted", "readback_confirmed"}:
            _error("invalid_planning_stage")
        if stage["lifecycle"] not in {"requested", "running", "reconciled"}:
            _error("invalid_planning_stage")


def _validate_tickets(value: Any) -> None:
    if not isinstance(value, dict) or len(value) > 1024:
        _error("invalid_tickets")
    identities: dict[str, set[Any]] = {
        "issue_number": set(), "thread_id": set(), "worktree": set(), "branch": set(),
    }
    for ticket_id, ticket in value.items():
        _identifier(ticket_id, "invalid_ticket_id")
        if not isinstance(ticket, dict) or set(ticket) != _TICKET_KEYS:
            _error("invalid_ticket_record")
        issue_number = _positive(ticket["issue_number"], "invalid_ticket_record")
        thread_id = _identifier(ticket["thread_id"], "invalid_ticket_record")
        worktree = ticket["worktree"]
        if not isinstance(worktree, str) or not _WORKTREE.fullmatch(worktree):
            _error("invalid_ticket_record")
        branch = ticket["branch"]
        if not isinstance(branch, str) or not _BRANCH.fullmatch(branch) or ".." in branch:
            _error("invalid_ticket_record")
        _positive(ticket["lease_generation"], "invalid_ticket_record")
        _sha(ticket["base_sha"], "invalid_ticket_record")
        _sha(ticket["head_sha"], "invalid_ticket_record")
        _optional_sha(ticket["tested_sha"], "invalid_ticket_record")
        _optional_sha(ticket["merged_sha"], "invalid_ticket_record")
        _optional_digest(ticket["evidence_digest"], "invalid_ticket_record")
        if ticket["lifecycle"] not in {"claimed", "implementing", "issue_closed", "reconciled"}:
            _error("invalid_ticket_record")
        if ticket["archive_state"] not in {
            "ineligible", "eligible", "archive_requested", "archive_pending", "archived"
        }:
            _error("invalid_ticket_record")
        archive_request_id = ticket["archive_request_id"]
        if archive_request_id is not None:
            _identifier(archive_request_id, "invalid_ticket_record")
        if ticket["cleanup_state"] not in {"not_requested", "requested", "complete"}:
            _error("invalid_ticket_record")
        _identifier_list(ticket["pending_effects"], "invalid_ticket_record")
        _identifier_list(ticket["unknown_effects"], "invalid_ticket_record")

        if ticket["archive_state"] == "ineligible" and archive_request_id is not None:
            _error("invalid_ticket_record")
        if ticket["archive_state"] == "eligible" and archive_request_id is not None:
            _error("invalid_ticket_record")
        if ticket["archive_state"] in {"archive_requested", "archive_pending", "archived"}:
            if archive_request_id is None:
                _error("invalid_ticket_record")
        if ticket["archive_state"] != "ineligible":
            if ticket["lifecycle"] != "reconciled" or ticket["evidence_digest"] is None:
                _error("invalid_ticket_record")
        if ticket["archive_state"] in {"eligible", "archive_requested"}:
            if ticket["pending_effects"] or ticket["unknown_effects"]:
                _error("invalid_ticket_record")
        for key, item in {
            "issue_number": issue_number, "thread_id": thread_id,
            "worktree": worktree, "branch": branch,
        }.items():
            if item in identities[key]:
                _error("duplicate_ticket_identity")
            identities[key].add(item)


def initial_state(
    run_id: str,
    repo_id: str,
    spec_digest: str,
    controller_thread_id: str,
    owner_generation: int,
) -> dict[str, Any]:
    state = {
        "schema_version": SCHEMA_VERSION,
        "run_id": _identifier(run_id, "invalid_run_id"),
        "project": {
            "repo_id": _identifier(repo_id, "invalid_repo_id"),
            "spec_digest": _digest(spec_digest, "invalid_spec_digest"),
        },
        "controller": {
            "thread_id": _identifier(controller_thread_id, "invalid_thread_id"),
            "generation": _positive(owner_generation, "invalid_owner_generation"),
            "state": "active",
            "handoff_request_id": None,
        },
        "planning_stages": {},
        "tickets": {},
        "actions": {},
        "events": {},
        "pending_actions": [],
        "status": "executing",
        "last_sequence": 0,
    }
    validate_state(state)
    return state


def validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error("invalid_registry_schema")
    _scan_private(value)
    if set(value) != _TOP_LEVEL or value.get("schema_version") != SCHEMA_VERSION:
        _error("invalid_registry_schema")
    _identifier(value.get("run_id"), "invalid_run_id")
    project = value.get("project")
    if not isinstance(project, dict) or set(project) != {"repo_id", "spec_digest"}:
        _error("invalid_project")
    _identifier(project.get("repo_id"), "invalid_repo_id")
    _digest(project.get("spec_digest"), "invalid_spec_digest")
    controller = value.get("controller")
    if not isinstance(controller, dict) or set(controller) != {
        "thread_id", "generation", "state", "handoff_request_id"
    }:
        _error("invalid_controller")
    _identifier(controller.get("thread_id"), "invalid_thread_id")
    _positive(controller.get("generation"), "invalid_owner_generation")
    if controller.get("state") not in {"active", "active_final", "checkpoint_prepared", "acknowledged"}:
        _error("invalid_controller")
    handoff = controller.get("handoff_request_id")
    if handoff is not None:
        _identifier(handoff, "invalid_controller")
    _validate_planning_stages(value.get("planning_stages"))
    _validate_tickets(value.get("tickets"))
    actions = value.get("actions")
    if not isinstance(actions, dict) or len(actions) > 1024:
        _error("invalid_actions")
    for request_id, action in actions.items():
        _identifier(request_id, "invalid_request_id")
        if not isinstance(action, dict) or set(action) != {"request", "result", "reconciliation"}:
            _error("invalid_action_record")
        try:
            request = host_actions.validate_request(action["request"])
            if request["request_id"] != request_id:
                _error("action_request_mismatch")
            if action["result"] is not None:
                host_actions.validate_result(action["result"])
            expected = host_actions.reconcile(action["request"], action["result"])
        except host_actions.ProtocolError as error:
            raise RegistryError(str(error)) from error
        if action["reconciliation"] != expected:
            _error("action_reconciliation_mismatch")
    for ticket in value["tickets"].values():
        request_id = ticket["archive_request_id"]
        if request_id is None:
            continue
        action = actions.get(request_id)
        if (
            not isinstance(action, dict)
            or action["request"]["action_kind"] != "set_thread_archived"
            or action["request"]["arguments"]["thread_id"] != ticket["thread_id"]
        ):
            _error("invalid_archive_action_reference")
    if not isinstance(value.get("events"), dict) or len(value["events"]) > MAX_EVENTS:
        _error("invalid_events")
    for event_id, event_hash in value["events"].items():
        _identifier(event_id, "invalid_event_id")
        _digest(event_hash, "invalid_event_hash")
    pending = value.get("pending_actions")
    checked_pending = _identifier_list(pending, "invalid_pending_actions")
    pending_set = set(checked_pending)
    for request_id in checked_pending:
        if request_id not in actions:
            _error("dangling_pending_action")
    archive_tickets = {
        ticket["archive_request_id"]: ticket
        for ticket in value["tickets"].values()
        if ticket["archive_request_id"] is not None
    }
    for request_id, action in actions.items():
        request = action["request"]
        if request["action_kind"] == "create_thread":
            should_be_pending = action["reconciliation"] in {"pending", "submitted", "unknown"}
        else:
            ticket = archive_tickets.get(request_id)
            should_be_pending = ticket is None or ticket["archive_state"] != "archived"
        if should_be_pending and request_id not in pending_set:
            _error("missing_pending_action")
        if not should_be_pending and request_id in pending_set:
            _error("stale_pending_action")
    if value.get("status") not in {"executing", "cancelling", "cancelled", "blocked", "complete"}:
        _error("invalid_run_status")
    last_sequence = value.get("last_sequence")
    if isinstance(last_sequence, bool) or not isinstance(last_sequence, int) or last_sequence < 0:
        _error("invalid_last_sequence")
    canonical_json(value)
    return value


def _validated_event(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "event_id", "sequence", "kind", "owner_generation", "payload", "event_hash"
    }:
        _error("invalid_event_schema")
    if value["schema_version"] != SCHEMA_VERSION:
        _error("unsupported_schema_version")
    _identifier(value["event_id"], "invalid_event_id")
    _positive(value["sequence"], "invalid_event_sequence")
    _identifier(value["kind"], "invalid_event_kind")
    _positive(value["owner_generation"], "invalid_owner_generation")
    if not isinstance(value["payload"], dict):
        _error("invalid_event_payload")
    _scan_private(value["payload"])
    expected = digest_record({key: item for key, item in value.items() if key != "event_hash"})
    if value["event_hash"] != expected:
        _error("event_hash_mismatch")
    return value


def _require_payload(payload: dict[str, Any], required: set[str], optional: set[str] = set()) -> None:
    if not required.issubset(payload) or not set(payload).issubset(required | optional):
        _error("invalid_event_payload")


def _ticket(state: dict[str, Any], ticket_id: Any) -> dict[str, Any]:
    identifier = _identifier(ticket_id, "invalid_ticket_id")
    ticket = state["tickets"].get(identifier)
    if not isinstance(ticket, dict):
        _error("unknown_ticket")
    return ticket


def _register_ticket(state: dict[str, Any], payload: dict[str, Any]) -> None:
    required = {
        "ticket_id", "issue_number", "thread_id", "worktree", "branch", "base_sha",
        "head_sha", "lease_generation",
    }
    _require_payload(payload, required)
    ticket_id = _identifier(payload["ticket_id"], "invalid_ticket_id")
    if ticket_id in state["tickets"]:
        _error("ticket_already_registered")
    if isinstance(payload["issue_number"], bool) or not isinstance(payload["issue_number"], int) or payload["issue_number"] < 1:
        _error("invalid_issue_number")
    _identifier(payload["thread_id"], "invalid_thread_id")
    if not isinstance(payload["worktree"], str) or not _WORKTREE.fullmatch(payload["worktree"]):
        _error("invalid_worktree")
    if not isinstance(payload["branch"], str) or not _BRANCH.fullmatch(payload["branch"]) or ".." in payload["branch"]:
        _error("invalid_branch")
    _sha(payload["base_sha"])
    _sha(payload["head_sha"])
    generation = _positive(payload["lease_generation"], "invalid_lease_generation")
    if generation != state["controller"]["generation"]:
        _error("stale_owner_generation")
    state["tickets"][ticket_id] = {
        "issue_number": payload["issue_number"],
        "thread_id": payload["thread_id"],
        "worktree": payload["worktree"],
        "branch": payload["branch"],
        "lease_generation": generation,
        "base_sha": payload["base_sha"],
        "head_sha": payload["head_sha"],
        "tested_sha": None,
        "merged_sha": None,
        "evidence_digest": None,
        "lifecycle": "claimed",
        "archive_state": "ineligible",
        "archive_request_id": None,
        "cleanup_state": "not_requested",
        "pending_effects": [],
        "unknown_effects": [],
    }


def _reconcile_ticket(state: dict[str, Any], payload: dict[str, Any]) -> None:
    _require_payload(payload, {"ticket_id", "tested_sha", "merged_sha", "evidence_digest"})
    ticket = _ticket(state, payload["ticket_id"])
    if ticket["lifecycle"] not in {"claimed", "implementing", "issue_closed"}:
        _error("invalid_ticket_transition")
    ticket["tested_sha"] = _sha(payload["tested_sha"])
    ticket["merged_sha"] = _sha(payload["merged_sha"])
    ticket["evidence_digest"] = _digest(payload["evidence_digest"])
    ticket["lifecycle"] = "reconciled"
    ticket["archive_state"] = "eligible"


def _request_archive(state: dict[str, Any], payload: dict[str, Any]) -> None:
    _require_payload(payload, {"ticket_id", "request_id"})
    ticket = _ticket(state, payload["ticket_id"])
    request_id = _identifier(payload["request_id"], "invalid_request_id")
    if (
        ticket["lifecycle"] != "reconciled"
        or ticket["archive_state"] not in {"eligible", "archive_pending"}
        or ticket["evidence_digest"] is None
        or ticket["pending_effects"]
        or ticket["unknown_effects"]
        or ticket["thread_id"] == state["controller"]["thread_id"]
    ):
        _error("archive_ineligible")
    action = state["actions"].get(request_id)
    if not isinstance(action, dict):
        _error("archive_action_missing")
    request = action["request"]
    if request["action_kind"] != "set_thread_archived":
        _error("archive_action_mismatch")
    if request["arguments"]["thread_id"] != ticket["thread_id"]:
        _error("archive_thread_mismatch")
    if request["expected_state"] != {"archive_state": ticket["archive_state"]}:
        _error("archive_expected_state_mismatch")
    if action["result"] is not None:
        _error("archive_action_already_resolved")
    ticket["archive_state"] = "archive_requested"
    ticket["archive_request_id"] = request_id
    if request_id not in state["pending_actions"]:
        state["pending_actions"].append(request_id)


def _archive_result(state: dict[str, Any], payload: dict[str, Any]) -> None:
    _require_payload(payload, {"ticket_id", "request_id"})
    ticket = _ticket(state, payload["ticket_id"])
    request_id = _identifier(payload["request_id"], "invalid_request_id")
    if ticket["archive_request_id"] != request_id or ticket["archive_state"] not in {
        "archive_requested", "archive_pending"
    }:
        _error("archive_result_mismatch")
    action = state["actions"].get(request_id)
    if not isinstance(action, dict) or action["request"]["action_kind"] != "set_thread_archived":
        _error("archive_action_missing")
    if action["request"]["arguments"]["thread_id"] != ticket["thread_id"]:
        _error("archive_thread_mismatch")
    if action["reconciliation"] == "reconciled":
        ticket["archive_state"] = "archived"
        if request_id in state["pending_actions"]:
            state["pending_actions"].remove(request_id)
    else:
        ticket["archive_state"] = "archive_pending"


def _external_action_requested(state: dict[str, Any], payload: dict[str, Any]) -> None:
    _require_payload(payload, {"request"})
    try:
        request = host_actions.validate_request(payload["request"])
    except host_actions.ProtocolError as error:
        raise RegistryError(str(error)) from error
    if request["owner_generation"] != state["controller"]["generation"]:
        _error("stale_owner_generation")
    request_id = request["request_id"]
    if request_id in state["actions"]:
        _error("action_already_registered")
    state["actions"][request_id] = {
        "request": copy.deepcopy(request),
        "result": None,
        "reconciliation": "pending",
    }
    if request_id not in state["pending_actions"]:
        state["pending_actions"].append(request_id)


def _external_action_result(state: dict[str, Any], payload: dict[str, Any]) -> None:
    _require_payload(payload, {"result"})
    result_value = payload["result"]
    try:
        result = host_actions.validate_result(result_value)
    except host_actions.ProtocolError as error:
        raise RegistryError(str(error)) from error
    action = state["actions"].get(result["request_id"])
    if not isinstance(action, dict):
        _error("unknown_action_request")
    if action["result"] is not None:
        _error("action_result_already_recorded")
    try:
        reconciliation = host_actions.reconcile(action["request"], result)
    except host_actions.ProtocolError as error:
        raise RegistryError(str(error)) from error
    action["result"] = copy.deepcopy(result)
    action["reconciliation"] = reconciliation
    # Archive requests stay pending until the domain archive_result transition
    # records exact-thread readback. Unknown create effects also remain pending.
    if (
        action["request"]["action_kind"] == "create_thread"
        and reconciliation in {"reconciled", "failed"}
        and result["request_id"] in state["pending_actions"]
    ):
        state["pending_actions"].remove(result["request_id"])


def apply_event(state_value: Any, event_value: Any) -> dict[str, Any]:
    validate_state(state_value)
    event = _validated_event(event_value)
    state = copy.deepcopy(state_value)
    existing = state["events"].get(event["event_id"])
    if existing is not None:
        if existing != event["event_hash"]:
            _error("event_conflict")
        return state
    if event["sequence"] <= state["last_sequence"]:
        _error("event_out_of_order")
    if event["owner_generation"] != state["controller"]["generation"]:
        _error("stale_owner_generation")
    kind = event["kind"]
    if kind == "ticket_registered":
        _register_ticket(state, event["payload"])
    elif kind == "ticket_reconciled":
        _reconcile_ticket(state, event["payload"])
    elif kind == "archive_requested":
        _request_archive(state, event["payload"])
    elif kind == "archive_result":
        _archive_result(state, event["payload"])
    elif kind == "external_action_requested":
        _external_action_requested(state, event["payload"])
    elif kind == "external_action_result":
        _external_action_result(state, event["payload"])
    else:
        _error("unknown_event_kind")
    state["events"][event["event_id"]] = event["event_hash"]
    state["last_sequence"] = event["sequence"]
    validate_state(state)
    return state


def reduce_events(initial: Any, events: list[Any]) -> dict[str, Any]:
    validate_state(initial)
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        _error("invalid_events")
    checked = [_validated_event(item) for item in events]
    checked.sort(key=lambda item: (item["sequence"], item["event_id"]))
    state = copy.deepcopy(initial)
    for item in checked:
        state = apply_event(state, item)
    return state


def _outside_project(path: Path, project_root: Path) -> None:
    try:
        common = os.path.commonpath((str(path), str(project_root)))
    except ValueError:
        return
    if common == str(project_root):
        _error("registry_inside_project")


class RegistryStore:
    """Compare-and-swap storage bound to a stable host root and run identity."""

    def __init__(
        self,
        state_root: str | Path,
        project_root: str | Path,
        repo_id: str,
        run_id: str,
    ) -> None:
        raw_root = Path(state_root)
        raw_project = Path(project_root)
        if not raw_root.is_absolute() or not raw_project.is_absolute():
            _error("registry_path_not_absolute")
        if raw_root.is_symlink():
            _error("invalid_registry_parent")
        root = raw_root.resolve(strict=True)
        self.project_root = raw_project.resolve(strict=True)
        _outside_project(root, self.project_root)
        if not root.is_dir():
            _error("invalid_registry_parent")
        mode = stat.S_IMODE(root.stat().st_mode)
        if mode & 0o077:
            _error("registry_parent_permissions")
        self.repo_id = _identifier(repo_id, "invalid_repo_id")
        self.run_id = _identifier(run_id, "invalid_run_id")
        filename = digest_record({
            "schema_version": SCHEMA_VERSION,
            "repo_id": self.repo_id,
            "run_id": self.run_id,
        })
        self.path = root / f"{filename}.json"
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            _error("invalid_registry_file")
        self.lock_path = root / f".{self.path.name}.lock"

    def _validate_identity(self, state: dict[str, Any]) -> dict[str, Any]:
        if state["run_id"] != self.run_id or state["project"]["repo_id"] != self.repo_id:
            _error("registry_identity_mismatch")
        return state

    def _read_unlocked(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except OSError as error:
            raise RegistryError("registry_read_failed") from error
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                _error("registry_file_permissions")
            if info.st_size > MAX_REGISTRY_BYTES:
                _error("registry_too_large")
            raw = os.read(descriptor, MAX_REGISTRY_BYTES + 1)
        finally:
            os.close(descriptor)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise RegistryError("invalid_registry_json") from error
        return self._validate_identity(validate_state(value))

    def read(self) -> dict[str, Any] | None:
        value = self._read_unlocked()
        return copy.deepcopy(value)

    def write(self, state_value: Any, expected_digest: str | None) -> str:
        state = copy.deepcopy(self._validate_identity(validate_state(state_value)))
        if expected_digest is not None:
            _digest(expected_digest, "invalid_expected_digest")
        lock_flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_descriptor = os.open(self.lock_path, lock_flags, 0o600)
        except OSError as error:
            raise RegistryError("registry_lock_failed") from error
        temporary_path: str | None = None
        try:
            os.fchmod(lock_descriptor, 0o600)
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            current = self._read_unlocked()
            current_digest = state_digest(current) if current is not None else None
            if current_digest != expected_digest:
                _error("registry_compare_failed")
            raw = canonical_json(state)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            try:
                os.fchmod(descriptor, 0o600)
                written = 0
                while written < len(raw):
                    written += os.write(descriptor, raw[written:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary_path, self.path)
            temporary_path = None
            directory_descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            return digest_record(state)
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(lock_descriptor)
