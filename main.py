"""Hosaka API v1 entrypoint; no host internals or direct data filesystem access."""
from __future__ import annotations

from citations.repository import Context, Repository
from citations.service import Service
from citations.validation import DomainError, validate_action
import sqlite3


def run(action: str, params: dict, context: Context) -> dict:
    repository = None
    database = None
    try:
        validate_action(action, params)
        if action in {"sync-now", "sync-status", "sync-conflicts", "sync-resolve", "background-tick"}:
            from citations.database import Database
            from citations.sync import Synchronizer
            database = Database(context)
            sync = Synchronizer(database)
            if action in {"sync-now", "background-tick"}:
                status = sync.exchange(force=action == "sync-now")
                error = status["last_error"]
                if not status["enabled"]:
                    message = "Remote synchronization is disabled; all records remain available in SQLite."
                elif status["unresolved_conflicts"]:
                    message = "Sync requires conflict review; both versions are preserved."
                    error = error or "Review unresolved conflicts before claiming convergence."
                elif any(status[key] for key in ("pending_changes", "pending_files", "pull_incomplete", "last_error")):
                    message = "Sync progress is durable; pending work remains. Local operations are available."
                else:
                    message = "Synchronization completed; SQLite remains the local write target."
                return {"success": error is None, "message": message,
                        "error": error, "record": status}
            if action == "sync-resolve":
                sync.resolve(params["conflict_id"], params["choice"])
            if action == "sync-conflicts":
                rows = []
                for item in sync.conflicts():
                    local = item["local_payload"]
                    person = database.row("persons", local["person_id"]) if item["entity_type"] == "citations" else None
                    item["label"] = (person["display_name"] if person else local.get("display_name") or local.get("title") or item["entity_type"])
                    item["detail"] = f"Local: {local.get('status', 'edited record')} / Remote: {(item['remote_payload'] or {}).get('status', 'edited record')}"
                    if params.get("query", "").casefold() in item["label"].casefold():
                        rows.append(item)
                return {"success": True, "message": "Both local and remote conflict versions are preserved.", "error": None, "rows": rows}
            return {"success": True, "message": "Offline-first synchronization status.", "error": None, "record": sync.status()}
        repository = Repository(context)
        if action == "add-evidence":
            from citations.evidence import attach
            result = attach(Service(repository), params)
        else:
            result = Service(repository).execute(action, params)
        context.log("citations.action", {"action": action, "mutating": repository.dirty})
        repository.commit()
        if action == "add-evidence":
            from pathlib import Path
            Path(context.data_path("uploads/" + params["upload_id"])).unlink(missing_ok=True)
        return {"success": True, "message": result.pop("message"), "error": None, **result}
    except DomainError as exc:
        context.log("citations.rejected", {"action": action})
        return {"success": False, "message": "Citation action rejected.", "error": str(exc)}
    except sqlite3.Error:
        context.log("citations.database_error", {"action": action})
        return {"success": False, "message": "Database operation failed.", "error": "SQLite rejected the operation; no partial transaction was committed. Check database/disk permissions and backups."}
    finally:
        if repository is not None:
            repository.close()
        if database is not None:
            database.close()
