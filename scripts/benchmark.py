"""Repeatable local action timings against disposable synthetic data."""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
import statistics
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "extension" if (ROOT / "extension").is_dir() else ROOT
sys.path.insert(0, str(PACKAGE))

import main
from citations.cli import OperatorContext
from citations.database import Database
from citations.validation import stamp, utcnow


def fixture(context, count):
    database = Database(context)
    at = stamp(utcnow())
    violation = database.rows("violation_types")[0]
    identifiers = []
    try:
        with database.transaction():
            for index in range(count):
                person = str(uuid4())
                identifier = str(uuid4())
                identifiers.append(identifier)
                database.write("persons", {
                    "id": person, "display_name": f"Synthetic Guest {index}",
                    "email": None, "phone": None, "created_at": at,
                    "updated_at": at, "deleted_at": None,
                }, enqueue=False)
                database.write("citations", {
                    "id": identifier, "citation_number": f"TEST-{index}",
                    "person_id": person, "violation_type_id": violation["id"],
                    "issued_at": at, "issued_by": "Benchmark", "description": "Synthetic",
                    "first_offense": True, "status": "open",
                    "resolution_duration_seconds": None, "due_at": None,
                    "resolved_at": None, "resolved_by": None, "resolution_notes": None,
                    "created_at": at, "updated_at": at, "revision": 0, "deleted_at": None,
                }, enqueue=False)
                database.write("remedies", {
                    "id": str(uuid4()), "citation_id": identifier, "description": "Dance",
                    "required": True, "completed": False, "completed_at": None,
                    "completed_by": None, "completion_notes": None, "position": 0,
                    "created_at": at, "updated_at": at, "deleted_at": None,
                }, enqueue=False)
            database.changed()
    finally:
        database.close()
    return identifiers


def summary(samples):
    ordered = sorted(samples)
    return {
        "samples": len(samples),
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * .95 + .999) - 1)], 3),
        "p99_ms": round(ordered[max(0, int(len(ordered) * .99 + .999) - 1)], 3),
    }


def measure(context, action, params):
    started = time.perf_counter()
    result = main.run(action, params, context)
    elapsed = (time.perf_counter() - started) * 1000
    if not result["success"]:
        raise RuntimeError(result["error"])
    return elapsed


def benchmark(count=1000, writes=1000, reads=50, workload="edit"):
    with tempfile.TemporaryDirectory(prefix="citations-benchmark-") as directory:
        context = OperatorContext(Path(directory))
        identifiers = fixture(context, count)
        cold = measure(context, "get", {"citation": identifiers[0]})
        results = {}
        for action, params in (
            ("get", {"citation": identifiers[0]}),
            ("search", {"query": "Synthetic"}),
            ("list-citations", {"query": "Synthetic", "limit": 50}),
        ):
            results[action] = summary([measure(context, action, params) for _ in range(reads)])
        revisions = dict.fromkeys(identifiers, 0)
        timings, by_action = [], {}
        database = Database(context)
        remedies = {row["citation_id"]: row["id"] for row in database.rows("remedies")}
        database.close()
        for index in range(writes):
            identifier = identifiers[(index // 4 if workload == "mixed" else index) % count]
            action = ("edit", "complete-remedy", "resolve", "reopen")[index % 4] if workload == "mixed" else "edit"
            params = {
                "citation": identifier, "expected_revision": revisions[identifier],
            }
            if action == "edit":
                params["description"] = f"Synthetic edit {index}"
            elif action == "complete-remedy":
                params["remedy_id"] = remedies[identifier]
            elif action == "reopen":
                params["reason"] = "Synthetic benchmark"
            elapsed = measure(context, action, params)
            timings.append(elapsed)
            by_action.setdefault(action, []).append(elapsed)
            revisions[identifier] += 1
        for action, samples in by_action.items():
            results[action] = summary(samples)
        results["all_writes"] = summary(timings)
        return {
            "hardware": platform.platform(), "python": platform.python_version(),
            "citations": count, "write_operations": writes, "cold_get_ms": round(cold, 3),
            "transport": "direct extension action, not HTTP or CLI",
            "workload": workload,
            "results": results,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--citations", type=int, default=1000)
    parser.add_argument("--writes", type=int, default=1000)
    parser.add_argument("--reads", type=int, default=50)
    parser.add_argument("--workload", choices=("edit", "mixed"), default="edit")
    args = parser.parse_args()
    if min(args.citations, args.writes, args.reads) < 1:
        parser.error("All sample counts must be positive.")
    if args.workload == "mixed" and args.writes > args.citations * 4:
        parser.error("Mixed workload requires at least one fixture citation per four writes.")
    print(json.dumps(benchmark(args.citations, args.writes, args.reads, args.workload), indent=2))
