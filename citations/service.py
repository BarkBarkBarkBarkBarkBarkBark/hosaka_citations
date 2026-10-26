"""Citation workflows independent of the Hosaka transport and declarative view."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from citations.repository import Repository
from citations.validation import DomainError, display_time, party_zone, stamp, timestamp, utcnow


class Service:
    def __init__(self, repository: Repository, clock: Callable[[], datetime] = utcnow):
        self.repo = repository
        self.state = repository.state
        self.now = clock()
        self.at = stamp(self.now)

    def execute(self, action: str, params: dict) -> dict:
        handler = getattr(self, action.replace("-", "_"))
        return handler(params)

    def initialize(self, params: dict) -> dict:
        return {"message": "Ready for offline citation entry."}

    def event(self, citation: dict, kind: str, actor: str, payload: dict) -> None:
        self.state["citation_events"].append({
            "id": str(uuid4()), "citation_id": citation["id"], "actor_id": actor or None,
            "event_type": kind, "event_payload": payload, "created_at": self.at,
        })
        self.repo.touch()

    def update(self, citation: dict, params: dict) -> None:
        if citation["revision"] != params["expected_revision"]:
            raise DomainError("Revision conflict. Retrieve the citation and reconcile before retrying.")
        citation["revision"] += 1
        citation["updated_at"] = self.at
        self.repo.touch()

    def editable(self, params: dict) -> dict:
        citation = self.repo.citation(params["citation"])
        if citation["status"] != "open":
            raise DomainError("Only open citations can be edited. Reopen a resolved citation first.")
        return citation

    def create_person(self, params: dict) -> dict:
        phone = params.get("phone", "").strip()
        if phone and not re.fullmatch(r"\+[1-9][0-9]{6,14}", phone):
            raise DomainError("Phone is optional; when supplied use E.164 (+ followed by 7-15 digits).")
        email = params.get("email", "").strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise DomainError("Supply a valid email or leave it blank.")
        person = {
            "id": str(uuid4()), "display_name": params["display_name"].strip(),
            "email": email or None, "phone": phone or None,
            "created_at": self.at, "updated_at": self.at, "deleted_at": None,
        }
        self.state["persons"].append(person)
        self.repo.touch()
        return {"message": f"Attendee added: {person['display_name']}. Contact details are optional.", "record": person}

    def search_persons(self, params: dict) -> dict:
        query = params.get("query", "").casefold()
        zone = party_zone(self.state["settings"]["party_timezone"])
        rows = []
        for person in self.state["persons"]:
            if person["deleted_at"] is not None or query not in person["display_name"].casefold():
                continue
            count = sum(row["person_id"] == person["id"] and row["status"] == "open" for row in self.state["citations"])
            rows.append({**person, "detail": f"{count} open citation(s) / added {display_time(person['created_at'], zone)}"})
        return {"message": f"{len(rows)} attendees.", "rows": rows}

    def create_violation(self, params: dict) -> dict:
        code = params["code"].strip()
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", code):
            raise DomainError("Violation code must be lowercase kebab-case, starting with a letter.")
        if any(row["code"] == code for row in self.state["violation_types"]):
            raise DomainError("That violation code already exists.")
        violation = {
            "id": str(uuid4()), "code": code, "title": params["title"].strip(),
            "description": params.get("description", ""), "default_remedy": params.get("default_remedy", ""),
            "default_resolution_seconds": params.get("default_resolution_seconds"),
            "severity": params.get("severity", 1), "active": True,
            "created_at": self.at, "updated_at": self.at,
        }
        self.state["violation_types"].append(violation)
        self.repo.touch()
        return {"message": f"Violation template created: {code}.", "record": violation}

    def list_violations(self, params: dict) -> dict:
        return {"message": "Eight spooky defaults; custom templates are welcome.",
                "rows": [row.copy() for row in self.state["violation_types"] if row["active"]]}

    def violation_code(self, title: str) -> str:
        """Kebab code for a new template. A taken code, including an inactive one, gets a numeric suffix."""
        slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:64].strip("-")
        if not slug or not slug[0].isalpha():
            slug = f"v-{slug}".strip("-")
        slug = slug[:64].strip("-")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", slug):
            raise DomainError("Violation needs a letter so a template code can be made from it.")
        taken = {row["code"] for row in self.state["violation_types"]}
        if slug not in taken:
            return slug
        for number in range(2, 1000):
            suffix = f"-{number}"
            candidate = f"{slug[:64 - len(suffix)].strip('-')}{suffix}"
            if candidate not in taken and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", candidate):
                return candidate
        raise DomainError("Could not make a unique violation code from that title.")

    def violation(self, value: str) -> dict:
        text = value.strip()
        if not text:
            raise DomainError("Violation is required.")
        if len(text) > 120:
            raise DomainError("Violation title must be 120 characters or fewer.")
        folded = text.casefold()
        for row in self.state["violation_types"]:
            if row["active"] and (text in (row["id"], row["code"]) or folded == row["code"].casefold()):
                return row
        for row in self.state["violation_types"]:
            if row["active"] and row["title"].casefold() == folded:
                return row
        return self.create_violation({"code": self.violation_code(text), "title": text, "severity": 1})["record"]

    def issue(self, params: dict) -> dict:
        person = self.repo.find("persons", params["person_id"])
        if person["deleted_at"] is not None:
            raise DomainError("Cannot issue a citation to a deleted attendee.")
        violation = self.violation(params["violation"])
        issued = timestamp(params["issued_at"]) if params.get("issued_at") else self.now
        if issued > self.now:
            raise DomainError("Incident time cannot be in the future.")
        duration = params.get("resolution_duration_seconds", violation["default_resolution_seconds"])
        first = not any(
            row["person_id"] == person["id"] and row["violation_type_id"] == violation["id"]
            and row["status"] != "void" and row["deleted_at"] is None and timestamp(row["issued_at"]) <= issued
            for row in self.state["citations"]
        )
        identifier = str(uuid4())
        citation = {
            "id": identifier, "citation_number": f"HW-{issued.year}-{identifier}",
            "person_id": person["id"], "violation_type_id": violation["id"],
            "issued_at": stamp(issued), "issued_by": params.get("actor") or None,
            "description": params.get("description", ""), "first_offense": first,
            "status": "open", "resolution_duration_seconds": duration,
            "due_at": stamp(issued + timedelta(seconds=duration)) if duration else None,
            "resolved_at": None, "resolved_by": None, "resolution_notes": None,
            "created_at": self.at, "updated_at": self.at, "revision": 0, "deleted_at": None,
        }
        self.state["citations"].append(citation)
        remedy = params.get("remedy", "").strip() or violation["default_remedy"]
        if remedy:
            self.new_remedy(citation, remedy, True)
        self.event(citation, "issued", params.get("actor", ""), {"first_offense": first})
        return self.details(citation, "Citation issued. The Department of Spooky Compliance has spoken.")

    def issue_for_attendee(self, params: dict) -> dict:
        name = params["display_name"].strip()
        selected = params.get("person_id", "")
        if selected:
            person = self.repo.find("persons", selected)
            if person["deleted_at"] is not None or person["display_name"].casefold() != name.casefold():
                raise DomainError("The selected attendee no longer matches this name. Select a current match again.")
        else:
            matches = [person for person in self.state["persons"] if person["deleted_at"] is None
                       and person["display_name"].casefold() == name.casefold()]
            if len(matches) > 1:
                raise DomainError("Several attendees share this name. Select the intended attendee from the dropdown.")
            person = matches[0] if matches else self.create_person({"display_name": name})["record"]
        issue_params = {key: value for key, value in params.items() if key not in {"display_name", "person_id"}}
        return self.issue({**issue_params, "person_id": person["id"]})

    def new_remedy(self, citation: dict, description: str, required: bool) -> dict:
        remedy = {
            "id": str(uuid4()), "citation_id": citation["id"], "description": description,
            "required": required, "completed": False, "completed_at": None,
            "completed_by": None, "completion_notes": None,
            "position": len(self.remedies(citation)), "created_at": self.at, "updated_at": self.at, "deleted_at": None,
        }
        self.state["remedies"].append(remedy)
        self.repo.touch()
        return remedy

    def remedies(self, citation: dict) -> list[dict]:
        return sorted(
            [row for row in self.state["remedies"] if row["citation_id"] == citation["id"] and row["deleted_at"] is None],
            key=lambda row: row["position"],
        )

    def is_overdue(self, citation: dict) -> bool:
        return citation["status"] == "open" and citation["due_at"] is not None and timestamp(citation["due_at"]) < self.now

    def summary(self, citation: dict) -> dict:
        return {
            **citation, "offender": self.repo.find("persons", citation["person_id"])["display_name"],
            "violation": self.repo.find("violation_types", citation["violation_type_id"])["title"],
            "overdue": self.is_overdue(citation),
        }

    def search(self, params: dict) -> dict:
        query = params.get("query", "").casefold()
        after = timestamp(params["issued_after"]) if params.get("issued_after") else None
        before = timestamp(params["issued_before"]) if params.get("issued_before") else None
        if after and before and after > before:
            raise DomainError("Start time must not be after end time.")
        rows = []
        for citation in self.state["citations"]:
            if citation["deleted_at"] is not None:
                continue
            row = self.summary(citation)
            if params.get("status", "all") not in ("all", row["status"]):
                continue
            if params.get("offense", "all") != "all" and row["first_offense"] != (params["offense"] == "first"):
                continue
            if params.get("overdue_only", False) and not row["overdue"]:
                continue
            if after and timestamp(row["issued_at"]) < after or before and timestamp(row["issued_at"]) > before:
                continue
            if query not in " ".join(str(row[key]) for key in ("offender", "violation", "citation_number", "description")).casefold():
                continue
            rows.append(row)
        rows.sort(key=lambda row: (row["issued_at"], row["id"]), reverse=True)
        return {"message": f"{len(rows)} citations. Overdue is derived at action time.", "rows": rows}

    def overdue(self, params: dict) -> dict:
        return self.search({"overdue_only": True})

    def find_citations(self, params: dict) -> dict:
        rows = self.search({"query": params["query"], "status": params.get("status", "open")})["rows"]
        zone = party_zone(self.state["settings"]["party_timezone"])
        matches = []
        for row in rows:
            citation = self.repo.citation(row["id"])
            remedies = self.remedies(citation)
            descriptions = [remedy["description"] for remedy in remedies if remedy["required"] and not remedy["completed"]]
            detail = f"{row['violation']} / {row['status']} / {display_time(row['issued_at'], zone)}"
            if descriptions:
                detail += " / Remedy: " + "; ".join(descriptions[:2])
                if len(descriptions) > 2:
                    detail += f"; and {len(descriptions) - 2} more."
            matches.append({
                "display_name": row["offender"], "detail": detail,
                "selection": f"{row['id']}:{self.repo.version(row['id'])}",
            })
        return {"message": f"{len(matches)} matching citations.", "rows": matches}

    def manage_citation(self, params: dict) -> dict:
        identifier, separator, revision = params["selection"].rpartition(":")
        if not separator or not revision.isascii() or not revision.isdecimal():
            raise DomainError("Find an attendee and select a citation from the dropdown first.")
        citation = self.repo.citation(identifier)
        if self.repo.version(citation["id"]) != int(revision):
            raise DomainError("This citation changed. Search and select it again before saving.")
        operation = params["operation"]
        actor = params.get("actor", "")
        common = {"citation": citation["id"], "expected_revision": citation["revision"], "actor": actor}
        notes = params.get("notes", "")
        reason = params.get("reason", "")
        if operation == "Record remedy":
            if citation["status"] != "open":
                raise DomainError("Only an open citation can receive a remedy record.")
            if not notes.strip():
                raise DomainError("Describe the remedy performed before saving.")
            required = [row for row in self.remedies(citation) if row["required"] and not row["completed"]]
            for remedy in required:
                self.complete_remedy({
                    **common, "expected_revision": citation["revision"], "remedy_id": remedy["id"], "notes": notes,
                })
            self.update(citation, {**common, "expected_revision": citation["revision"]})
            self.event(citation, "remedy_recorded", actor, {"notes": notes, "completed_remedies": [row["id"] for row in required]})
            if params.get("resolve_after", True):
                return self.resolve({**common, "expected_revision": citation["revision"], "notes": notes})
            return self.details(citation, "Remedy recorded; the citation remains open.")
        if operation == "View citation":
            return self.get(common)
        if operation == "Activity history":
            return self.history(common)
        if operation == "Resolve citation":
            return self.resolve({**common, "notes": notes, "override_reason": reason})
        if operation == "Reopen citation":
            return self.reopen({**common, "reason": reason})
        if operation == "Void citation":
            return self.void({**common, "reason": reason})
        if operation == "Delete citation":
            if not reason.strip():
                raise DomainError("A deletion requires a reason.")
            if citation["status"] == "void":
                self.update(citation, common)
            result = self.void({**common, "reason": reason}) if citation["status"] != "void" else self.details(citation, "Citation deleted.")
            citation["deleted_at"] = self.at
            for attachment in self.state["attachments"]:
                if attachment["citation_id"] == citation["id"]:
                    attachment["deleted_at"] = self.at
            self.event(citation, "deleted", actor, {"reason": reason})
            return {**result, "message": "Citation deleted with a durable tombstone; history and evidence are retained."}
        if operation == "Edit incident and deadline":
            return self.edit({**common, "description": params.get("description", ""),
                              "resolution_duration_seconds": params.get("resolution_duration_seconds", 3600)})
        if operation == "Correct first offense":
            return self.edit({**common, "first_offense": params.get("first_offense", True), "correction_reason": reason})
        if operation == "Add remedy":
            description = params.get("description", "").strip()
            if not description or len(description) > 500:
                raise DomainError("Enter a remedy description of 1-500 characters.")
            return self.add_remedy({**common, "description": description, "required": params.get("required", True)})
        if operation in {"Edit remedy", "Complete one remedy"}:
            name = params.get("remedy_match", "").strip().casefold()
            candidates = [row for row in self.remedies(citation) if not row["completed"]
                          and (not name or row["description"].strip().casefold() == name)]
            if len(candidates) != 1:
                raise DomainError("Enter the exact description of the intended incomplete remedy; no remedy ID is needed.")
            if operation == "Complete one remedy":
                return self.complete_remedy({**common, "remedy_id": candidates[0]["id"], "notes": notes})
            description = params.get("description", "").strip()
            if not description or len(description) > 500:
                raise DomainError("Enter an updated remedy description of 1-500 characters.")
            return self.edit_remedy({**common, "remedy_id": candidates[0]["id"], "description": description,
                                     "required": params.get("required", True)})
        raise DomainError("Unknown management operation.")

    def get(self, params: dict) -> dict:
        return self.details(self.repo.citation(params["citation"]), "Citation retrieved.")

    def details(self, citation: dict, message: str) -> dict:
        summary = self.summary(citation)
        remedies = [row.copy() for row in self.remedies(citation)]
        zone = party_zone(self.state["settings"]["party_timezone"])
        due = display_time(citation["due_at"], zone) if citation["due_at"] else "No deadline"
        violation = self.repo.find("violation_types", citation["violation_type_id"])
        lines = [
            "OFFICIAL HALLOWEEN VIOLATION NOTICE",
            "Fictional party fun only. Not a legal notice.",
            f"Citation: {citation['citation_number']}", f"Offender: {summary['offender']}",
            f"Violation: {summary['violation']}", f"Severity: {violation['severity']}/5",
            f"Issued: {display_time(citation['issued_at'], zone)} ({zone.key})",
            f"First-time offense: {'Yes' if citation['first_offense'] else 'No'}",
            f"Incident: {citation['description']}", f"Deadline: {due}",
            f"Status: {citation['status']}{' (OVERDUE)' if summary['overdue'] else ''}",
            "Required remedies:",
            *[f"[{'x' if row['completed'] else ' '}] {row['description']} ({'required' if row['required'] else 'optional'})" for row in remedies],
            f"Organizer signature: {citation['issued_by'] or '(unsigned)'}",
            f"Private evidence: {sum(row['citation_id'] == citation['id'] and row['deleted_at'] is None for row in self.state['attachments'])} file(s).",
        ]
        return {
            "message": message, "record": {**summary, "remedies": remedies, "elapsed_resolution_seconds": (
                max(0, int((timestamp(citation["resolved_at"]) - timestamp(citation["issued_at"])).total_seconds()))
                if citation["resolved_at"] else None
            )},
            "rows": remedies, "receipt": "\n".join(lines),
            "files": [
                {"name": row["storage_key"], "label": row["original_filename"]}
                for row in self.state["attachments"] if row["citation_id"] == citation["id"] and row["deleted_at"] is None
            ],
        }

    def edit(self, params: dict) -> dict:
        citation = self.editable(params)
        if not any(key in params for key in ("description", "resolution_duration_seconds", "first_offense")):
            raise DomainError("Supply a description, deadline duration, or first-offense correction.")
        if "first_offense" in params and not params.get("correction_reason", "").strip():
            raise DomainError("A first-offense correction requires a documented reason.")
        self.update(citation, params)
        changes = {}
        for key in ("description", "resolution_duration_seconds", "first_offense"):
            if key in params:
                changes[key] = {"before": citation[key], "after": params[key]}
                citation[key] = params[key]
        if "resolution_duration_seconds" in params:
            citation["due_at"] = stamp(timestamp(citation["issued_at"]) + timedelta(seconds=params["resolution_duration_seconds"]))
        self.event(citation, "edited", params.get("actor", ""), {"changes": changes, "correction_reason": params.get("correction_reason", "")})
        return self.details(citation, "Citation updated.")

    def add_remedy(self, params: dict) -> dict:
        citation = self.editable(params)
        self.update(citation, params)
        remedy = self.new_remedy(citation, params["description"], params.get("required", True))
        self.event(citation, "remedy_added", params.get("actor", ""), {"remedy_id": remedy["id"]})
        return self.details(citation, "Remedy added.")

    def remedy_for(self, citation: dict, identifier: str) -> dict:
        for row in self.remedies(citation):
            if row["id"] == identifier:
                return row
        raise DomainError("Remedy not found on this citation.")

    def edit_remedy(self, params: dict) -> dict:
        citation = self.editable(params)
        remedy = self.remedy_for(citation, params["remedy_id"])
        if remedy["completed"]:
            raise DomainError("A completed remedy is historical and cannot be edited.")
        if "description" not in params and "required" not in params:
            raise DomainError("Supply remedy description or required flag.")
        self.update(citation, params)
        before = {"description": remedy["description"], "required": remedy["required"]}
        for key in before:
            if key in params:
                remedy[key] = params[key]
        remedy["updated_at"] = self.at
        self.event(citation, "remedy_edited", params.get("actor", ""), {"remedy_id": remedy["id"], "before": before})
        return self.details(citation, "Remedy updated.")

    def complete_remedy(self, params: dict) -> dict:
        citation = self.editable(params)
        remedy = self.remedy_for(citation, params["remedy_id"])
        if remedy["completed"]:
            raise DomainError("Remedy is already complete; retrieve the current revision.")
        self.update(citation, params)
        remedy.update(completed=True, completed_at=self.at, completed_by=params.get("actor") or None,
                      completion_notes=params.get("notes", ""), updated_at=self.at)
        self.event(citation, "remedy_completed", params.get("actor", ""), {"remedy_id": remedy["id"]})
        return self.details(citation, "Remedy completed. Your spooky cooperation is appreciated.")

    def resolve(self, params: dict) -> dict:
        citation = self.editable(params)
        incomplete = [row["id"] for row in self.remedies(citation) if row["required"] and not row["completed"]]
        if incomplete and not params.get("override_reason", "").strip():
            raise DomainError("Complete required remedies or supply an explicit override reason.")
        self.update(citation, params)
        citation.update(status="resolved", resolved_at=self.at, resolved_by=params.get("actor") or None,
                        resolution_notes=params.get("notes", ""))
        self.event(citation, "resolved", params.get("actor", ""), {
            "override_reason": params.get("override_reason", ""), "incomplete_remedies": incomplete,
            "notes": params.get("notes", ""),
            "elapsed_resolution_seconds": max(0, int((self.now - timestamp(citation["issued_at"])).total_seconds())),
        })
        return self.details(citation, "Citation resolved. The spirits are appeased.")

    def reopen(self, params: dict) -> dict:
        citation = self.repo.citation(params["citation"])
        if citation["status"] != "resolved":
            raise DomainError("Only a resolved citation can be reopened.")
        self.update(citation, params)
        prior = {"resolved_at": citation["resolved_at"], "resolved_by": citation["resolved_by"],
                 "resolution_notes": citation["resolution_notes"], "reason": params["reason"]}
        citation.update(status="open", resolved_at=None, resolved_by=None, resolution_notes=None)
        self.event(citation, "reopened", params.get("actor", ""), prior)
        return self.details(citation, "Citation reopened; previous resolution remains in history.")

    def void(self, params: dict) -> dict:
        citation = self.repo.citation(params["citation"])
        if citation["status"] == "void":
            raise DomainError("Citation is already void.")
        self.update(citation, params)
        previous = {key: citation[key] for key in ("status", "resolved_at", "resolved_by", "resolution_notes")}
        citation.update(status="void", resolved_at=None, resolved_by=None, resolution_notes=None)
        self.event(citation, "voided", params.get("actor", ""), {"reason": params["reason"], "previous": previous})
        return self.details(citation, "Citation voided; history and remedies retained.")

    def history(self, params: dict) -> dict:
        citation = self.repo.citation(params["citation"])
        rows = [row.copy() for row in self.state["citation_events"] if row["citation_id"] == citation["id"]]
        return {"message": f"{len(rows)} append-only activity events.", "rows": rows, "record": {"revision": citation["revision"]}}

    def dashboard(self, params: dict) -> dict:
        counts = {status: sum(row["status"] == status for row in self.state["citations"]) for status in ("open", "resolved", "void")}
        counts["overdue"] = sum(self.is_overdue(row) for row in self.state["citations"])
        due_soon = sum(
            row["status"] == "open" and row["due_at"] is not None
            and self.now <= timestamp(row["due_at"]) <= self.now + timedelta(seconds=self.state["settings"]["due_soon_seconds"])
            for row in self.state["citations"]
        )
        counts["due_soon"] = due_soon
        counts["incomplete_required_remedies"] = sum(
            row["required"] and not row["completed"]
            for citation in self.state["citations"] if citation["status"] == "open" for row in self.remedies(citation)
        )
        from citations.sync import Synchronizer
        status = Synchronizer(self.repo.database).status()
        return {"message": "Offline SQLite compliance dashboard. Deadlines are derived from persisted records.",
                "record": {**counts, **self.state["settings"], "synchronization": status},
                "rows": [{"name": key, "value": value} for key, value in counts.items()]}

    def party_settings(self, params: dict) -> dict:
        if "party_timezone" in params:
            party_zone(params["party_timezone"])
        for key in ("party_timezone", "due_soon_seconds"):
            if key in params:
                self.state["settings"][key] = params[key]
                self.repo.touch()
        return {"message": "Party settings. Stored times remain UTC.", "record": self.state["settings"].copy()}

    def export(self, params: dict) -> dict:
        return {"message": "Private JSON backup. Contains optional contact details and organizer history; do not publish.",
                "export_json": self.repo.encoded()}
