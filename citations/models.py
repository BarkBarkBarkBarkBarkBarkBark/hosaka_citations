"""Domain constants and deterministic seed templates."""
from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

TEMPLATES = (
    ("no-costume", "Attending without a costume", "Put on an improvised costume."),
    ("low-spirit", "Insufficient Halloween spirit", "Deliver a spirited spooky cheer."),
    ("too-serious", "Excessive seriousness", "Perform a silly dance."),
    ("no-festivities", "Failure to participate in festivities", "Join one party activity."),
    ("normal-behavior", "Suspiciously normal behavior", "Give a dramatic monster impression."),
    ("candy-hoarding", "Unlicensed candy hoarding", "Offer a treat to a fellow guest."),
    ("ghosting", "Ghosting the dance floor", "Haunt the dance floor for one song."),
    ("bad-pun", "Aggravated pumpkin pun", "Deliver a dramatic apology."),
)


def initial_state() -> dict:
    created = "2026-10-01T00:00:00.000000Z"
    return {
        "schema_version": 1,
        "settings": {"party_timezone": "UTC", "due_soon_seconds": 900},
        "persons": [], "citations": [], "remedies": [], "citation_events": [],
        "violation_types": [
            {
                "id": str(uuid5(NAMESPACE_URL, f"hosaka:halloween-citations:{code}")),
                "code": code, "title": title, "description": "",
                "default_remedy": remedy, "default_resolution_seconds": 3600,
                "severity": 1, "active": True, "created_at": created, "updated_at": created,
            }
            for code, title, remedy in TEMPLATES
        ],
    }
