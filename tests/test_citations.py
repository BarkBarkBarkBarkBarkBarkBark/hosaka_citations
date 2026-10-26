from __future__ import annotations

import copy
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main
from citations.models import initial_state
from citations.repository import MAX_BYTES, STATE_FILE, Repository
from citations.service import Service
from citations.validation import ACTIONS, DomainError, validate_action
from hosaka.extensions.manifest import validate_package
from hosaka.extensions.worker import Context as HostContext
from citations.database import TABLES
from jsonschema import Draft202012Validator

NOW = datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)

class Context(HostContext):
    def __init__(self, root, storage):
        super().__init__(root, storage, managed=storage)


class CitationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = Context(self.root, True)

    def call(self, action, **params):
        validate_action(action, params)
        repository = Repository(self.context)
        try:
            result = Service(repository, lambda: NOW).execute(action, params)
            repository.commit()
            return result
        finally:
            repository.close()

    def person(self, name="Count Byte", **params):
        return self.call("create-person", display_name=name, **params)["record"]

    def issue(self, person=None, **params):
        person = person or self.person()
        return self.call("issue", person_id=person["id"], violation=params.pop("violation", "no-costume"), **params)["record"]

    def saved(self):
        repository = Repository(self.context)
        try:
            return repository.encoded()
        finally:
            repository.close()

    def restore(self, snapshot, context=None):
        repository = Repository(context or self.context)
        state = json.loads(snapshot)
        try:
            database = repository.database
            with database.transaction():
                for table in ("file_transfers", "sync_conflicts", "sync_outbox", "replica_state", "row_versions"):
                    database.connection.execute(f"DELETE FROM {table}")
                for table in reversed(TABLES):
                    database.connection.execute(f"DELETE FROM {table}")
                for table in TABLES:
                    for row in state[table]:
                        database.write(table, row, enqueue=False)
                database.set_meta("party_settings", json.dumps(state["settings"]))
                database.changed()
        finally:
            repository.close()

    def test_initialization_is_repeatable_lazy_and_seed_ids_are_stable(self):
        first = Repository(self.context).state
        second = Repository(Context(self.root, True)).state
        self.assertEqual(first, second)
        self.assertEqual(len(first["violation_types"]), 8)
        self.assertFalse((self.root / STATE_FILE).exists())
        self.person()
        self.assertEqual(Repository(Context(self.root, True)).state["violation_types"], first["violation_types"])

    def test_optional_contacts_and_attendee_identity_not_name(self):
        first = self.person("Pat")
        second = self.person("Pat", email="pat@example.test", phone="+15555550123")
        self.assertNotEqual(first["id"], second["id"])
        self.assertIsNone(first["email"])
        self.assertIsNone(first["phone"])
        self.assertEqual(len(self.call("search-persons", query="pAt")["rows"]), 2)
        for contact in ({"phone": "5551234"}, {"email": "not-an-email"}, {"phone": "+012345678"}):
            before = self.saved()
            with self.assertRaises(DomainError):
                self.person(**contact)
            self.assertEqual(self.saved(), before)

    def test_custom_templates_unique_codes_and_no_deadline(self):
        self.call("create-violation", code="unlicensed-boo", title="Unlicensed boo", severity=5)
        citation = self.issue(violation="unlicensed-boo")
        self.assertIsNone(citation["due_at"])
        self.assertEqual(citation["remedies"], [])
        before = self.saved()
        for code in ("unlicensed-boo", "INVALID CODE"):
            with self.assertRaises(DomainError):
                self.call("create-violation", code=code, title="Boo")
            self.assertEqual(self.saved(), before)
        self.assertEqual(len(self.call("list-violations")["rows"]), 9)

    def test_uuid_number_and_first_repeat_snapshots(self):
        person = self.person()
        first = self.issue(person)
        second = self.issue(person)
        for citation in (first, second):
            self.assertEqual(str(UUID(citation["id"])), citation["id"])
            self.assertIn(citation["id"], citation["citation_number"])
            self.assertEqual(citation["status"], "open")
        self.assertNotEqual(first["citation_number"], second["citation_number"])
        self.assertTrue(first["first_offense"])
        self.assertFalse(second["first_offense"])
        self.assertTrue(self.issue(person, violation="bad-pun")["first_offense"])
        self.call("void", citation=first["id"], expected_revision=0, reason="Wrong offense")
        self.call("void", citation=second["id"], expected_revision=0, reason="Wrong offense")
        self.assertTrue(self.issue(person)["first_offense"])
        self.assertFalse(self.call("get", citation=second["id"])["record"]["first_offense"])

    def test_backdating_does_not_rewrite_existing_historical_snapshot(self):
        person = self.person()
        later = self.issue(person)
        earlier = self.issue(person, issued_at="2026-10-30T12:00:00-07:00")
        self.assertTrue(earlier["first_offense"])
        self.assertTrue(self.call("get", citation=later["id"])["record"]["first_offense"])

    def test_deadlines_utc_timezone_and_restart_overdue(self):
        self.call("party-settings", party_timezone="America/Los_Angeles")
        citation = self.issue(issued_at="2026-10-31T12:00:00-07:00", resolution_duration_seconds=60)
        self.assertEqual(citation["issued_at"], "2026-10-31T19:00:00.000000Z")
        self.assertEqual(citation["due_at"], "2026-10-31T19:01:00.000000Z")
        restarted = Service(Repository(Context(self.root, True)), lambda: NOW)
        self.assertEqual(len(restarted.overdue({})["rows"]), 1)
        receipt = restarted.get({"citation": citation["citation_number"]})["receipt"]
        self.assertIn("2026-10-31T12:01:00-07:00", receipt)
        self.assertIn("OVERDUE", receipt)
        self.assertNotIn("overdue", Repository(self.context).state["citations"][0]["status"])
        self.call("resolve", citation=citation["id"], expected_revision=0, override_reason="Marshal discretion")
        self.assertEqual(self.call("overdue")["rows"], [])
        self.call("reopen", citation=citation["id"], expected_revision=1, reason="Further haunting")
        self.assertEqual(len(self.call("overdue")["rows"]), 1)

    def test_overdue_boundary_and_due_soon(self):
        citation = self.issue(issued_at="2026-10-31T22:59:00Z", resolution_duration_seconds=60)
        dashboard = self.call("dashboard")["record"]
        self.assertEqual(dashboard["overdue"], 0)
        self.assertEqual(dashboard["due_soon"], 1)
        later = Service(Repository(self.context), lambda: NOW + timedelta(microseconds=1))
        self.assertTrue(later.get({"citation": citation["id"]})["record"]["overdue"])

    def test_remedies_resolution_reopen_void_and_history(self):
        citation = self.issue()
        number = citation["citation_number"]
        first_remedy = citation["remedies"][0]["id"]
        original = self.saved()
        with self.assertRaisesRegex(DomainError, "required remedies"):
            self.call("resolve", citation=number, expected_revision=0)
        self.assertEqual(self.saved(), original)
        record = self.call("add-remedy", citation=number, expected_revision=0,
                           description="Deliver a pun", required=False)["record"]
        extra = record["remedies"][1]["id"]
        self.call("edit-remedy", citation=number, expected_revision=1, remedy_id=extra,
                  description="Deliver a really bad pun", required=True)
        self.call("complete-remedy", citation=number, expected_revision=2, remedy_id=first_remedy,
                  actor="Marshal", notes="Costume approved")
        self.call("complete-remedy", citation=number, expected_revision=3, remedy_id=extra)
        resolved = self.call("resolve", citation=number, expected_revision=4, actor="Marshal", notes="All done")["record"]
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["resolved_by"], "Marshal")
        self.assertIsNotNone(resolved["resolved_at"])
        self.call("reopen", citation=number, expected_revision=5, actor="Marshal", reason="Appeal")
        self.assertTrue(all(remedy["completed"] for remedy in self.call("get", citation=number)["record"]["remedies"]))
        self.call("void", citation=number, expected_revision=6, reason="Dismissed")
        final = self.call("get", citation=number)["record"]
        self.assertEqual(final["status"], "void")
        self.assertEqual(final["revision"], 7)
        events = self.call("history", citation=number)["rows"]
        self.assertEqual([event["event_type"] for event in events], [
            "issued", "remedy_added", "remedy_edited", "remedy_completed", "remedy_completed",
            "resolved", "reopened", "voided",
        ])
        self.assertEqual(events[-2]["event_payload"]["resolution_notes"], "All done")
        self.assertEqual(len(final["remedies"]), 2)

    def test_reasoned_resolution_override_and_first_offense_correction(self):
        citation = self.issue()
        self.call("edit", citation=citation["id"], expected_revision=0, first_offense=False,
                  correction_reason="Earlier paper notice located")
        result = self.call("resolve", citation=citation["id"], expected_revision=1,
                           override_reason="Accessibility accommodation")["record"]
        self.assertEqual(result["status"], "resolved")
        self.assertFalse(result["first_offense"])
        history = self.call("history", citation=citation["id"])["rows"]
        self.assertEqual(history[1]["event_payload"]["correction_reason"], "Earlier paper notice located")
        self.assertEqual(history[2]["event_payload"]["override_reason"], "Accessibility accommodation")

    def test_edits_and_filters(self):
        person = self.person()
        citation = self.issue(person)
        self.issue(person, violation="bad-pun")
        result = self.call("edit", citation=citation["id"], expected_revision=0, description="Pumpkin incident",
                           resolution_duration_seconds=10)["record"]
        self.assertEqual(result["due_at"], "2026-10-31T23:00:10.000000Z")
        self.assertTrue(result["first_offense"])
        self.assertEqual(len(self.call("search", query="count byte", offense="first", status="open")["rows"]), 2)
        self.assertEqual(len(self.call("search", query="pumpkin incident")["rows"]), 1)
        self.assertEqual(len(self.call("search", query=citation["citation_number"])["rows"]), 1)
        self.assertEqual(self.call("search", status="void")["rows"], [])
        self.assertEqual(len(self.call("search", issued_after="2026-10-31T23:00:00Z",
                                       issued_before="2026-10-31T23:00:00Z")["rows"]), 2)

    def test_revision_conflict_between_two_simulated_organizers_is_atomic(self):
        citation = self.issue()
        first_read = self.call("get", citation=citation["id"])["record"]
        second_read = self.call("get", citation=citation["id"])["record"]
        self.call("edit", citation=citation["id"], expected_revision=first_read["revision"], description="First edit")
        before = self.saved()
        with self.assertRaisesRegex(DomainError, "Revision conflict"):
            self.call("resolve", citation=citation["id"], expected_revision=second_read["revision"],
                      override_reason="Concurrent resolution")
        self.assertEqual(before, self.saved())
        self.assertEqual(self.call("get", citation=citation["id"])["record"]["description"], "First edit")

    def test_invalid_transitions_and_foreign_remedy_leave_snapshot_unchanged(self):
        citation = self.issue()
        other = self.issue()
        attempts = [
            ("reopen", {"citation": citation["id"], "expected_revision": 0, "reason": "Not resolved"}),
            ("complete-remedy", {"citation": citation["id"], "expected_revision": 0,
                                 "remedy_id": other["remedies"][0]["id"]}),
            ("edit", {"citation": citation["id"], "expected_revision": 0, "first_offense": False}),
            ("edit", {"citation": citation["id"], "expected_revision": 0}),
        ]
        for action, params in attempts:
            before = self.saved()
            with self.assertRaises(DomainError):
                self.call(action, **params)
            self.assertEqual(before, self.saved())
        self.call("void", citation=citation["id"], expected_revision=0, reason="Test")
        for action, fields in (("resolve", {}), ("reopen", {"reason": "Test"}), ("void", {"reason": "Test"}),
                               ("edit", {"description": "Test"}), ("add-remedy", {"description": "Test"})):
            before = self.saved()
            with self.assertRaises(DomainError):
                self.call(action, citation=citation["id"], expected_revision=1, **fields)
            self.assertEqual(before, self.saved())

    def test_timestamp_and_settings_validation(self):
        person = self.person()
        for value in ("2026-10-31T12:00:00", "not-a-date", "2026-11-01T00:00:00Z",
                      "0001-01-01T00:00:00+14:00"):
            with self.assertRaises(DomainError):
                self.issue(person, issued_at=value)
        before = self.saved()
        with self.assertRaises(DomainError):
            self.call("party-settings", party_timezone="../../etc/passwd")
        self.assertEqual(before, self.saved())
        with self.assertRaises(DomainError):
            self.call("search", issued_after="2026-11-01T00:00:00Z", issued_before="2026-10-31T00:00:00Z")
        self.call("party-settings", party_timezone="America/Los_Angeles")
        before = self.saved()
        with self.assertRaisesRegex(DomainError, "party timezone"):
            self.issue(person, issued_at="0001-01-01T00:00:00Z")
        self.assertEqual(before, self.saved())

    def test_private_backup_and_contact_free_receipt_roundtrip(self):
        person = self.person(email="private@example.test", phone="+15555550123")
        citation = self.issue(person, actor="Marshal")
        result = self.call("get", citation=citation["id"])
        self.assertNotIn(person["email"], json.dumps(result))
        self.assertNotIn(person["phone"], json.dumps(result))
        self.assertIn("OFFICIAL HALLOWEEN VIOLATION NOTICE", result["receipt"])
        self.assertIn("Fictional party fun", result["receipt"])
        backup = self.call("export")["export_json"]
        self.assertIn(person["email"], backup)
        self.assertEqual(json.loads(backup), Repository(self.context).state)
        with tempfile.TemporaryDirectory() as directory:
            restored = Context(Path(directory), True)
            self.restore(backup, restored)
            self.assertEqual(Repository(restored).state, Repository(self.context).state)
            self.assertEqual(Service(Repository(restored), lambda: NOW).get({"citation": citation["id"]})["receipt"], result["receipt"])

    def test_old_json_is_not_overwritten_or_silently_used_as_sqlite(self):
        self.context.write_data(STATE_FILE, "unmodified legacy backup")
        self.person()
        result = main.run("dashboard", {}, Context(self.root, True))
        self.assertTrue(result["success"])
        self.assertEqual(self.context.read_data(STATE_FILE), "unmodified legacy backup")
        with tempfile.TemporaryDirectory() as directory:
            context = Context(Path(directory), True)
            path = Path(context.data_path("citations.sqlite3"))
            path.write_bytes(b"not a database")
            result = main.run("dashboard", {}, context)
            self.assertFalse(result["success"])
            self.assertEqual(path.read_bytes(), b"not a database")

    def test_sqlite_storage_exceeds_the_legacy_text_limit(self):
        self.person()
        repository = Repository(self.context)
        repository.state["persons"][0]["display_name"] = "a"
        base = len(repository.encoded().encode())
        repository.state["persons"][0]["display_name"] += "a" * (MAX_BYTES - base)
        repository.touch()
        repository.commit()
        repository.close()
        result = main.run("create-person", {"display_name": "Still saved"}, Context(self.root, True))
        self.assertTrue(result["success"])
        self.assertEqual(len(Repository(self.context).state["persons"]), 2)
        backup = main.run("export", {}, Context(self.root, True))
        self.assertTrue(backup["success"])
        self.assertLess(len(json.dumps(backup).encode()), 262144)

    def test_storage_denial_write_error_and_metadata_only_logs(self):
        with self.assertRaises(PermissionError):
            main.run("dashboard", {}, Context(self.root, False))
        broken = Context(self.root, True)
        with patch.object(broken, "write_data", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                main.run("create-person", {"display_name": "Not saved"}, broken)
        self.assertFalse((self.root / STATE_FILE).exists())
        context = Context(self.root, True)
        result = main.run("create-person", {"display_name": "Secret attendee"}, context)
        self.assertTrue(result["success"])
        self.assertNotIn("Secret attendee", json.dumps(context.events))
        self.assertEqual(context.events[0]["event"], "citations.action")
        rejected = main.run("unknown", {}, Context(self.root, True))
        self.assertFalse(rejected["success"])

    def test_host_schema_and_standalone_validation_reject_bad_inputs(self):
        for action, params in (
            ("issue", {"person_id": "a" * 36, "violation": "no-costume", "resolution_duration_seconds": True}),
            ("create-person", {"display_name": ""}),
            ("create-person", {"display_name": "x", "unexpected": "no"}),
            ("create-violation", {"code": "x", "title": "X", "severity": 6}),
            ("resolve", {"citation": "x", "expected_revision": -1}),
            ("dashboard", {"extra": True}),
        ):
            schema = json.loads((ROOT / "schemas" / f"{action}.input.json").read_text())
            self.assertFalse(Draft202012Validator(schema).is_valid(params))
            with self.assertRaises(DomainError):
                validate_action(action, params)
        for name in (" \t", "\x1b[31m"):
            result = main.run("create-person", {"display_name": name}, Context(self.root, True))
            self.assertFalse(result["success"])

    def test_package_contract_all_actions_success_failure_and_ui_bindings(self):
        package = validate_package(ROOT)
        self.assertEqual(package.manifest.id, "halloween-citations")
        self.assertEqual(set(package.manifest.actions), set(ACTIONS))
        self.assertTrue(package.manifest.permissions.network)
        self.assertEqual(package.manifest.permissions.filesystem, "data-directory")
        person = self.person()
        citation = self.issue(person)
        remedy = citation["remedies"][0]["id"]
        samples = {
            "initialize": {}, "dashboard": {}, "create-person": {"display_name": "Witch Hazel"},
            "search-persons": {}, "create-violation": {"code": "test-boo", "title": "Boo"},
            "list-violations": {}, "issue": {"person_id": person["id"], "violation": "bad-pun"},
            "issue-for-attendee": {"display_name": "Fresh Ghost", "violation": "no-costume"},
            "find-citations": {"query": "Count"},
            "manage-citation": {"selection": f"{citation['id']}:0", "operation": "Record remedy", "notes": "Costume worn"},
            "search": {}, "get": {"citation": citation["id"]},
            "edit": {"citation": citation["id"], "expected_revision": 0, "description": "Updated"},
            "add-remedy": {"citation": citation["id"], "expected_revision": 0, "description": "Dance"},
            "edit-remedy": {"citation": citation["id"], "expected_revision": 0, "remedy_id": remedy, "required": False},
            "complete-remedy": {"citation": citation["id"], "expected_revision": 0, "remedy_id": remedy},
            "resolve": {"citation": citation["id"], "expected_revision": 0, "override_reason": "Discretion"},
            "reopen": {"citation": citation["id"], "expected_revision": 1, "reason": "Appeal"},
            "void": {"citation": citation["id"], "expected_revision": 0, "reason": "Dismissed"},
            "overdue": {}, "history": {"citation": citation["id"]}, "party-settings": {}, "export": {},
            "sync-status": {}, "sync-now": {}, "background-tick": {}, "sync-conflicts": {},
            "sync-resolve": {"conflict_id": str(UUID(int=11)), "choice": "local"},
            "add-evidence": {"selection": f"{citation['id']}:0", "upload_id": UUID(int=12).hex, "filename": "proof.pdf"},
        }
        original = self.saved()
        for action, params in samples.items():
            self.restore(original)
            if action == "sync-resolve":
                from citations.database import Database, encoded
                database = Database(self.context)
                database.connection.execute(
                    "INSERT INTO sync_conflicts(id,entity_type,entity_id,local_payload,remote_payload,remote_revision,created_at) VALUES(?,'citations',?,?,?,?,?)",
                    (str(UUID(int=11)), citation["id"], encoded(Repository(self.context).state["citations"][0]),
                     encoded(Repository(self.context).state["citations"][0]), 1, "2026-10-31T00:00:00Z"),
                )
                database.close()
            if action == "add-evidence":
                Path(self.context.data_path("uploads/" + UUID(int=12).hex)).write_bytes(b"%PDF-1.4\nproof\n%%EOF")
            if action == "reopen":
                self.call("resolve", citation=citation["id"], expected_revision=0, override_reason="Discretion")
            with self.subTest(action=action):
                spec = package.manifest.actions[action]
                Draft202012Validator(json.loads((ROOT / spec.input_schema).read_text())).validate(params)
                with patch("main.Service", side_effect=lambda repository: Service(repository, lambda: NOW)):
                    output = main.run(action, params, Context(self.root, True))
                self.assertTrue(output["success"], output)
                validator = Draft202012Validator(json.loads((ROOT / spec.output_schema).read_text()))
                validator.validate(output)
                validator.validate(main.run(action, {"unexpected": True}, Context(self.root, True)))

        nodes = []
        def collect(node):
            nodes.append(node)
            for child in node.get("children", []):
                collect(child)
            for page in node.get("pages", []):
                collect(page["view"])
        collect(package.view["view"])
        self.assertLessEqual(len(nodes), 128)
        inputs = {node["id"]: node.get("default", "") for node in nodes if "id" in node}
        inputs.update({node["selection_id"]: "" for node in nodes if node["type"] == "LookupInput"})
        inputs.update({node["filename_id"]: "" for node in nodes if node["type"] == "FileInput"})
        buttons = [node for node in nodes if node["type"] == "Button"]
        self.assertEqual({node["action"] for node in buttons}, {
            "dashboard", "create-person", "search-persons", "create-violation", "list-violations",
            "issue-for-attendee", "search", "overdue", "manage-citation", "party-settings", "export",
            "sync-now", "sync-status", "sync-conflicts", "sync-resolve", "add-evidence",
        })
        for button in buttons:
            action = button["action"]
            mapped = {key: inputs[binding.removeprefix("params.")] for key, binding in button["params"].items()}
            schema = json.loads((ROOT / package.manifest.actions[action].input_schema).read_text())
            self.assertTrue(set(schema.get("required", [])) <= set(mapped))
            self.assertTrue(set(mapped) <= set(schema["properties"]))
            for key, value in mapped.items():
                # Empty required identifiers are intentionally user-filled.
                kind = schema["properties"][key]["type"]
                self.assertIsInstance(value, {"string": str, "integer": int, "boolean": bool}[kind])

    def test_issue_is_default_page_and_all_other_tools_are_in_the_app_menu(self):
        view = validate_package(ROOT).view["view"]
        self.assertEqual(view["type"], "Navigation")
        self.assertEqual([page["label"] for page in view["pages"]], [
            "Issue citation", "Citation table", "Manage citation", "Attendees",
            "Violation templates", "Dashboard", "Synchronization", "Settings and backup",
        ])
        main_page = view["pages"][0]["view"]["children"]
        self.assertEqual([node["id"] for node in main_page if "id" in node], [
            "issue_attendee_name", "violation", "description", "remedy", "duration",
        ])
        self.assertEqual([node["action"] for node in main_page if node["type"] == "Button"], ["issue-for-attendee"])
        lookup = next(node for node in main_page if node["type"] == "LookupInput")
        self.assertEqual((lookup["action"], lookup["selection_id"]), ("search-persons", "issue_person_id"))
        self.assertFalse(any(node.get("bind") in {"result.record", "result.rows", "result.export_json"} for node in main_page))
        table_page = view["pages"][1]["view"]["children"]
        table = next(node for node in table_page if node["type"] == "Table")
        self.assertEqual(table["columns"], [
            "issued_at", "offender", "violation", "status", "overdue", "first_offense", "due_at",
        ])
        self.assertEqual(view["pages"][1]["load"], {"action": "search", "params": {}})
        manage_nodes = view["pages"][2]["view"]["children"]
        self.assertEqual(next(node for node in manage_nodes if node["type"] == "LookupInput")["action"], "find-citations")
        self.assertFalse(any(node.get("id") in {"citation", "expected_revision", "remedy_id"} for node in manage_nodes))
        operations = next(node["options"] for node in manage_nodes if node.get("id") == "manage_operation")
        self.assertEqual(operations, json.loads((ROOT / "schemas/manage-citation.input.json").read_text())["properties"]["operation"]["enum"])

    def test_first_citation_creates_attendee_and_later_exact_name_reuses_them(self):
        first = self.call("issue-for-attendee", display_name="  New Ghost  ", violation="no-costume")["record"]
        second = self.call("issue-for-attendee", display_name="new ghost", violation="no-costume")["record"]
        self.assertEqual(first["person_id"], second["person_id"])
        self.assertEqual(first["offender"], "New Ghost")
        self.assertTrue(first["first_offense"])
        self.assertFalse(second["first_offense"])
        self.assertEqual(len(Repository(self.context).state["persons"]), 1)
        self.assertEqual(len(self.call("search-persons", query="ghost")["rows"]), 1)

    def test_ambiguous_name_requires_dropdown_selection_and_stale_selection_fails(self):
        first = self.person("Twin Ghost")
        second = self.person("Twin Ghost")
        before = self.saved()
        with self.assertRaisesRegex(DomainError, "dropdown"):
            self.call("issue-for-attendee", display_name="Twin Ghost", violation="no-costume")
        self.assertEqual(self.saved(), before)
        selected = self.call("issue-for-attendee", display_name="Twin Ghost", person_id=second["id"], violation="no-costume")["record"]
        self.assertEqual(selected["person_id"], second["id"])
        self.assertNotEqual(selected["person_id"], first["id"])
        before = self.saved()
        with self.assertRaisesRegex(DomainError, "no longer matches"):
            self.call("issue-for-attendee", display_name="Different Ghost", person_id=second["id"], violation="no-costume")
        self.assertEqual(self.saved(), before)

    def test_invalid_first_citation_does_not_leave_orphan_attendees(self):
        result = main.run("issue-for-attendee", {
            "display_name": "Unsaved Ghost", "violation": "no-costume", "issued_at": "2999-01-01T00:00:00Z",
        }, Context(self.root, True))
        self.assertFalse(result["success"])
        self.assertFalse((self.root / STATE_FILE).exists())
        self.assertEqual(len(Repository(self.context).state["persons"]), 0)
        self.assertEqual(Repository(self.context).state["citations"], [])

    def test_new_violation_phrase_creates_one_template_and_reuses_it(self):
        first = self.call("issue-for-attendee", display_name="Sock Ghost", violation="Socks with sandals")["record"]
        templates = [row for row in Repository(self.context).state["violation_types"] if row["title"] == "Socks with sandals"]
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["code"], "socks-with-sandals")
        self.assertEqual(templates[0]["severity"], 1)
        self.assertEqual(first["violation_type_id"], templates[0]["id"])
        second = self.call("issue-for-attendee", display_name="Sock Ghost", violation="socks with sandals")["record"]
        self.assertEqual(second["violation_type_id"], first["violation_type_id"])
        again = [row for row in Repository(self.context).state["violation_types"] if row["title"].casefold() == "socks with sandals"]
        self.assertEqual(len(again), 1)
        coded = self.call("issue-for-attendee", display_name="Sock Ghost", violation="no-costume")["record"]
        self.assertNotEqual(coded["violation_type_id"], first["violation_type_id"])
        self.assertEqual(self.call("get", citation=coded["id"])["record"]["violation"], "Attending without a costume")

    def test_find_citations_uses_names_open_default_human_details_and_newest_first(self):
        person = self.person()
        earlier = self.issue(person, issued_at="2026-10-30T22:00:00Z")
        later = self.issue(person, violation="bad-pun")
        self.call("resolve", citation=earlier["id"], expected_revision=0, override_reason="Dismissed")
        rows = self.call("find-citations", query="count")["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["display_name"], person["display_name"])
        self.assertIn("dramatic apology", rows[0]["detail"])
        self.assertNotIn(later["id"], rows[0]["detail"])
        self.assertNotIn(later["citation_number"], rows[0]["detail"])
        all_rows = self.call("find-citations", query="Count", status="all")["rows"]
        self.assertEqual([row["selection"].split(":")[0] for row in all_rows], [later["id"], earlier["id"]])
        self.assertEqual([row["id"] for row in self.call("search")["rows"]], [later["id"], earlier["id"]])

    def test_manage_records_required_remedies_and_resolves_without_human_identifiers(self):
        citation = self.issue()
        self.call("add-remedy", citation=citation["id"], expected_revision=0, description="Dance", required=True)
        self.call("add-remedy", citation=citation["id"], expected_revision=1, description="Optional pun", required=False)
        selection = self.call("find-citations", query="Count")["rows"][0]["selection"]
        record = self.call("manage-citation", selection=selection, operation="Record remedy",
                           notes="Costume and dance witnessed", actor="Marshal")["record"]
        self.assertEqual(record["status"], "resolved")
        self.assertTrue(all(row["completed"] for row in record["remedies"] if row["required"]))
        self.assertFalse(record["remedies"][-1]["completed"])
        self.assertEqual(record["resolution_notes"], "Costume and dance witnessed")
        self.assertEqual(self.call("find-citations", query="Count")["rows"], [])
        history = self.call("history", citation=citation["id"])["rows"]
        self.assertEqual([row["event_type"] for row in history][-4:], [
            "remedy_completed", "remedy_completed", "remedy_recorded", "resolved",
        ])

    def test_manage_can_record_without_resolving_and_conflicts_do_not_overwrite(self):
        citation = self.issue()
        selection = self.call("find-citations", query="Count")["rows"][0]["selection"]
        record = self.call("manage-citation", selection=selection, operation="Record remedy",
                           notes="Costume worn", resolve_after=False)["record"]
        self.assertEqual(record["status"], "open")
        self.assertTrue(record["remedies"][0]["completed"])
        before = self.saved()
        with self.assertRaisesRegex(DomainError, "changed"):
            self.call("manage-citation", selection=selection, operation="Void citation", reason="Concurrent dismissal")
        self.assertEqual(self.saved(), before)
        fresh = self.call("find-citations", query="Count")["rows"][0]["selection"]
        self.assertEqual(self.call("manage-citation", selection=fresh, operation="Record remedy",
                                   notes="Ready to close")["record"]["status"], "resolved")

    def test_manage_all_advanced_operations_remain_available_and_invalid_actions_are_atomic(self):
        citation = self.issue()
        original = self.saved()
        samples = {
            "View citation": {}, "Activity history": {},
            "Resolve citation": {"reason": "Accessibility accommodation"},
            "Reopen citation": {"reason": "Another haunting"},
            "Void citation": {"reason": "Wrong guest"},
            "Edit incident and deadline": {"description": "Updated incident", "resolution_duration_seconds": 7200},
            "Correct first offense": {"first_offense": False, "reason": "Paper notice found"},
            "Add remedy": {"description": "Dance", "required": False},
            "Edit remedy": {"description": "Wear a cape"},
            "Complete one remedy": {"notes": "Costume worn"},
        }
        for operation, params in samples.items():
            self.restore(original)
            if operation == "Reopen citation":
                self.call("resolve", citation=citation["id"], expected_revision=0, override_reason="Discretion")
            selection = self.call("find-citations", query="Count", status="all")["rows"][0]["selection"]
            with self.subTest(operation=operation):
                self.call("manage-citation", selection=selection, operation=operation, **params)
        self.restore(original)
        for selection, operation, params in (
            ("", "Record remedy", {"notes": "Test"}),
            ("bad-selection", "Record remedy", {"notes": "Test"}),
            (f"{citation['id']}:0", "Record remedy", {}),
            (f"{citation['id']}:0", "Add remedy", {"description": "x" * 501}),
            (f"{citation['id']}:0", "Edit remedy", {"description": "x" * 501}),
        ):
            before = self.saved()
            with self.assertRaises(DomainError):
                self.call("manage-citation", selection=selection, operation=operation, **params)
            self.assertEqual(self.saved(), before)

    def test_small_event_fixture_persists_and_no_duplicate_read_side_effects(self):
        people = [self.person(f"Guest {index}") for index in range(12)]
        codes = [row["code"] for row in self.call("list-violations")["rows"]]
        for index in range(12):
            self.issue(people[index], violation=codes[index % 8])
        for _ in range(3):
            self.issue(people[0])
        before = self.saved()
        for _ in range(3):
            self.assertEqual(len(self.call("search")["rows"]), 15)
            self.call("dashboard")
            self.call("overdue")
        self.assertEqual(self.saved(), before)
        state = Repository(Context(self.root, True)).state
        self.assertEqual(len(state["persons"]), 12)
        self.assertEqual(len(state["citation_events"]), 15)
        self.assertFalse(state["citations"][-1]["first_offense"])


@unittest.skipIf(os.geteuid() == 0, "Host trusted-local workers refuse root.")
class HostIntegrationTests(unittest.TestCase):
    def test_disposable_real_worker_cli_api_lifecycle_preserves_data_and_native_registry(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from hosaka.extensions.manager import Manager
        from hosaka.extensions.runner import run
        from hosaka.web.extensions_api import manager as api_manager, router

        native = Manager()
        native_registry = native.lock.read_bytes() if native.lock.exists() else None
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"HOSAKA_DATA_DIR": directory}):
                manager = Manager()
                manager.link(ROOT)
                info = manager.info("halloween-citations")
                self.assertFalse(info["enabled"])
                self.assertEqual(info["grants"], [])
                self.assertIsNone(info["approved_digest"])
                self.assertFalse(run(manager, "halloween-citations", "dashboard", {})["ok"])
                manager.trust("halloween-citations", allow_storage=True, allow_managed_data=True)
                manager.enable("halloween-citations", True)
                person_result = run(manager, "halloween-citations", "create-person", {"display_name": "Worker Ghost"})
                self.assertTrue(person_result["ok"], person_result)
                person = person_result["result"]["record"]
                citation_result = run(manager, "halloween-citations", "issue",
                                      {"person_id": person["id"], "violation": "no-costume", "issued_at": "2026-10-01T00:00:00Z"})
                self.assertTrue(citation_result["ok"], citation_result)
                citation = citation_result["result"]["record"]
                restarted = Manager()
                self.assertEqual(run(restarted, "halloween-citations", "get", {"citation": citation["id"]})["result"]["record"], citation)
                cli = subprocess.run([
                    sys.executable, "-m", "hosaka.extensions", "run", "halloween-citations", "get",
                    "--params", json.dumps({"citation": citation["citation_number"]}), "--json",
                ], check=True, capture_output=True, text=True, timeout=20)
                self.assertEqual(json.loads(cli.stdout)["result"]["record"]["id"], citation["id"])
                from hosaka import main_console

                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    current = Path(directory)
                    outcome = main_console._dispatch_command(
                        "/extensions run halloween-citations overdue --params '{}'", current, "llm",
                    )
                self.assertEqual(outcome, (current, "llm"))
                self.assertEqual(len(json.loads(output.getvalue())["result"]["rows"]), 1)
                invalid = run(restarted, "halloween-citations", "resolve", {"citation": citation["id"], "expected_revision": 0})
                self.assertFalse(invalid["ok"])
                self.assertEqual(invalid["error"]["code"], "action_failed")

                app = FastAPI()
                app.include_router(router)
                app.dependency_overrides[api_manager] = lambda: restarted
                with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
                    catalog = client.get("/api/v1/extensions")
                    self.assertEqual(catalog.status_code, 200, catalog.text)
                    self.assertIn("halloween-citations", [row["id"] for row in catalog.json()["extensions"]])
                    view = client.get("/api/v1/extensions/halloween-citations/view").json()
                    result = client.post("/api/v1/extensions/halloween-citations/actions/overdue", json={
                        "invocation_id": str(UUID(int=1)), "digest": view["digest"], "params": {},
                    })
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertEqual(len(result.json()["result"]["rows"]), 1)

                path = Path(directory) / "extension-data/halloween-citations/citations.sqlite3"
                original = path.read_bytes()
                manager.enable("halloween-citations", False)
                manager.remove("halloween-citations")
                self.assertEqual(path.read_bytes(), original)
                manager.link(ROOT)
                self.assertFalse(manager.info("halloween-citations")["enabled"])
                manager.enable("halloween-citations", True)
                self.assertTrue(run(manager, "halloween-citations", "dashboard", {})["ok"])
                self.assertEqual(path.read_bytes(), original)
                logs = (Path(directory) / "logs/extensions/halloween-citations.jsonl").read_text()
                self.assertNotIn("Worker Ghost", logs)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(native.lock.read_bytes() if native.lock.exists() else None, native_registry)


if __name__ == "__main__":
    unittest.main()
