"""Bounded local read models without domain-table snapshots."""
from __future__ import annotations

from citations.validation import DomainError, stamp, timestamp, utcnow


def list_citations(database, params: dict, now=None) -> dict:
    now = now or utcnow()
    sql = database.connection
    sql.create_function("casefold", 1, lambda value: str(value or "").casefold(), deterministic=True)
    sql.create_function("utc_stamp", 1, lambda value: stamp(timestamp(value)) if value else None, deterministic=True)
    where, values = ["c.deleted_at IS NULL"], []
    status = params.get("status", "all")
    if status != "all":
        where.append("c.status=?")
        values.append(status)
    query = params.get("query", "").casefold()
    if query:
        where.append("instr(casefold(p.display_name||' '||v.title||' '||c.citation_number||' '||coalesce(c.description,'')),?)>0")
        values.append(query)
    after = timestamp(params["issued_after"]) if params.get("issued_after") else None
    before = timestamp(params["issued_before"]) if params.get("issued_before") else None
    if after and before and after > before:
        raise DomainError("Start time must not be after end time.")
    for boundary, operator in ((after, ">="), (before, "<=")):
        if boundary:
            where.append(f"utc_stamp(c.issued_at){operator}?")
            values.append(stamp(boundary))
    if params.get("offense", "all") != "all":
        where.append("c.first_offense=?")
        values.append(params["offense"] == "first")
    if params.get("overdue_only"):
        where.append("c.status='open' AND utc_stamp(c.due_at)<?")
        values.append(stamp(now))
    source = "FROM citations c JOIN persons p ON p.id=c.person_id JOIN violation_types v ON v.id=c.violation_type_id WHERE " + " AND ".join(where)
    offset, limit = params.get("offset", 0), params.get("limit", 50)
    sql.execute("BEGIN")
    try:
        ranks = {
            row[0]: idx
            for idx, row in enumerate(
                sql.execute("SELECT id FROM citations WHERE deleted_at IS NULL ORDER BY issued_at ASC, id ASC").fetchall(),
                1,
            )
        }
        total = sql.execute("SELECT count(*) " + source, values).fetchone()[0]
        selected = sql.execute(
            "SELECT c.id,c.citation_number,c.description,c.status,c.first_offense,c.issued_at,c.issued_by,c.due_at,"
            "c.resolved_at,c.resolved_by,c.resolution_notes,c.revision,p.display_name AS offender,v.title AS violation " + source
            + " ORDER BY c.issued_at DESC,c.id DESC LIMIT ? OFFSET ?", (*values, limit, offset),
        ).fetchall()
        rows = [database.decode(dict(row)) for row in selected]
        by_id = {row["id"]: row for row in rows}
        for row in rows:
            row["display_code"] = f"#{ranks.get(row['id'], 0)}"
            row["overdue"] = row["status"] == "open" and bool(row["due_at"]) and timestamp(row["due_at"]) < now
            row["remedies"] = []
        identifiers = tuple(by_id)
        for start in range(0, len(identifiers), 500):
            batch = identifiers[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            for remedy in sql.execute(
                f"SELECT citation_id,description,required,completed,completed_at,completed_by,completion_notes FROM remedies WHERE deleted_at IS NULL AND citation_id IN ({placeholders}) ORDER BY position",
                batch,
            ):
                by_id[remedy["citation_id"]]["remedies"].append({
                    "description": remedy["description"],
                    "required": bool(remedy["required"]),
                    "completed": bool(remedy["completed"]),
                    "completed_at": remedy["completed_at"],
                    "completed_by": remedy["completed_by"],
                    "completion_notes": remedy["completion_notes"],
                })
        return {
            "message": f"{total} matching citations.", "rows": rows,
            "record": {"total": total, "offset": offset, "limit": limit, "generation": database.generation()},
        }
    finally:
        sql.rollback()
