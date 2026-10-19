"""SQLite schema, transactions and record replication primitives."""
from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from uuid import UUID

from citations.config import load_config
from citations.validation import DomainError, stamp, timestamp, utcnow

TABLES = ("persons", "violation_types", "citations", "remedies", "citation_events", "attachments")
BOOLS = {"active", "first_offense", "required", "completed"}


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Database:
    def __init__(self, context):
        self.context = context
        self.config = load_config(context, validate_remote=False)
        path = Path(context.data_path(self.config["database"]))
        location = context.read_data("database-location.json")
        if location is not None:
            try:
                saved_location = json.loads(location)
            except ValueError as exc:
                raise DomainError("Invalid database-location.json; repair or restore the locator.") from exc
            if saved_location != self.config["database"]:
                raise DomainError("Database path changed. Move a verified backup explicitly; do not silently start a new database.")
        with open(context.data_path("migration.lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            old_mask = os.umask(0o077)
            try:
                self.connection = sqlite3.connect(path, timeout=5, isolation_level=None)
            finally:
                os.umask(old_mask)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)")
            migrations = sorted((Path(__file__).parent / "migrations/sqlite").glob("*.sql"))
            available = {int(migration.name.split("_")[0]) for migration in migrations}
            applied = {row[0] for row in self.connection.execute("SELECT version FROM schema_migrations")}
            if applied - available:
                self.connection.close()
                raise DomainError("Database requires a newer extension version; downgrade refused.")
            for migration in migrations:
                version = int(migration.name.split("_")[0])
                if self.connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
                    continue
                self.connection.executescript(
                    "BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8")
                    + f"\nINSERT INTO schema_migrations VALUES({version});\nCOMMIT;"
                )
            from citations.seeds import seed_defaults
            with self.transaction():
                if self.meta("initialized") is None:
                    self.set_meta("party_settings", encoded({"party_timezone": "UTC", "due_soon_seconds": 900}))
                    seed_defaults(self)
                    self.set_meta("initialized", "1")
                    self.set_meta("generation", "0")
            context.write_data("database-location.json", json.dumps(self.config["database"]))

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def meta(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row["value"] if row is not None else None

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute("INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def generation(self) -> int:
        return int(self.meta("generation") or "0")

    def changed(self) -> None:
        self.set_meta("generation", str(self.generation() + 1))

    def columns(self, table: str) -> list[str]:
        if table not in TABLES:
            raise DomainError("Unsupported synchronized entity.")
        return [row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")]

    def row(self, table: str, identifier: str) -> dict | None:
        self.columns(table)
        record = self.connection.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        return self.decode(dict(record)) if record is not None else None

    @staticmethod
    def decode(row: dict) -> dict:
        for key in row:
            if key in BOOLS:
                row[key] = bool(row[key])
            if key == "event_payload":
                row[key] = json.loads(row[key])
        return row

    def rows(self, table: str) -> list[dict]:
        self.columns(table)
        return [self.decode(dict(row)) for row in self.connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]

    def write(self, table: str, payload: dict, *, enqueue: bool) -> bool:
        columns = self.columns(table)
        if set(payload) - set(columns):
            raise DomainError("Record contains unknown database fields.")
        record = {column: payload.get(column) for column in columns}
        try:
            if str(UUID(record["id"])) != record["id"]:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise DomainError("Database record IDs must be canonical UUIDs.") from exc
        if table == "attachments" and (
            not isinstance(record["storage_key"], str)
            or not re.fullmatch(r"[a-f0-9]{32}\.[a-f0-9]{64}\.(png|jpg|webp|pdf)", record["storage_key"])
            or record["mime_type"] not in {"image/png", "image/jpeg", "image/webp", "application/pdf"}
        ):
            raise DomainError("Invalid evidence metadata.")
        old = self.row(table, record["id"])
        if old == record:
            return False
        if table == "citation_events" and old is not None:
            raise DomainError("Citation history is append-only.")
        values = [encoded(record[column]) if column == "event_payload" else record[column] for column in columns]
        updates = ",".join(f"{column}=excluded.{column}" for column in columns if column != "id")
        self.connection.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}", values,
        )
        self.connection.execute(
            "INSERT INTO row_versions VALUES(?,?,0) ON CONFLICT(entity_type,entity_id) DO UPDATE SET version=version+1",
            (table, record["id"]),
        )
        if enqueue:
            revision_row = self.connection.execute("SELECT revision FROM replica_state WHERE entity_type=? AND entity_id=?", (table, record["id"])).fetchone()
            revision = revision_row["revision"] if revision_row else 0
            count = self.connection.execute(
                "SELECT count(*) FROM sync_outbox WHERE entity_type=? AND entity_id=? AND status='pending'", (table, record["id"]),
            ).fetchone()[0]
            self.connection.execute(
                "INSERT INTO sync_outbox(operation_id,entity_type,entity_id,payload,base_revision,created_at) VALUES(?,?,?,?,?,?)",
                (str(uuid4()), table, record["id"], encoded(record), revision + count, stamp(utcnow())),
            )
        return True

    def remote_revision(self, table: str, identifier: str, revision: int) -> None:
        self.connection.execute(
            "INSERT INTO replica_state VALUES(?,?,?) ON CONFLICT(entity_type,entity_id) DO UPDATE SET revision=excluded.revision",
            (table, identifier, revision),
        )

    def normalize_remote(self, table: str, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) != set(self.columns(table)):
            raise DomainError("Remote record does not match the installed schema.")
        result = dict(payload)
        if table == "attachments" and not re.fullmatch(r"[a-f0-9]{32}\.[a-f0-9]{64}\.(png|jpg|webp|pdf)", result["storage_key"]):
            raise DomainError("Invalid remote evidence storage key.")
        for key, value in result.items():
            if key.endswith("_at") and value is not None:
                if not isinstance(value, str):
                    raise DomainError("Invalid remote timestamp.")
                result[key] = stamp(timestamp(value))
        return result

    def close(self):
        self.connection.close()
