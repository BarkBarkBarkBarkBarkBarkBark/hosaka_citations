"""Immutable, private evidence objects and independent durable file transfers."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from uuid import UUID

from citations.validation import DomainError

MAX_FILE = 10 * 1024 * 1024


def inspect(content: bytes) -> tuple[str, str]:
    if not 0 < len(content) <= MAX_FILE:
        raise DomainError("Evidence must be 1 byte to 10 MiB.")
    if content.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in content[-16:]:
        return "image/png", "png"
    if content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
        return "image/jpeg", "jpg"
    if len(content) >= 16 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", "webp"
    if content.startswith(b"%PDF-") and b"%%EOF" in content[-1024:]:
        return "application/pdf", "pdf"
    raise DomainError("Evidence content is not a supported PNG, JPEG, WebP or PDF.")


def attach(service, params: dict) -> dict:
    token = params["upload_id"]
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise DomainError("Choose an evidence file before attaching it.")
    identifier = str(UUID(token))
    selected, separator, revision = params["selection"].rpartition(":")
    if not separator or not revision.isdecimal():
        raise DomainError("Select a citation by attendee name before attaching evidence.")
    citation = service.repo.citation(selected)
    existing = next((row for row in service.state["attachments"] if row["id"] == identifier), None)
    if existing is not None:
        if existing["citation_id"] != citation["id"]:
            raise DomainError("That upload already belongs to another citation.")
        return service.details(citation, "Evidence was already attached.")
    if service.repo.version(citation["id"]) != int(revision) or citation["deleted_at"]:
        raise DomainError("Citation changed; select it again before adding evidence.")
    source = Path(service.repo.context.data_path("uploads/" + token))
    if source.is_symlink() or not source.is_file():
        raise DomainError("Uploaded evidence is unavailable; choose the file again.")
    with source.open("rb") as stream:
        content = stream.read(MAX_FILE + 1)
    mime, suffix = inspect(content)
    filename = params.get("filename", "").strip() or "Evidence"
    if Path(filename).name != filename or "\\" in filename or any(ord(char) < 32 for char in filename):
        raise DomainError("Evidence filenames must be plain names, not filesystem paths.")
    digest = hashlib.sha256(content).hexdigest()
    key = f"{token}.{digest}.{suffix}"
    target = Path(service.repo.context.data_path("files/" + key))
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise DomainError("Existing immutable evidence has a different checksum.")
    if not target.exists():
        with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    row = {
        "id": identifier, "citation_id": citation["id"], "uploaded_by": params.get("actor") or None,
        "original_filename": filename, "mime_type": mime, "size_bytes": len(content),
        "sha256": digest, "storage_key": key, "created_at": service.at, "deleted_at": None,
    }
    service.state["attachments"].append(row)
    service.update(citation, {"expected_revision": citation["revision"]})
    service.event(citation, "evidence_added", params.get("actor", ""), {"attachment_id": identifier, "sha256": digest})
    return service.details(citation, "Private evidence attached. Original image metadata is retained; review it before sharing.")
