"""Read-only legacy JSON format validation; new persistence uses SQLite."""
from __future__ import annotations

from uuid import UUID

from citations.validation import DomainError, party_zone, timestamp

COLLECTIONS = ("persons", "violation_types", "citations", "remedies", "citation_events")


def check_state(state: dict) -> None:
    """Reject incompatible/corrupt snapshots instead of silently resetting data."""
    if not isinstance(state, dict) or set(state) != {"schema_version", "settings", *COLLECTIONS}:
        raise DomainError("Invalid citation snapshot structure; restore a backup.")
    if type(state["schema_version"]) is not int or state["schema_version"] != 1:
        raise DomainError("Unsupported citation data version; do not overwrite it.")
    settings = state["settings"]
    if not isinstance(settings, dict) or set(settings) != {"party_timezone", "due_soon_seconds"}:
        raise DomainError("Invalid citation settings.")
    if not isinstance(settings["party_timezone"], str):
        raise DomainError("Invalid party timezone.")
    party_zone(settings["party_timezone"])
    if type(settings["due_soon_seconds"]) is not int or not 1 <= settings["due_soon_seconds"] <= 604800:
        raise DomainError("Invalid due-soon setting.")
    expected = {
        "persons": {"id", "display_name", "email", "phone", "created_at", "updated_at", "deleted_at"},
        "violation_types": {
            "id", "code", "title", "description", "default_remedy", "default_resolution_seconds",
            "severity", "active", "created_at", "updated_at",
        },
        "citations": {
            "id", "citation_number", "person_id", "violation_type_id", "issued_at", "issued_by",
            "description", "first_offense", "status", "resolution_duration_seconds", "due_at",
            "resolved_at", "resolved_by", "resolution_notes", "created_at", "updated_at", "revision", "deleted_at",
        },
        "remedies": {
            "id", "citation_id", "description", "required", "completed", "completed_at",
            "completed_by", "completion_notes", "position", "created_at", "updated_at", "deleted_at",
        },
        "citation_events": {"id", "citation_id", "actor_id", "event_type", "event_payload", "created_at"},
    }
    identifiers: dict[str, set[str]] = {}
    for name in COLLECTIONS:
        rows = state[name]
        if not isinstance(rows, list):
            raise DomainError(f"Invalid {name} collection.")
        identifiers[name] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != expected[name]:
                raise DomainError(f"Invalid {name} row.")
            try:
                if not isinstance(row["id"], str) or str(UUID(row["id"])) != row["id"]:
                    raise ValueError
            except ValueError as exc:
                raise DomainError(f"Invalid UUID in {name}.") from exc
            if row["id"] in identifiers[name]:
                raise DomainError(f"Duplicate UUID in {name}.")
            identifiers[name].add(row["id"])
            for key, value in row.items():
                if key.endswith("_at") and value is not None:
                    if not isinstance(value, str):
                        raise DomainError(f"Invalid {key} timestamp.")
                    timestamp(value)
            for key in ("first_offense", "active", "required", "completed"):
                if key in row and type(row[key]) is not bool:
                    raise DomainError(f"Invalid {key} flag.")
            for key in ("revision", "position", "severity", "resolution_duration_seconds", "default_resolution_seconds"):
                if key in row and row[key] is not None and (
                    type(row[key]) is not int or row[key] < (1 if key in {
                        "severity", "resolution_duration_seconds", "default_resolution_seconds"
                    } else 0)
                ):
                    raise DomainError(f"Invalid {key}.")
            for key in ("display_name", "title", "code", "citation_number", "event_type"):
                if key in row and (not isinstance(row[key], str) or not row[key].strip()):
                    raise DomainError(f"Invalid {key}.")
            for key in ("description", "email", "phone", "default_remedy", "resolution_notes", "completion_notes"):
                if key in row and row[key] is not None and not isinstance(row[key], str):
                    raise DomainError(f"Invalid {key}.")
    for name, field in (("violation_types", "code"), ("citations", "citation_number")):
        if len({row[field] for row in state[name]}) != len(state[name]):
            raise DomainError(f"Duplicate {field}.")
    for citation in state["citations"]:
        if citation["person_id"] not in identifiers["persons"] or citation["violation_type_id"] not in identifiers["violation_types"]:
            raise DomainError("Citation references a missing person or violation.")
        if citation["status"] not in {"open", "resolved", "void"}:
            raise DomainError("Invalid citation status.")
        if (citation["status"] == "resolved") != (citation["resolved_at"] is not None):
            raise DomainError("Inconsistent resolution state.")
        if citation["status"] == "open" and citation["deleted_at"] is not None:
            raise DomainError("Open citation cannot be deleted.")
    for name in ("remedies", "citation_events"):
        for row in state[name]:
            if row["citation_id"] not in identifiers["citations"]:
                raise DomainError("Missing citation reference.")
            if name == "remedies" and row["completed"] != (row["completed_at"] is not None):
                raise DomainError("Inconsistent remedy completion state.")
            if name == "citation_events" and not isinstance(row["event_payload"], dict):
                raise DomainError("Invalid event payload.")

