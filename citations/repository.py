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
    def __init__(self, context: Context):
        self.context = context
        self.database = Database(context)
        connection = self.database.connection
        connection.execute("BEGIN")
        try:
            self.state = {name: self.database.rows(name) for name in TABLES}
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
        self.dirty = False

    def version(self, identifier: str) -> int:
        return self.versions.get(identifier, 0)

    def touch(self) -> None:
        self.dirty = True

    def find(self, collection: str, identifier: str) -> dict:
        for row in self.state[collection]:
            if row["id"] == identifier:
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
