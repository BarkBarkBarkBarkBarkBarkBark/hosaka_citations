from __future__ import annotations

import copy
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main
from citations.cli import OperatorContext, backup
from citations.database import Database, encoded
from citations.repository import Repository
from citations.service import Service
from citations.supabase import SyncError
from citations.sync import Synchronizer
from citations.validation import DomainError


class SQLiteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.context = OperatorContext(Path(self.directory.name), True)

    def action(self, action, **params):
        result = main.run(action, params, self.context)
        self.assertTrue(result["success"], result)
        return result

    def test_offline_create_edit_resolve_restart_and_pending_operations_are_atomic(self):
        citation = self.action("issue-for-attendee", display_name="Offline Ghost", violation="no-costume")["record"]
        self.action("edit", citation=citation["id"], expected_revision=0, description="Updated offline")
        self.action("resolve", citation=citation["id"], expected_revision=1, override_reason="Party accommodation")
        restarted = OperatorContext(Path(self.directory.name))
        record = main.run("get", {"citation": citation["id"]}, restarted)["record"]
        self.assertEqual(record["status"], "resolved")
        self.assertEqual(record["description"], "Updated offline")
        database = Database(self.context)
        self.assertEqual(database.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertGreater(database.connection.execute("SELECT count(*) FROM sync_outbox WHERE status='pending'").fetchone()[0], 8)
        database.close()
        repository = Repository(self.context)
        service = Service(repository)
        service.issue_for_attendee({"display_name": "Must Roll Back", "violation": "no-costume"})
        repository.database.connection.execute(
            "CREATE TRIGGER fail_outbox BEFORE INSERT ON sync_outbox BEGIN SELECT RAISE(ABORT,'test failure'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            repository.commit()
        repository.close()
        database = Database(self.context)
        self.assertFalse(any(row["display_name"] == "Must Roll Back" for row in database.rows("persons")))
        database.close()

    def test_more_than_64k_and_migration_seed_update_preservation(self):
        for index in range(80):
            self.action("issue-for-attendee", display_name=f"Guest {index}", violation="no-costume", description="x" * 900)
        path = Path(self.context.data_path("citations.sqlite3"))
        self.assertGreater(path.stat().st_size, 65536)
        first = Repository(self.context)
        first.state["violation_types"][0]["title"] = "Organizer's custom title"
        first.touch()
        first.commit()
        first.close()
        second = Repository(self.context)
        self.assertEqual(len(second.state["citations"]), 80)
        self.assertEqual(second.state["violation_types"][0]["title"], "Organizer's custom title")
        self.assertEqual(second.database.connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 1)
        self.assertEqual(len(second.state["violation_types"]), 8)
        second.close()

    def test_evidence_checksum_retry_identity_and_complete_backup(self):
        citation = self.action("issue-for-attendee", display_name="Evidence Ghost", violation="no-costume")["record"]
        selection = self.action("find-citations", query="Evidence")["rows"][0]["selection"]
        token = uuid4().hex
        content = b"%PDF-1.4\nprivate evidence\n%%EOF"
        Path(self.context.data_path("uploads/" + token)).write_bytes(content)
        params = {"selection": selection, "upload_id": token, "filename": "proof.pdf"}
        result = self.action("add-evidence", **params)
        self.action("add-evidence", **params)
        database = Database(self.context)
        self.assertEqual(len(database.rows("attachments")), 1)
        self.assertEqual(database.connection.execute("SELECT count(*) FROM file_transfers").fetchone()[0], 1)
        database.close()
        file = Path(self.context.data_path("files/" + result["files"][0]["name"]))
        self.assertEqual(file.read_bytes(), content)
        archive = Path(self.directory.name) / "backup.zip"
        backup(self.context, archive)
        import zipfile
        with zipfile.ZipFile(archive) as saved:
            self.assertIn("database.sqlite3", saved.namelist())
            self.assertIn("files/" + file.name, saved.namelist())
            self.assertNotIn("supabase-auth.json", saved.namelist())


@unittest.skipUnless(os.environ.get("CITATIONS_TEST_PG"), "Set CITATIONS_TEST_PG to a disposable PostgreSQL database.")
class PostgreSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg = psycopg
        cls.admin = psycopg.connect(os.environ["CITATIONS_TEST_PG"], autocommit=True)
        cls.name = "hc_test_" + uuid4().hex
        cls.admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.name)))
        cls.dsn = psycopg.conninfo.make_conninfo(os.environ["CITATIONS_TEST_PG"], dbname=cls.name,
                                                 options="-c search_path=hosaka_citations,public")
        cls.connection = psycopg.connect(cls.dsn, autocommit=True)
        cls.connection.execute("""
          DO $$ BEGIN
           IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
           IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
          END $$;
          CREATE SCHEMA auth;
          CREATE TABLE auth.users(id uuid PRIMARY KEY);
          CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
          $$ SELECT nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
          CREATE SCHEMA storage;
          CREATE TABLE storage.buckets(id text PRIMARY KEY,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);
          CREATE TABLE storage.objects(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),bucket_id text REFERENCES storage.buckets(id),name text,UNIQUE(bucket_id,name));
          ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;
          GRANT USAGE ON SCHEMA auth,storage,public TO authenticated,anon;
          GRANT SELECT,INSERT ON storage.objects TO authenticated;
        """)
        cls.connection.execute((ROOT / "citations/migrations/postgres_bootstrap.sql").read_text())
        for migration in sorted((ROOT / "citations/migrations/postgres").glob("*.sql")):
            cls.connection.execute(migration.read_text())

    @classmethod
    def tearDownClass(cls):
        from psycopg import sql
        cls.connection.close()
        cls.admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(cls.name)))
        cls.admin.close()

    def setUp(self):
        self.workspace, self.user = str(uuid4()), str(uuid4())
        self.connection.execute("INSERT INTO auth.users VALUES(%s)", (self.user,))
        self.connection.execute("INSERT INTO hc_workspaces VALUES(%s,'Disposable test')", (self.workspace,))
        self.connection.execute("INSERT INTO hc_members VALUES(%s,%s)", (self.workspace, self.user))
        self.directories = []
        self.objects = {}
        self.upload_failure = False
        self.lost_ack = False

    def tearDown(self):
        for directory in self.directories:
            directory.cleanup()

    def client(self, user=None, workspace=None):
        test = self
        class Client:
            def rpc(self, query, parameters):
                with test.psycopg.connect(test.dsn) as connection:
                    connection.execute("SET LOCAL ROLE authenticated")
                    connection.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (user or test.user,))
                    return connection.execute(query, parameters).fetchone()[0]

            def push(self, operation):
                from psycopg.types.json import Jsonb
                result = self.rpc("SELECT hosaka_citations.hc_push(%s,%s,%s,%s,%s,%s)", (
                    workspace or test.workspace, operation["operation_id"], operation["entity_type"],
                    operation["entity_id"], operation["base_revision"], Jsonb(json.loads(operation["payload"])),
                ))
                if test.lost_ack:
                    test.lost_ack = False
                    raise SyncError("Simulated lost acknowledgment after the committed server write.")
                return result

            def pull(self, cursor, limit=100):
                return self.rpc("SELECT hosaka_citations.hc_pull(%s,%s,%s)", (workspace or test.workspace, cursor, limit))

            def upload(self, attachment, content):
                key = (workspace or test.workspace, attachment["storage_key"])
                existing = test.objects.setdefault(key, content)
                if existing != content:
                    raise SyncError("Immutable object collision.")
                if test.upload_failure:
                    test.upload_failure = False
                    raise SyncError("Simulated interrupted evidence upload.")

            def download(self, attachment):
                return test.objects[(workspace or test.workspace, attachment["storage_key"])]
        return Client()

    def device(self):
        directory = tempfile.TemporaryDirectory()
        self.directories.append(directory)
        context = OperatorContext(Path(directory.name), True)
        context.write_data("citations-config.json", encoded({
            "sync_enabled": True, "workspace_id": self.workspace, "supabase_url": "https://test.invalid",
            "publishable_key": "sb_publishable_test",
        }))
        return context

    def action(self, context, action, **params):
        result = main.run(action, params, context)
        self.assertTrue(result["success"], result)
        return result

    def sync(self, context):
        database = Database(context)
        try:
            result = Synchronizer(database, self.client()).exchange(budget=30)
            self.assertIsNone(result["last_error"], result)
            return result
        finally:
            database.close()

    def test_two_devices_offline_lost_ack_remote_resolution_and_convergence(self):
        first, second = self.device(), self.device()
        citation = self.action(first, "issue-for-attendee", display_name="Offline", violation="no-costume")["record"]
        self.action(first, "edit", citation=citation["id"], expected_revision=0, description="Offline edit")
        self.lost_ack = True
        database = Database(first)
        status = Synchronizer(database, self.client()).exchange(budget=30)
        self.assertIsNotNone(status["last_error"])
        database.close()
        self.sync(first)
        self.sync(first)
        self.sync(second)
        self.assertEqual(self.action(second, "get", citation=citation["id"])["record"]["description"], "Offline edit")
        self.action(second, "resolve", citation=citation["id"], expected_revision=1, override_reason="Remote organizer")
        self.lost_ack = True
        database = Database(second)
        failed = Synchronizer(database, self.client()).exchange(budget=30)
        self.assertIsNotNone(failed["last_error"])
        database.close()
        audits = self.connection.execute(
            "SELECT count(*) FROM hc_citation_events WHERE workspace_id=%s AND event_type='remote_revision'", (self.workspace,),
        ).fetchone()[0]
        self.sync(second)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM hc_citation_events WHERE workspace_id=%s AND event_type='remote_revision'", (self.workspace,),
        ).fetchone()[0], audits)
        self.sync(first)
        self.assertEqual(self.action(first, "get", citation=citation["id"])["record"]["status"], "resolved")
        count = self.connection.execute("SELECT count(*) FROM hc_citations WHERE workspace_id=%s", (self.workspace,)).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(self.sync(first)["pending_changes"], 0)

    def test_concurrent_edits_preserve_both_versions_and_explicit_reconciliation(self):
        first, second = self.device(), self.device()
        citation = self.action(first, "issue-for-attendee", display_name="Conflict", violation="no-costume")["record"]
        self.sync(first)
        self.sync(second)
        self.action(first, "edit", citation=citation["id"], expected_revision=0, description="Device A")
        self.action(second, "edit", citation=citation["id"], expected_revision=0, description="Device B")
        self.sync(first)
        status = self.sync(second)
        self.assertGreater(status["unresolved_conflicts"], 0)
        database = Database(second)
        synchronizer = Synchronizer(database, self.client())
        conflicts = synchronizer.conflicts()
        conflict = next(row for row in conflicts if row["entity_type"] == "citations")
        self.assertEqual(conflict["local_payload"]["description"], "Device B")
        self.assertEqual(conflict["remote_payload"]["description"], "Device A")
        synchronizer.resolve(conflict["id"], "local")
        database.close()
        self.sync(second)
        self.sync(first)
        self.assertEqual(self.action(first, "get", citation=citation["id"])["record"]["description"], "Device B")

    def test_tombstones_prevent_resurrection_and_evidence_recovers(self):
        first, second = self.device(), self.device()
        citation = self.action(first, "issue-for-attendee", display_name="Evidence", violation="no-costume")["record"]
        selection = self.action(first, "find-citations", query="Evidence")["rows"][0]["selection"]
        token = uuid4().hex
        content = b"%PDF-1.4\nprivate\n%%EOF"
        Path(first.data_path("uploads/" + token)).write_bytes(content)
        self.action(first, "add-evidence", selection=selection, upload_id=token, filename="proof.pdf")
        self.upload_failure = True
        database = Database(first)
        Synchronizer(database, self.client()).exchange(budget=30)
        database.close()
        self.sync(first)
        self.sync(second)
        database = Database(second)
        attachment = database.rows("attachments")[0]
        self.assertEqual(Path(second.data_path("files/" + attachment["storage_key"])).read_bytes(), content)
        database.close()
        selection = self.action(first, "find-citations", query="Evidence")["rows"][0]["selection"]
        self.action(first, "manage-citation", selection=selection, operation="Delete citation", reason="Test deletion")
        self.action(second, "edit", citation=citation["id"], expected_revision=1, description="Stale edit")
        self.sync(first)
        self.sync(second)
        database = Database(second)
        sync = Synchronizer(database, self.client())
        conflict = next(row for row in sync.conflicts() if row["entity_type"] == "citations")
        with self.assertRaises(DomainError):
            sync.resolve(conflict["id"], "local")
        sync.resolve(conflict["id"], "remote")
        database.close()
        self.sync(second)
        self.assertEqual(self.action(second, "search")["rows"], [])
        self.assertTrue(self.connection.execute("SELECT deleted_at IS NOT NULL FROM hc_citations WHERE workspace_id=%s AND id=%s", (self.workspace, citation["id"])).fetchone()[0])

    def test_workspace_rls_storage_policy_and_operation_identity(self):
        context = self.device()
        self.sync(context)
        outsider = str(uuid4())
        self.connection.execute("INSERT INTO auth.users VALUES(%s)", (outsider,))
        with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
            self.client(user=outsider).pull(0)
        with self.psycopg.connect(self.dsn) as connection:
            connection.execute("SET LOCAL ROLE authenticated")
            connection.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (outsider,))
            self.assertEqual(connection.execute("SELECT count(*) FROM hc_violation_types").fetchone()[0], 0)
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                connection.execute("INSERT INTO storage.objects(bucket_id,name) VALUES('halloween-citations-evidence',%s)", (self.workspace + "/forbidden",))
        database = Database(context)
        operation = dict(database.connection.execute("SELECT * FROM sync_outbox LIMIT 1").fetchone())
        altered = copy.deepcopy(operation)
        payload = json.loads(altered["payload"])
        payload["title"] = "Reused operation attack"
        altered["payload"] = encoded(payload)
        with self.assertRaises(self.psycopg.errors.RaiseException):
            self.client().push(altered)
        database.close()

    def test_application_is_isolated_in_its_own_schema_and_owner_role(self):
        self.connection.execute("CREATE TABLE IF NOT EXISTS public.other_application(id int)")
        owners = self.connection.execute(
            "SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class WHERE relnamespace='hosaka_citations'::regnamespace"
        ).fetchall()
        self.assertEqual(owners, [("hosaka_citations_owner",)])
        definers = self.connection.execute(
            "SELECT DISTINCT pg_get_userbyid(proowner) FROM pg_proc WHERE pronamespace='hosaka_citations'::regnamespace AND proname<>'hc_uid'"
        ).fetchall()
        self.assertEqual(definers, [("hosaka_citations_owner",)])
        self.assertFalse(self.connection.execute(
            "SELECT has_table_privilege('hosaka_citations_owner','public.other_application','SELECT,INSERT,UPDATE,DELETE')"
        ).fetchone()[0])
        self.assertFalse(self.connection.execute("SELECT has_schema_privilege('hosaka_citations_owner','auth','USAGE')").fetchone()[0])
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM pg_class WHERE relnamespace='public'::regnamespace AND relname LIKE 'hc\\_%'"
        ).fetchone()[0], 0)

    def test_direct_remote_resolution_is_logged_and_downloads_do_not_create_outbox_loops(self):
        context = self.device()
        citation = self.action(context, "issue-for-attendee", display_name="Remote SQL", violation="no-costume")["record"]
        self.sync(context)
        self.connection.execute(
            "UPDATE hc_citations SET status='resolved',resolved_at=clock_timestamp(),resolved_by='remote organizer',resolution_notes='Remote resolution' "
            "WHERE workspace_id=%s AND id=%s", (self.workspace, citation["id"]),
        )
        self.sync(context)
        result = self.action(context, "get", citation=citation["id"])["record"]
        self.assertEqual(result["status"], "resolved")
        self.assertIsNotNone(result["elapsed_resolution_seconds"])
        self.assertEqual(result["due_at"], citation["due_at"])
        self.assertTrue(any(row["event_type"] == "remote_revision" for row in self.action(context, "history", citation=citation["id"])["rows"]))
        self.assertEqual(self.sync(context)["pending_changes"], 0)

    def test_enabling_remote_later_uploads_existing_offline_records(self):
        context = self.device()
        config = json.loads(context.read_data("citations-config.json"))
        config["sync_enabled"] = False
        context.write_data("citations-config.json", encoded(config))
        citation = self.action(context, "issue-for-attendee", display_name="Later enabled", violation="bad-pun")["record"]
        self.assertFalse(self.action(context, "sync-status")["record"]["enabled"])
        config["sync_enabled"] = True
        context.write_data("citations-config.json", encoded(config))
        self.assertEqual(self.sync(context)["pending_changes"], 0)
        self.assertEqual(str(self.connection.execute("SELECT id FROM hc_citations WHERE workspace_id=%s", (self.workspace,)).fetchone()[0]), citation["id"])

    def test_commit_order_cursor_cannot_skip_a_slow_earlier_writer(self):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
        from psycopg.types.json import Jsonb
        from citations.validation import stamp, utcnow
        at = stamp(utcnow())
        def person(name):
            return {"id": str(uuid4()), "display_name": name, "email": None, "phone": None,
                    "created_at": at, "updated_at": at, "deleted_at": None}
        first, second = person("Slow commit"), person("Later commit")
        connection = self.psycopg.connect(self.dsn)
        try:
            connection.execute("SET LOCAL ROLE authenticated")
            connection.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (self.user,))
            connection.execute("SELECT hc_push(%s,%s,'persons',%s,0,%s)",
                               (self.workspace, str(uuid4()), first["id"], Jsonb(first)))
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.client().push, {
                    "operation_id": str(uuid4()), "entity_type": "persons", "entity_id": second["id"],
                    "base_revision": 0, "payload": encoded(second),
                })
                with self.assertRaises(FutureTimeout):
                    future.result(timeout=.1)
                self.assertEqual(self.client().pull(0)["changes"], [])
                connection.commit()
                self.assertEqual(future.result(timeout=5)["status"], "ok")
            changes = self.client().pull(0)["changes"]
            self.assertEqual([row["entity_id"] for row in changes], [first["id"], second["id"]])
        finally:
            connection.rollback()
            connection.close()

    def test_remote_setup_and_optional_seed_repeat_without_overwriting_customizations(self):
        from unittest.mock import patch
        from citations.cli import remote_migrate, psql
        self.connection.execute("INSERT INTO hc_schema_migrations VALUES(1),(2),(3) ON CONFLICT DO NOTHING")
        environment = {
            "PGHOST": self.connection.info.host, "PGPORT": str(self.connection.info.port),
            "PGUSER": self.connection.info.user, "PGDATABASE": self.name,
        }
        with patch.dict(os.environ, environment):
            remote_migrate(self.workspace, self.user, "Do not overwrite existing workspace name")
            from citations.models import initial_state
            rows = [{**row, "workspace_id": self.workspace, "sync_revision": 0, "deleted_at": None}
                    for row in initial_state()["violation_types"]]
            script = (ROOT / "citations/seeds/postgres.sql").read_text()
            psql(script, {"defaults": encoded(rows)})
            self.connection.execute("UPDATE hc_violation_types SET title='Organizer customization' WHERE workspace_id=%s AND code='no-costume'", (self.workspace,))
            psql(script, {"defaults": encoded(rows)})
        self.assertEqual(self.connection.execute("SELECT count(*) FROM hc_violation_types WHERE workspace_id=%s", (self.workspace,)).fetchone()[0], 8)
        self.assertEqual(self.connection.execute("SELECT title FROM hc_violation_types WHERE workspace_id=%s AND code='no-costume'", (self.workspace,)).fetchone()[0], "Organizer customization")


if __name__ == "__main__":
    unittest.main()
