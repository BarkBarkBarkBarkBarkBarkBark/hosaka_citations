"""Private extension-owned configuration; no provider or administrative secrets."""
from __future__ import annotations

import json
from urllib.parse import urlsplit
from uuid import UUID

from citations.validation import DomainError

DEFAULTS = {
    "database": "citations.sqlite3", "sync_enabled": False, "supabase_url": "",
    "publishable_key": "", "workspace_id": "", "sync_interval_seconds": 60,
}


def load_config(context, *, validate_remote: bool = True) -> dict:
    raw = context.read_data("citations-config.json")
    try:
        config = json.loads(raw) if raw is not None else {}
    except ValueError as exc:
        raise DomainError("Invalid citations-config.json; restore or repair it.") from exc
    if not isinstance(config, dict) or set(config) - set(DEFAULTS):
        raise DomainError("Unknown citation configuration fields.")
    result = {**DEFAULTS, **config}
    if type(result["sync_enabled"]) is not bool or type(result["sync_interval_seconds"]) is not int:
        raise DomainError("Sync enablement must be boolean and interval must be integer seconds.")
    if not 15 <= result["sync_interval_seconds"] <= 86400:
        raise DomainError("Sync interval must be 15-86400 seconds.")
    for name in ("database", "supabase_url", "publishable_key", "workspace_id"):
        if not isinstance(result[name], str):
            raise DomainError(f"Configuration {name} must be text.")
    context.data_path(result["database"])
    result["supabase_url"] = result["supabase_url"].rstrip("/")
    if validate_remote:
        validate_remote_config(result)
    return result


def validate_remote_config(result: dict) -> None:
    if result["supabase_url"]:
        url = urlsplit(result["supabase_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise DomainError("Supabase URL must be a verified HTTPS origin without credentials or query parameters.")
        result["supabase_url"] = result["supabase_url"].rstrip("/")
    if result["workspace_id"]:
        try:
            if str(UUID(result["workspace_id"])) != result["workspace_id"]:
                raise ValueError
        except ValueError as exc:
            raise DomainError("Workspace ID must be a canonical UUID.") from exc
    key = result["publishable_key"]
    if key.startswith("sb_secret_"):
        raise DomainError("Never configure a Supabase secret/service-role key in an extension client.")
    if key.count(".") == 2:
        import base64
        try:
            claims = json.loads(base64.urlsafe_b64decode(key.split(".")[1] + "==="))
            if not isinstance(claims, dict):
                raise ValueError
            role = claims.get("role")
        except (ValueError, UnicodeError) as exc:
            raise DomainError("Invalid Supabase publishable/anon key.") from exc
        if role != "anon":
            raise DomainError("Only publishable/anon project keys are permitted; sign in as a workspace member.")
