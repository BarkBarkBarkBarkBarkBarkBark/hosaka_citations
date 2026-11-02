"""Durable row-level replication, optimistic revisions and explicit conflicts."""
from __future__ import annotations

import fcntl
import hashlib
import json
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from citations.database import Database, encoded
from citations.supabase import Supabase, SyncError
from citations.validation import DomainError, stamp, timestamp, utcnow


class Synchronizer:
    def __init__(self, database: Database, client=None):
        self.db = database
        self.sql = database.connection
        self.context = database.context
        self.config = database.config
        self.client = client

    def status(self) -> dict:
        return {
            "enabled": self.config["sync_enabled"],
            "pending_changes": self.sql.execute("SELECT count(*) FROM sync_outbox WHERE status IN ('pending','conflict')").fetchone()[0],
            "unresolved_conflicts": self.sql.execute("SELECT count(*) FROM sync_conflicts WHERE resolved_at IS NULL").fetchone()[0],
            "pending_files": self.sql.execute("SELECT count(*) FROM file_transfers WHERE status='pending'").fetchone()[0],
            "last_successful_sync": self.db.meta("last_successful_sync"),
            "last_error": self.db.meta("sync_error") or None,
            "sync_interval_seconds": self.config["sync_interval_seconds"],
            "remote_cursor": self.sql.execute("SELECT coalesce(max(cursor),0) FROM sync_checkpoints").fetchone()[0],
            "pull_incomplete": self.db.meta("pull_incomplete") == "1",
        }

    def conflict(self, table: str, identifier: str, payload: dict | None, revision: int):
        local = self.db.row(table, identifier)
        if local is None:
            raise SyncError("A conflict references a missing local record.")
        self.sql.execute(
            "INSERT INTO sync_conflicts(id,entity_type,entity_id,local_payload,remote_payload,remote_revision,created_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(entity_type,entity_id,remote_revision) DO NOTHING",
            (str(uuid4()), table, identifier, encoded(local), encoded(payload) if payload is not None else None, revision, stamp(utcnow())),
        )
        self.sql.execute("UPDATE sync_outbox SET status='conflict' WHERE entity_type=? AND entity_id=? AND status='pending'", (table, identifier))

    def conflicts(self) -> list[dict]:
        rows = []
        for row in self.sql.execute("SELECT * FROM sync_conflicts WHERE resolved_at IS NULL ORDER BY created_at"):
            item = dict(row)
            item["local_payload"] = json.loads(item["local_payload"])
            item["remote_payload"] = json.loads(item["remote_payload"]) if item["remote_payload"] else None
            rows.append(item)
        return rows

    def resolve(self, identifier: str, choice: str):
        if choice not in {"local", "remote"}:
            raise DomainError("Conflict choice must be local or remote.")
        with self.db.transaction():
            conflict = self.sql.execute("SELECT * FROM sync_conflicts WHERE id=? AND resolved_at IS NULL", (identifier,)).fetchone()
            if conflict is None:
                raise DomainError("Unresolved conflict not found.")
            table, entity = conflict["entity_type"], conflict["entity_id"]
            latest = self.sql.execute(
                "SELECT max(remote_revision) FROM sync_conflicts WHERE entity_type=? AND entity_id=? AND resolved_at IS NULL", (table, entity),
            ).fetchone()[0]
            if latest != conflict["remote_revision"]:
                raise DomainError("A newer remote version is available; review that conflict first.")
            local = self.db.row(table, entity)
            remote = json.loads(conflict["remote_payload"]) if conflict["remote_payload"] else None
            if remote is None:
                raise DomainError("Remote record is absent; retain the conflict for explicit recovery.")
            if choice == "local" and remote.get("deleted_at") and not local.get("deleted_at"):
                raise DomainError("A remote deletion cannot be silently resurrected. Choose remote; the local version stays archived.")
            self.sql.execute(
                "UPDATE sync_outbox SET status='superseded' WHERE entity_type=? AND entity_id=? AND status IN ('pending','conflict')", (table, entity),
            )
            self.db.remote_revision(table, entity, latest)
            if choice == "remote":
                self.db.write(table, remote, enqueue=False)
            else:
                # Enqueue the latest local snapshot even when the domain row did not change.
                self.sql.execute(
                    "INSERT INTO sync_outbox(operation_id,entity_type,entity_id,payload,base_revision,created_at) VALUES(?,?,?,?,?,?)",
                    (str(uuid4()), table, entity, encoded(local), latest, stamp(utcnow())),
                )
            self.sql.execute(
                "UPDATE sync_conflicts SET resolved_at=?,resolution=?,resolution_payload=? "
                "WHERE entity_type=? AND entity_id=? AND resolved_at IS NULL",
                (stamp(utcnow()), choice, encoded(local), table, entity),
            )
            self.db.changed()

    def retry(self, table: str, key: str, attempts: int, error: str):
        if table == "sync_outbox":
            self.sql.execute("UPDATE sync_outbox SET attempts=attempts+1,next_attempt_at=?,last_error=? WHERE operation_id=?",
                             (stamp(utcnow() + timedelta(seconds=min(3600, 2 ** min(attempts + 1, 12)))), error, key))
        else:
            self.sql.execute("UPDATE file_transfers SET attempts=attempts+1,next_attempt_at=?,last_error=? WHERE attachment_id=?",
                             (stamp(utcnow() + timedelta(seconds=min(3600, 2 ** min(attempts + 1, 12)))), error, key))

    def transfer_files(self, deadline: float, force: bool):
        for row in self.sql.execute("SELECT * FROM file_transfers WHERE status='pending' ORDER BY rowid").fetchall():
            if time.monotonic() >= deadline:
                return
            if not force and row["next_attempt_at"] and timestamp(row["next_attempt_at"]) > utcnow():
                continue
            attachment = self.db.row("attachments", row["attachment_id"])
            if attachment["deleted_at"]:
                self.sql.execute("UPDATE file_transfers SET status='done',last_error=NULL WHERE attachment_id=?", (attachment["id"],))
                continue
            path = Path(self.context.data_path("files/" + attachment["storage_key"]))
            try:
                if row["direction"] == "upload":
                    with path.open("rb") as stream:
                        content = stream.read(10485761)
                    if hashlib.sha256(content).hexdigest() != attachment["sha256"]:
                        raise SyncError("Local evidence checksum failed; upload was not attempted.")
                    self.client.upload(attachment, content)
                else:
                    content = self.client.download(attachment)
                    if len(content) != attachment["size_bytes"] or hashlib.sha256(content).hexdigest() != attachment["sha256"]:
                        raise SyncError("Downloaded evidence checksum failed.")
                    temporary = path.with_suffix(".download")
                    with temporary.open("wb") as stream:
                        stream.write(content)
                        stream.flush()
                        import os
                        os.fsync(stream.fileno())
                    temporary.replace(path)
                self.sql.execute("UPDATE file_transfers SET status='done',last_error=NULL WHERE attachment_id=?", (attachment["id"],))
            except (SyncError, OSError) as exc:
                error = str(exc) if isinstance(exc, SyncError) else "Local evidence file is unavailable."
                self.retry("file_transfers", attachment["id"], row["attempts"], error)
                self.db.set_meta("sync_error", error)

    def exchange(self, *, force=True, budget=25) -> dict:
        if not self.config["sync_enabled"]:
            return self.status()
        now = utcnow()
        if not force and self.db.meta("next_sync_at") and timestamp(self.db.meta("next_sync_at")) > now:
            return self.status()
        with open(self.context.data_path("sync.lock"), "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DomainError("Synchronization is already running.")
            self.db.set_meta("next_sync_at", stamp(now + timedelta(seconds=self.config["sync_interval_seconds"])))
            scope = self.config["supabase_url"] + "/" + self.config["workspace_id"]
            bound = self.db.meta("remote_scope")
            if bound and bound != scope:
                self.db.set_meta("sync_error", "This database is bound to a different remote workspace/project; switching it is refused.")
                return self.status()
            try:
                self.client = self.client or Supabase(self.context, self.config)
                if bound is None:
                    probe = self.client.pull(0, 1)
                    if not isinstance(probe.get("changes"), list) or type(probe.get("cursor")) is not int:
                        raise SyncError("Remote workspace preflight failed.")
                self.db.set_meta("remote_scope", scope)
                self.db.set_meta("sync_error", "")
                deadline = time.monotonic() + budget
                self.transfer_files(deadline, force)
                for operation in self.sql.execute("SELECT * FROM sync_outbox WHERE status='pending' ORDER BY sequence").fetchall():
                    if time.monotonic() >= deadline:
                        break
                    if not force and operation["next_attempt_at"] and timestamp(operation["next_attempt_at"]) > utcnow():
                        # Preserve per-entity delivery order while allowing unrelated records through.
                        continue
                    earlier = self.sql.execute(
                        "SELECT 1 FROM sync_outbox WHERE entity_type=? AND entity_id=? AND sequence<? AND status IN ('pending','conflict')",
                        (operation["entity_type"], operation["entity_id"], operation["sequence"]),
                    ).fetchone()
                    if earlier is not None:
                        continue
                    if operation["entity_type"] == "attachments" and self.sql.execute(
                        "SELECT 1 FROM file_transfers WHERE attachment_id=? AND direction='upload' AND status='pending'", (operation["entity_id"],),
                    ).fetchone():
                        continue
                    try:
                        response = self.client.push(dict(operation))
                    except SyncError as exc:
                        self.retry("sync_outbox", operation["operation_id"], operation["attempts"], str(exc))
                        raise
                    with self.db.transaction():
                        table, identifier = operation["entity_type"], operation["entity_id"]
                        revision = response.get("revision")
                        if type(revision) is not int or revision < 0:
                            raise SyncError("Invalid server revision.")
                        if response.get("status") == "conflict":
                            remote = self.db.normalize_remote(table, response["payload"]) if response.get("payload") else None
                            self.conflict(table, identifier, remote, revision)
                        elif response.get("status") == "ok":
                            self.sql.execute("UPDATE sync_outbox SET status='delivered',last_error=NULL WHERE operation_id=?", (operation["operation_id"],))
                            self.db.remote_revision(table, identifier, revision)
                            # Only unsent successors are rebased; operation IDs/payloads never change.
                            pending = self.sql.execute(
                                "SELECT operation_id,attempts FROM sync_outbox WHERE entity_type=? AND entity_id=? AND status='pending' ORDER BY sequence", (table, identifier),
                            ).fetchall()
                            for index, successor in enumerate(pending):
                                if successor["attempts"] == 0:
                                    self.sql.execute("UPDATE sync_outbox SET base_revision=? WHERE operation_id=?", (revision + index, successor["operation_id"]))
                        else:
                            raise SyncError("Unknown synchronization acknowledgment.")
                cursor_row = self.sql.execute("SELECT cursor FROM sync_checkpoints WHERE scope=?", (scope,)).fetchone()
                cursor = cursor_row[0] if cursor_row else 0
                caught_up = False
                self.db.set_meta("pull_incomplete", "1")
                while time.monotonic() < deadline:
                    response = self.client.pull(cursor)
                    changes = response.get("changes")
                    next_cursor = response.get("cursor")
                    if not isinstance(changes, list) or type(next_cursor) is not int or next_cursor < cursor:
                        raise SyncError("Invalid durable change cursor response.")
                    with self.db.transaction():
                        for change in changes:
                            if type(change.get("cursor")) is not int or not cursor < change["cursor"] <= next_cursor:
                                raise SyncError("Changes are not ordered by the durable server cursor.")
                            cursor = change["cursor"]
                            table, identifier, revision = change["entity_type"], change["entity_id"], change["revision"]
                            if type(revision) is not int or revision < 1:
                                raise SyncError("Invalid remote record revision.")
                            payload = self.db.normalize_remote(table, change["payload"])
                            if payload["id"] != identifier:
                                raise SyncError("Remote identity mismatch.")
                            known = self.sql.execute("SELECT revision FROM replica_state WHERE entity_type=? AND entity_id=?", (table, identifier)).fetchone()
                            if known and revision <= known[0]:
                                continue
                            pending = self.sql.execute(
                                "SELECT 1 FROM sync_outbox WHERE entity_type=? AND entity_id=? AND status IN ('pending','conflict')", (table, identifier),
                            ).fetchone()
                            if pending:
                                self.conflict(table, identifier, payload, revision)
                            else:
                                self.db.write(table, payload, enqueue=False)
                                self.db.remote_revision(table, identifier, revision)
                                self.db.changed()
                                if table == "attachments" and not payload["deleted_at"]:
                                    path = Path(self.context.data_path("files/" + payload["storage_key"]))
                                    if not path.exists():
                                        self.sql.execute("INSERT INTO file_transfers(attachment_id,direction) VALUES(?,'download') ON CONFLICT DO NOTHING", (identifier,))
                        self.sql.execute("INSERT INTO sync_checkpoints VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET cursor=excluded.cursor", (scope, next_cursor))
                        cursor = next_cursor
                    if not response.get("has_more"):
                        caught_up = True
                        self.db.set_meta("pull_incomplete", "0")
                        break
                    if not changes:
                        raise SyncError("Remote cursor did not advance.")
                self.transfer_files(deadline, force)
                status = self.status()
                if caught_up and not any(status[key] for key in ("pending_changes", "pending_files", "unresolved_conflicts", "last_error")):
                    self.db.set_meta("last_successful_sync", stamp(utcnow()))
            except (SyncError, DomainError, PermissionError) as exc:
                message = str(exc) if not isinstance(exc, PermissionError) else "Network access requires the owner's explicit --allow-network grant."
                self.db.set_meta("sync_error", message)
            except (KeyError, TypeError, sqlite3.IntegrityError):
                self.db.set_meta("sync_error", "Remote record/protocol integrity validation failed; local changes and cursor are preserved.")
            return self.status()
