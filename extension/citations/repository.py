"""Offline SQLite repository; domain writes and outbox operations commit together."""
from __future__ import annotations

import copy
import json
from typing import Protocol

from citations.database import Database, TABLES, encoded
from citations.legacy import check_state as check_legacy
from citations.validation import DomainError

STATE_FILE = "citations-v1.json"
MAX_BYTES = 65536  # Only the preserved legacy text file used this limit.


class Context(Protocol):
    def read_data(self, name: str) -> str | None: ...
    def write_data(self, name: str, value: str) -> None: ...
    def delete_data(self, name: str) -> None: ...
    def data_path(self, relative: str) -> str: ...
    def require_network(self) -> None: ...
    def log(self, event: str, metadata: dict) -> None: ...


def check_state(state: dict) -> None:
    if set(state) != {"schema_version", "settings", *TABLES} or state["schema_version"] != 2:
        raise DomainError("Unsupported SQLite export format.")
    legacy = copy.deepcopy({key: value for key, value in state.items() if key != "attachments"})
    legacy["schema_version"] = 1
    for row in legacy["violation_types"]:
        row.pop("deleted_at", None)
    check_legacy(legacy)


class Repository:
    def __init__(self, context: Context, *, citation: str | None = None):
        self.context = context
        self.database = Database(context)
        connection = self.database.connection
        connection.execute("BEGIN")
        try:
            if citation is None:
                self.state = {name: self.database.rows(name) for name in TABLES}
            else:
                selected = connection.execute(
                    "SELECT * FROM citations WHERE id=? OR citation_number=?", (citation, citation),
                ).fetchone()
                if selected is None:
                    raise DomainError("Citation not found.")
                row = self.database.decode(dict(selected))
                self.state = {
                    "citations": [row],
                    "persons": [self.database.row("persons", row["person_id"])],
                    "violation_types": [self.database.row("violation_types", row["violation_type_id"])],
                    **{table: [
                        self.database.decode(dict(item)) for item in connection.execute(
                            f"SELECT * FROM {table} WHERE citation_id=? ORDER BY rowid", (row["id"],),
                        )
                    ] for table in ("remedies", "citation_events", "attachments")},
                }
            self.state.update(schema_version=2, settings=json.loads(self.database.meta("party_settings")))
            self.generation = self.database.generation()
            self.versions = {row["entity_id"]: row["version"] for row in connection.execute(
                "SELECT entity_id,version FROM row_versions WHERE entity_type='citations'"
            )}
            connection.commit()
        except BaseException:
            connection.rollback()
            self.database.close()
            raise
        self.original = copy.deepcopy(self.state)
        self.indexes = {table: {row["id"]: row for row in self.state[table]} for table in TABLES}
        self.dirty = False

    def version(self, identifier: str) -> int:
        return self.versions.get(identifier, 0)

    def touch(self) -> None:
        self.dirty = True

    def find(self, collection: str, identifier: str) -> dict:
        if identifier in self.indexes[collection]:
            return self.indexes[collection][identifier]
        for row in self.state[collection]:
            if row["id"] == identifier:
                self.indexes[collection][identifier] = row
                return row
        raise DomainError(f"No matching {collection} record.")

    def citation(self, identifier: str) -> dict:
        for row in self.state["citations"]:
            if identifier in (row["id"], row["citation_number"]):
                return row
        raise DomainError("Citation not found.")

    def encoded(self) -> str:
        check_state(self.state)
        return encoded(self.state)

    def commit(self, metadata: dict | None = None) -> None:
        if not self.dirty:
            return
        check_state(self.state)
        with self.database.transaction():
            if self.database.generation() != self.generation:
                raise DomainError("Local data changed during this action. Retrieve the record and retry.")
            for table in TABLES:
                originals = {row["id"]: row for row in self.original[table]}
                for row in self.state[table]:
                    if originals.get(row["id"]) != row:
                        self.database.write(table, row, enqueue=True)
                        if table == "attachments" and row["id"] not in originals:
                            self.database.connection.execute(
                                "INSERT INTO file_transfers(attachment_id,direction) VALUES(?,'upload')", (row["id"],),
                            )
            self.database.set_meta("party_settings", encoded(self.state["settings"]))
            for key, value in (metadata or {}).items():
                self.database.set_meta(key, value)
            self.database.changed()
        self.generation = self.database.generation()
        self.original = copy.deepcopy(self.state)
        self.dirty = False

    def close(self) -> None:
        self.database.close()
