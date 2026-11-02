import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extension"))
from citations import terminal


class TerminalTests(unittest.TestCase):
    def test_open_batches_remedies_instead_of_getting_each_record(self):
        row = {"id": "test", "offender": "Guest", "violation": "Test", "remedies": [
            {"description": "Dance", "required": True, "completed": False},
        ]}
        with patch.object(sys, "argv", ["cite", "open"]), patch.object(terminal, "selection_file"), \
             patch.object(terminal, "save_selection"), patch.object(terminal, "run", return_value={"rows": [row], "record": {"total": 1, "generation": 0}}) as run, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            terminal.command_line()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], "list-citations")
        self.assertIn("[ ] Dance", output.getvalue())

    def test_multi_page_list_rejects_a_changed_snapshot(self):
        pages = [
            {"rows": [{"id": "first"}], "record": {"total": 2, "generation": 1}},
            {"rows": [{"id": "second"}], "record": {"total": 2, "generation": 2}},
        ]
        with patch.object(sys, "argv", ["cite", "open"]), patch.object(terminal, "selection_file"), \
             patch.object(terminal, "save_selection") as save, patch.object(terminal, "run", side_effect=pages):
            with self.assertRaisesRegex(SystemExit, "changed while paging"):
                terminal.command_line()
        save.assert_not_called()

    def test_numbered_selection_expiry_and_full_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.json"
            with patch.object(terminal, "selection_file", return_value=path):
                terminal.save_selection([{"id": "full-id"}])
                self.assertEqual(terminal.identifier("1"), "full-id")
                self.assertEqual(terminal.identifier("full-id"), "full-id")
                with patch.object(terminal.time, "time", return_value=10**12):
                    with self.assertRaises(SystemExit):
                        terminal.identifier("1")
