"""Portable interactive citation workflows through Hosaka's guarded runner."""
from __future__ import annotations

import argparse
import hashlib
from functools import lru_cache
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time


def hosaka(*args):
    executable = shutil.which("hosaka")
    if executable is None:
        raise SystemExit("Hosaka is not on PATH. Install Hosaka and reopen your terminal.")
    return [executable, *args]


def run(action, params=None):
    trace = os.environ.get("CITE_TRACE") == "1"
    started = time.monotonic()
    command = hosaka("extensions", "run", "citations", action, "--params", json.dumps(params or {}))
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=5)
                break
            except subprocess.TimeoutExpired:
                if trace:
                    print(f"[trace] {action}: {time.monotonic() - started:.1f}s elapsed", file=sys.stderr)
    if trace:
        print(f"[trace] {action}: {(time.monotonic() - started) * 1000:.1f} ms", file=sys.stderr)
    try:
        report = json.loads(stdout)
    except ValueError as exc:
        raise SystemExit("No readable response. Check the citation before retrying.\n" + stderr) from exc
    result = report.get("result") or {}
    if process.returncode or not report.get("ok") or result.get("success") is False:
        raise SystemExit(result.get("error") or (report.get("error") or {}).get("message") or stderr or "Action failed.")
    return result


@lru_cache(maxsize=1)
def data_root():
    result = subprocess.run(hosaka("paths", "--json"), capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise SystemExit(result.stderr or "Cannot resolve Hosaka paths.")
    return Path(json.loads(result.stdout)["paths"]["extension_data"]) / "citations"


def selection_file():
    try:
        descriptor = sys.stdin.fileno()
        terminal = os.ttyname(descriptor)
        stat = os.fstat(descriptor)
    except (OSError, ValueError) as exc:
        raise SystemExit("Numbered selections require an interactive terminal.") from exc
    root = data_root()
    path = root / "citations-config.json"
    config = json.loads(path.read_text()) if path.exists() else {}
    identity = [
        str(root.resolve()), config.get("supabase_url"), config.get("workspace_id"),
        terminal, stat.st_dev, stat.st_ino, stat.st_ctime_ns, os.getsid(0),
    ]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    directory = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "hosaka-cite"
    if directory.is_symlink():
        raise SystemExit("Citation selection cache must not be a symlink.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory / (key + ".json")


def save_selection(rows):
    path = selection_file()
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"saved_at": time.time(), "rows": {str(i): row["id"] for i, row in enumerate(rows, 1)}}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def identifier(value):
    if not value.isdecimal():
        return value
    try:
        saved = json.loads(selection_file().read_text())
        if not 0 <= time.time() - saved["saved_at"] <= 8 * 3600:
            raise ValueError("Expired terminal selection")
        return saved["rows"][str(int(value))]
    except (FileNotFoundError, KeyError, ValueError, TypeError) as exc:
        raise SystemExit("No current matching selection. Run cite open in this terminal first.") from exc


def clean(value):
    return "".join(character if character.isprintable() else " " for character in str(value))


def table(headers, rows, widths):
    def line(values):
        return " | ".join(
            (clean(value)[:width - 1] + "~" if len(clean(value)) > width else clean(value)).ljust(width)
            for value, width in zip(values, widths)
        ).rstrip()
    print(line(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(line(row))


def remedies(result):
    return result["record"].get("remedies", result.get("rows", []))


def display(result, compact=True):
    record = result.get("record") or {}
    if not isinstance(record, dict) or "offender" not in record:
        print(clean(result.get("message", "")))
        print(json.dumps(result.get("record", result.get("rows", {})), indent=2, ensure_ascii=False))
        return
    width = max(30, min(shutil.get_terminal_size((80, 24)).columns, 100))

    def wrapped(value, prefix="  "):
        print(textwrap.fill(clean(value), width=width, initial_indent=prefix, subsequent_indent=" " * len(prefix)))

    print()
    wrapped(record["offender"], "")
    wrapped(record["violation"])
    wrapped("Status: " + record["status"])
    print("\nRemedies")
    checklist = remedies(result)
    if not checklist:
        print("  None")
    for item in checklist:
        wrapped(item["description"] + ("" if item.get("required", True) else " (optional)"),
                "  [x] " if item.get("completed") else "  [ ] ")
    if not compact:
        print("\nDetails")
        wrapped("Citation: " + str(record.get("citation_number") or record["id"]))
        for key, label in (
            ("description", "Incident"), ("issued_at", "Issued"), ("issued_by", "Issued by"),
            ("due_at", "Deadline"), ("resolved_at", "Resolved"), ("resolved_by", "Resolved by"),
            ("resolution_notes", "Resolution notes"),
        ):
            if record.get(key):
                wrapped(f"{label}: {record[key]}")
    print()


def confirm(message):
    if input(message + " [y/N] ").strip().lower() != "y":
        raise SystemExit("Cancelled. Nothing submitted.")


def command_line():
    parser = argparse.ArgumentParser(
        description="Issue and manage citations through Hosaka. Writes save locally; sync is separate.",
        epilog="Numbers refer to the last cite open in this terminal for up to eight hours. Full IDs are accepted.",
    )
    parser.add_argument("command", nargs="?", default="issue", choices=[
        "issue", "types", "open", "show", "remedy", "done", "close", "sync", "status",
    ])
    parser.add_argument("value", nargs="?", help="Selection number, full ID, or search text")
    args = parser.parse_args()
    command = args.command
    if command == "issue":
        params = {
            "display_name": input("Attendee name: ").strip(),
            "violation": input("Violation code (cite types lists them): ").strip(),
            "description": input("Incident description: ").strip(),
            "actor": input("Issued by: ").strip(),
        }
        remedy = input("Required remediation (blank = template default): ").strip()
        if remedy:
            params["remedy"] = remedy
        if not params["display_name"] or not params["violation"]:
            raise SystemExit("Name and violation are required.")
        print(json.dumps(params, indent=2, ensure_ascii=False))
        confirm("Issue this citation?")
        display(run("issue-for-attendee", params))
        print("Saved locally.")
        return
    if command == "types":
        rows = run("list-violations").get("rows", [])
        table(["Code", "Violation", "Default remediation"], [
            [row.get("code", ""), row.get("title", ""), row.get("default_remedy", "")] for row in rows
        ], [22, 25, 30])
        return
    if command == "open":
        selection_file()
        rows = []
        generation = None
        while True:
            page = run("list-citations", {"status": "open", "query": args.value or "", "offset": len(rows), "limit": 1000})
            current_generation = page["record"].get("generation")
            if generation is not None and current_generation != generation:
                raise SystemExit("Citation list changed while paging. Run cite open again.")
            generation = current_generation
            rows.extend(page["rows"])
            if len(rows) >= page["record"]["total"]:
                break
            if not page["rows"]:
                raise SystemExit("Citation list changed while paging. Run cite open again.")
        save_selection(rows)
        table(["#", "Attendee", "Violation", "Status", "Remediation"], [
            [index, row["offender"], row["violation"], "OVERDUE" if row.get("overdue") else "Open",
             "; ".join(("[x] " if remedy["completed"] else "[ ] ") + remedy["description"]
                       + ("" if remedy["required"] else " (optional)") for remedy in row["remedies"]) or "None"]
            for index, row in enumerate(rows, 1)
        ], [4, 16, 22, 8, 36])
        print(f"\n{len(rows)} open citation(s). Use cite show NUMBER for full details.")
        print("Numbers refer to this list in this terminal; listing again replaces them.")
        return
    if command in {"sync", "status"}:
        display(run("sync-now" if command == "sync" else "sync-status"))
        return
    if not args.value:
        parser.error(f"{command} requires a selection number or full citation ID")
    result = run("get", {"citation": identifier(args.value)})
    display(result, compact=command != "show")
    if command == "show":
        return
    record = result["record"]
    if record["status"] != "open":
        raise SystemExit("This citation is not open.")
    params = {"citation": record["id"], "expected_revision": record["revision"]}
    if command == "remedy":
        description = input("New required remediation: ").strip()
        if not description:
            raise SystemExit("Remediation cannot be empty.")
        params.update(description=description, required=True)
        action, confirmation = "add-remedy", f"Add required remediation: {description}?"
    elif command == "done":
        pending = [row for row in remedies(result) if not row["completed"]]
        if not pending:
            raise SystemExit("No incomplete remedies.")
        for index, remedy in enumerate(pending, 1):
            print(f"{index}. {remedy['description']} ({'required' if remedy['required'] else 'optional'})")
        choice = input("Remedy number to mark complete: ").strip()
        if not choice.isdecimal() or not 1 <= int(choice) <= len(pending):
            raise SystemExit("Invalid selection. Nothing changed.")
        selected = pending[int(choice) - 1]
        params.update(remedy_id=selected["id"], notes=input("Completion notes: ").strip())
        action, confirmation = "complete-remedy", f"Mark completed: {selected['description']}?"
    else:
        pending = [row for row in remedies(result) if row["required"] and not row["completed"]]
        print(f"Closing: {record['offender']} - {record['violation']}")
        for item in pending:
            confirm(f"Performed: {item['description']}?")
        actor = input("Recorded by: ").strip()
        notes = input("Completion / resolution notes: ").strip()
        confirm("Record these completions and close this citation?")
        revision, completed = record["revision"], 0
        try:
            for item in pending:
                updated = run("complete-remedy", {
                    "citation": record["id"], "expected_revision": revision,
                    "remedy_id": item["id"], "actor": actor, "notes": notes,
                })
                completed += 1
                print(f"Recorded completion: {item['description']}")
                revision = updated["record"]["revision"]
            closed = run("resolve", {
                "citation": record["id"], "expected_revision": revision, "actor": actor, "notes": notes,
            })
        except (SystemExit, KeyboardInterrupt, EOFError, OSError, KeyError):
            print(f"Close stopped; {completed} completion(s) confirmed saved. Other submitted work may have saved too.")
            print("Inspect with cite show before retrying. Earlier successful changes are retained.")
            raise
        display(closed)
        print("Saved locally.")
        return
    params["actor"] = input("Recorded by: ").strip()
    confirm(confirmation)
    display(run(action, params))
    print("Saved locally.")


def main():
    try:
        command_line()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SystemExit("\nInterrupted. If submission started, check before retrying.") from exc
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Local command error: {exc}") from exc


if __name__ == "__main__":
    main()
