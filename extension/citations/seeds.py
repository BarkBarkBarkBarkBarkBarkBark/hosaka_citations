"""Idempotent defaults, deliberately separate from migrations."""
from citations.models import initial_state


def seed_defaults(database) -> int:
    count = 0
    for row in initial_state()["violation_types"]:
        exists = database.connection.execute(
            "SELECT 1 FROM violation_types WHERE id=? OR code=?", (row["id"], row["code"]),
        ).fetchone()
        if exists is None:
            database.write("violation_types", {**row, "deleted_at": None}, enqueue=True)
            count += 1
    return count
