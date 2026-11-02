import json
from pathlib import Path
import sys
import tempfile
import unittest
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extension"))
from citations.api import run
from citations.cli import OperatorContext
from citations.repository import Repository
from citations.database import Database
from citations.queries import list_citations


class ScopedRepositoryTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(sqlite3.Connection, "setlimit"), "SQLite runtime limit API requires Python 3.11+")
    def test_thousand_row_page_respects_older_sqlite_parameter_limit(self):
        from scripts.benchmark import fixture

        with tempfile.TemporaryDirectory() as directory:
            context = OperatorContext(Path(directory))
            fixture(context, 1000)
            database = Database(context)
            try:
                database.connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
                result = list_citations(database, {"limit": 1000})
                self.assertEqual(len(result["rows"]), 1000)
                self.assertTrue(all(len(row["remedies"]) == 1 for row in result["rows"]))
            finally:
                database.close()

    def test_scoped_mutation_preserves_unrelated_records_and_events(self):
        with tempfile.TemporaryDirectory() as directory:
            context = OperatorContext(Path(directory))
            first = run("issue-for-attendee", {"display_name": "First", "violation": "no-costume"}, context)["record"]
            second = run("issue-for-attendee", {"display_name": "Second", "violation": "no-costume"}, context)["record"]
            repository = Repository(context)
            before = json.loads(repository.encoded())
            repository.close()
            updated = run("edit", {"citation": first["id"], "expected_revision": 0, "description": "changed"}, context)
            self.assertTrue(updated["success"], updated)
            self.assertFalse(run("edit", {"citation": first["id"], "expected_revision": 0, "description": "stale"}, context)["success"])
            self.assertEqual(run("get", {"citation": second["id"]}, context)["record"]["description"], second["description"])
            repository = Repository(context)
            after = json.loads(repository.encoded())
            repository.close()
            self.assertEqual(len(after["citations"]), 2)
            self.assertEqual(len(after["citation_events"]), len(before["citation_events"]) + 1)
            self.assertEqual(len(after["persons"]), len(before["persons"]))
