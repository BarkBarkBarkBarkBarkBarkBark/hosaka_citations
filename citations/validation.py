"""Small stdlib validator for this package's deliberately simple JSON schemas."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class DomainError(ValueError):
    pass


ACTIONS = (
    "dashboard", "create-person", "search-persons", "create-violation",
    "list-violations", "issue", "issue-for-attendee", "search", "get", "edit", "add-remedy",
    "edit-remedy", "complete-remedy", "resolve", "reopen", "void", "overdue",
    "history", "party-settings", "export", "find-citations", "manage-citation",
    "sync-now", "sync-status", "sync-conflicts", "sync-resolve", "background-tick", "add-evidence", "initialize",
)


def validate(value: Any, schema: dict, path: str = "params") -> None:
    kind = schema["type"]
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
    }[kind]
    if not valid:
        raise DomainError(f"{path} must be {kind}.")
    if "enum" in schema and value not in schema["enum"]:
        raise DomainError(f"{path} must be one of {', '.join(map(str, schema['enum']))}.")
    if kind == "object":
        properties = schema["properties"]
        if set(value) - set(properties):
            raise DomainError(f"{path} contains unsupported fields.")
        if set(schema.get("required", [])) - set(value):
            raise DomainError(f"{path} is missing required fields.")
        for key, item in value.items():
            validate(item, properties[key], f"{path}.{key}")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema["maxItems"]:
            raise DomainError(f"{path} has an invalid item count.")
        for item in value:
            validate(item, schema["items"], f"{path}[]")
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema["maxLength"]:
            raise DomainError(f"{path} has an invalid length.")
        if schema.get("minLength", 0) and not value.strip():
            raise DomainError(f"{path} cannot be blank.")
        if any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise DomainError(f"{path} contains control characters.")
    elif kind == "integer":
        if not schema["minimum"] <= value <= schema["maximum"]:
            raise DomainError(f"{path} is outside its allowed range.")


def validate_action(action: str, params: dict) -> None:
    if action not in ACTIONS:
        raise DomainError("Unknown citation action.")
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / f"{action}.input.json").read_text(encoding="utf-8"))
    validate(params, schema)
    if len(json.dumps(params, ensure_ascii=False, allow_nan=False).encode()) > 16384:
        raise DomainError("Action parameters exceed 16 KiB.")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DomainError("Use an ISO 8601 timestamp with an explicit UTC offset.") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise DomainError("Timestamps must include a UTC offset; local ambiguous times are not accepted.")
    try:
        return result.astimezone(timezone.utc)
    except OverflowError as exc:
        raise DomainError("Timestamp is outside the supported UTC calendar range.") from exc


def display_time(value: str, zone: ZoneInfo) -> str:
    try:
        return timestamp(value).astimezone(zone).isoformat()
    except OverflowError as exc:
        raise DomainError("Timestamp cannot be represented in the party timezone.") from exc


def party_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise DomainError("Unknown IANA party timezone. Use UTC if zone data is unavailable.") from exc
