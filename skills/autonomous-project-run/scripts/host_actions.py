#!/usr/bin/env python3
"""Validate APR host task actions without executing or authorizing them."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 64 * 1024
MAX_IDENTIFIER = 160

_TOOLS = {
    "codex_app__create_thread": "create_thread",
    "codex_app__read_thread": "read_thread",
    "codex_app__list_threads": "list_threads",
    "codex_app__set_thread_archived": "set_thread_archived",
    # Codex PreToolUse currently collapses the namespace separators before its
    # canonicalizer sees the identity.  These exact observed forms are fixtures,
    # not a general alias rule.
    "codex_appcreate_thread": "create_thread",
    "codex_appread_thread": "read_thread",
    "codex_applist_threads": "list_threads",
    "codex_appset_thread_archived": "set_thread_archived",
}
_MUTATIONS = {"create_thread", "set_thread_archived"}
_TASK_KINDS = {"planning", "implementation", "controller_successor", "final_verifier"}
_RESULT_STATUSES = {"succeeded", "failed", "unknown"}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ProtocolError(ValueError):
    """A deterministic, fail-closed host action validation error."""


def _error(code: str) -> None:
    raise ProtocolError(code)


def canonical_json(value: Any) -> bytes:
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise ProtocolError("invalid_json_value") from error
    if len(raw) > MAX_RECORD_BYTES:
        _error("record_too_large")
    return raw


def _hash_without(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: item for key, item in value.items() if key != field})).hexdigest()


def _mapping(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(code)
    return value


def _exact_keys(value: dict[str, Any], required: set[str], optional: set[str], code: str) -> None:
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        _error(code)


def _identifier(value: Any, code: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        _error(code)
    return value


def _digest(value: Any, code: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _error(code)
    return value


def _positive_integer(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _error(code)
    return value


def canonicalize_tool(identity: Any) -> str:
    if not isinstance(identity, str) or identity not in _TOOLS:
        _error("unknown_tool_identity")
    return _TOOLS[identity]


def _validate_expected_state(value: Any) -> dict[str, str]:
    state = _mapping(value, "invalid_expected_state")
    if not state or len(state) > 16:
        _error("invalid_expected_state")
    for key, item in state.items():
        _identifier(key, "invalid_expected_state")
        _identifier(item, "invalid_expected_state")
    return state


def _validate_target(value: Any) -> dict[str, Any]:
    target = _mapping(value, "invalid_create_arguments")
    _exact_keys(target, {"type", "project_id", "environment"}, set(), "invalid_create_arguments")
    if target["type"] != "project":
        _error("invalid_create_arguments")
    _identifier(target["project_id"], "invalid_create_arguments")
    environment = _mapping(target["environment"], "invalid_create_arguments")
    _exact_keys(environment, {"type"}, {"starting_state"}, "invalid_create_arguments")
    if environment["type"] not in {"local", "worktree"}:
        _error("invalid_create_arguments")
    if environment["type"] == "local" and "starting_state" in environment:
        _error("invalid_create_arguments")
    if "starting_state" in environment:
        starting = _mapping(environment["starting_state"], "invalid_create_arguments")
        _exact_keys(starting, {"type"}, {"branch_name"}, "invalid_create_arguments")
        if starting["type"] == "working-tree":
            if "branch_name" in starting:
                _error("invalid_create_arguments")
        elif starting["type"] == "branch":
            branch = starting.get("branch_name")
            if not isinstance(branch, str) or not _BRANCH.fullmatch(branch) or ".." in branch:
                _error("invalid_create_arguments")
        else:
            _error("invalid_create_arguments")
    return target


def _validate_arguments(kind: str, value: Any) -> dict[str, Any]:
    arguments = _mapping(value, "invalid_action_arguments")
    if kind == "create_thread":
        _exact_keys(
            arguments,
            {"task_kind", "prompt_digest", "target"},
            set(),
            "invalid_create_arguments",
        )
        if arguments["task_kind"] not in _TASK_KINDS:
            _error("invalid_create_arguments")
        _digest(arguments["prompt_digest"], "invalid_create_arguments")
        _validate_target(arguments["target"])
        return arguments
    if kind == "set_thread_archived":
        _exact_keys(
            arguments,
            {"thread_id", "archived"},
            {"host_id"},
            "invalid_archive_arguments",
        )
        _identifier(arguments["thread_id"], "invalid_archive_arguments")
        if "host_id" in arguments:
            _identifier(arguments["host_id"], "invalid_archive_arguments")
        if arguments["archived"] is not True:
            _error("invalid_archive_arguments")
        return arguments
    _error("invalid_action_kind")


def validate_request(value: Any) -> dict[str, Any]:
    request = _mapping(value, "invalid_request")
    _exact_keys(
        request,
        {
            "schema_version", "request_id", "logical_key", "action_kind", "tool_identity",
            "owner_generation", "expected_state", "arguments", "payload_hash",
        },
        set(),
        "invalid_request_schema",
    )
    if request["schema_version"] != SCHEMA_VERSION:
        _error("unsupported_schema_version")
    _identifier(request["request_id"], "invalid_request_id")
    _identifier(request["logical_key"], "invalid_logical_key")
    kind = request["action_kind"]
    if kind not in _MUTATIONS or canonicalize_tool(request["tool_identity"]) != kind:
        _error("action_tool_mismatch")
    _positive_integer(request["owner_generation"], "invalid_owner_generation")
    _validate_expected_state(request["expected_state"])
    _validate_arguments(kind, request["arguments"])
    if not isinstance(request["payload_hash"], str) or request["payload_hash"] != _hash_without(request, "payload_hash"):
        _error("request_hash_mismatch")
    return request


def _validate_observed(kind: str, status: str, value: Any) -> dict[str, Any]:
    observed = _mapping(value, "invalid_observed_result")
    allowed = {
        "thread_id", "client_thread_id", "host_id", "archived", "readback", "code"
    }
    if not set(observed).issubset(allowed):
        _error("invalid_observed_result")
    for key in ("thread_id", "client_thread_id", "host_id", "code"):
        if key in observed:
            _identifier(observed[key], "invalid_observed_result")
    for key in ("archived", "readback"):
        if key in observed and not isinstance(observed[key], bool):
            _error("invalid_observed_result")
    if status == "succeeded" and kind == "create_thread":
        if not ({"thread_id", "client_thread_id"} & set(observed)):
            _error("invalid_observed_result")
    if status == "succeeded" and kind == "set_thread_archived":
        if observed.get("archived") is not True or "thread_id" not in observed:
            _error("invalid_observed_result")
    return observed


def validate_result(value: Any) -> dict[str, Any]:
    result = _mapping(value, "invalid_result")
    _exact_keys(
        result,
        {
            "schema_version", "request_id", "logical_key", "action_kind", "owner_generation",
            "status", "observed", "result_hash",
        },
        set(),
        "invalid_result_schema",
    )
    if result["schema_version"] != SCHEMA_VERSION:
        _error("unsupported_schema_version")
    _identifier(result["request_id"], "invalid_request_id")
    _identifier(result["logical_key"], "invalid_logical_key")
    if result["action_kind"] not in _MUTATIONS:
        _error("invalid_action_kind")
    _positive_integer(result["owner_generation"], "invalid_owner_generation")
    if result["status"] not in _RESULT_STATUSES:
        _error("invalid_result_status")
    _validate_observed(result["action_kind"], result["status"], result["observed"])
    if not isinstance(result["result_hash"], str) or result["result_hash"] != _hash_without(result, "result_hash"):
        _error("result_hash_mismatch")
    return result


def reconcile(request_value: Any, result_value: Any | None) -> str:
    request = validate_request(request_value)
    if result_value is None:
        return "pending"
    result = validate_result(result_value)
    for key in ("request_id", "logical_key", "action_kind", "owner_generation"):
        if result[key] != request[key]:
            _error("result_request_mismatch")
    if request["action_kind"] == "set_thread_archived":
        expected_thread = request["arguments"]["thread_id"]
        if result["status"] != "succeeded":
            return "archive_pending"
        if result["observed"].get("thread_id") != expected_thread:
            _error("archive_thread_mismatch")
        return "reconciled" if result["observed"].get("readback") is True else "archive_pending"
    if result["status"] == "failed":
        return "failed"
    if result["status"] == "unknown":
        return "unknown"
    if result["observed"].get("thread_id") and result["observed"].get("readback") is True:
        return "reconciled"
    return "submitted"
