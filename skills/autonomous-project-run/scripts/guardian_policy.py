#!/usr/bin/env python3
"""Evaluate one bounded, stateless guardian snapshot without side effects."""

import json
import re
import sys
from typing import NoReturn


MAX_INPUT_BYTES = 4096
TOP_LEVEL_KEYS = {
    "schema_version",
    "project_key",
    "guardian_id",
    "singleton_owner",
    "poll_id",
    "last_poll_id",
    "lifecycle",
    "state_digest",
    "previous_digest",
    "metrics",
}
METRIC_OWNERS = {"guardian", "implementation"}
METRIC_KEYS = {"tokens", "compactions"}
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


class PolicyError(ValueError):
    """A stable, public validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def require_identifier(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise PolicyError("invalid_identifier")
    return value


def require_digest(value: object) -> str:
    if not isinstance(value, str) or DIGEST_PATTERN.fullmatch(value) is None:
        raise PolicyError("invalid_digest")
    return value


def require_metrics(value: object) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict) or set(value) != METRIC_OWNERS:
        raise PolicyError("invalid_metrics")
    checked: dict[str, dict[str, int]] = {}
    for owner in sorted(METRIC_OWNERS):
        counters = value[owner]
        if not isinstance(counters, dict) or set(counters) != METRIC_KEYS:
            raise PolicyError("invalid_metrics")
        if any(type(counters[key]) is not int or counters[key] < 0 for key in METRIC_KEYS):
            raise PolicyError("invalid_metrics")
        checked[owner] = {key: counters[key] for key in sorted(METRIC_KEYS)}
    return checked


def validate_state(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != TOP_LEVEL_KEYS:
        raise PolicyError("invalid_schema")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise PolicyError("unsupported_schema")
    state = {key: require_identifier(value[key]) for key in (
        "project_key", "guardian_id", "singleton_owner", "poll_id", "last_poll_id"
    )}
    lifecycle = value["lifecycle"]
    if lifecycle not in {"active", "complete", "blocked"}:
        raise PolicyError("invalid_lifecycle")
    state["lifecycle"] = lifecycle
    state["state_digest"] = require_digest(value["state_digest"])
    state["previous_digest"] = require_digest(value["previous_digest"])
    state["metrics"] = require_metrics(value["metrics"])
    return state


def evaluate(state: dict[str, object]) -> dict[str, object] | None:
    if state["guardian_id"] != state["singleton_owner"]:
        return None
    if state["poll_id"] == state["last_poll_id"]:
        return None
    if state["lifecycle"] in {"complete", "blocked"}:
        return None
    if state["state_digest"] == state["previous_digest"]:
        return None
    return {
        "kind": "delta",
        "project_key": state["project_key"],
        "state_digest": state["state_digest"],
        "metrics": state["metrics"],
    }


def emit_blocker(code: str) -> NoReturn:
    print(json.dumps({"kind": "blocker", "code": code}, separators=(",", ":")))
    raise SystemExit(2)


def main() -> None:
    payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(payload) > MAX_INPUT_BYTES:
        emit_blocker("input_too_large")
    try:
        decoded = json.loads(payload)
        result = evaluate(validate_state(decoded))
    except (json.JSONDecodeError, UnicodeDecodeError):
        emit_blocker("invalid_json")
    except PolicyError as error:
        emit_blocker(error.code)
    if result is not None:
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
