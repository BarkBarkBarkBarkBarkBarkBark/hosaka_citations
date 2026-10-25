"""Authenticated Supabase REST transport. No administrative credentials."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from citations.config import validate_remote_config


# Exposed to PostgREST alongside other applications' schemas; selected per request.
SCHEMA = "hosaka_citations"


class SyncError(RuntimeError):
    pass


class Supabase:
    def __init__(self, context, config: dict, timeout: float = 2):
        context.require_network()
        if not all(config[key] for key in ("supabase_url", "publishable_key", "workspace_id")):
            raise SyncError("Configure the Supabase HTTPS origin, publishable key and workspace before syncing.")
        validate_remote_config(config)
        self.context, self.config, self.timeout = context, config, timeout
        raw = context.read_data("supabase-auth.json")
        try:
            self.auth = json.loads(raw) if raw is not None else {}
        except ValueError as exc:
            raise SyncError("Invalid private Supabase authentication file.") from exc
        if not isinstance(self.auth, dict):
            raise SyncError("Invalid private Supabase authentication file.")

    def request(self, path: str, payload=None, *, method="POST", binary: bytes | None = None, mime="application/json", authenticated=True):
        headers = {"apikey": self.config["publishable_key"], "Content-Type": mime}
        if path.startswith("/rest/v1/"):
            headers["Content-Profile"] = headers["Accept-Profile"] = SCHEMA
        if authenticated:
            self.ensure_auth()
            headers["Authorization"] = "Bearer " + self.auth["access_token"]
        data = binary if binary is not None else (json.dumps(payload).encode() if payload is not None else None)
        request = urllib.request.Request(self.config["supabase_url"] + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(10485761)
                if len(raw) > 10485760:
                    raise SyncError("Supabase response exceeds the permitted size.")
                if method == "GET":
                    return raw
                result = json.loads(raw or b"{}")
                if not isinstance(result, dict):
                    raise SyncError("Supabase returned an invalid response object.")
                return result
        except urllib.error.HTTPError as exc:
            # Response bodies may contain credentials or private records.
            raise SyncError(f"Supabase request failed (HTTP {exc.code}).") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SyncError("Supabase is unreachable; local records and pending work are retained.") from None
        except (ValueError, UnicodeError):
            raise SyncError("Supabase returned an invalid response.") from None

    def save_auth(self, response: dict):
        if not isinstance(response.get("access_token"), str) or not isinstance(response.get("refresh_token"), str):
            raise SyncError("Authentication response did not contain usable user tokens.")
        self.auth = {
            "access_token": response["access_token"], "refresh_token": response["refresh_token"],
            "expires_at": time.time() + int(response.get("expires_in", 3600)) - 30,
        }
        self.validate_user_token()
        self.context.write_data("supabase-auth.json", json.dumps(self.auth))

    def validate_user_token(self):
        import base64
        token = self.auth.get("access_token", "")
        try:
            claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==="))
            if not isinstance(claims, dict) or claims.get("role") != "authenticated":
                raise ValueError
        except (ValueError, IndexError, AttributeError, UnicodeError) as exc:
            raise SyncError("Use an authenticated Supabase user token, never a service-role/admin token.") from exc

    def login(self, email: str, password: str):
        self.save_auth(self.request("/auth/v1/token?grant_type=password", {"email": email, "password": password}, authenticated=False))

    def ensure_auth(self):
        expiry = self.auth.get("expires_at", 0)
        if not isinstance(expiry, (int, float)):
            raise SyncError("Invalid private token expiry; sign in again.")
        if self.auth.get("access_token") and expiry > time.time():
            self.validate_user_token()
            return
        if not isinstance(self.auth.get("refresh_token"), str):
            raise SyncError("Sign in with the extension CLI as an authenticated workspace member.")
        self.save_auth(self.request("/auth/v1/token?grant_type=refresh_token", {"refresh_token": self.auth["refresh_token"]}, authenticated=False))
        self.validate_user_token()

    def push(self, operation: dict):
        return self.request("/rest/v1/rpc/hc_push", {
            "p_workspace": self.config["workspace_id"], "p_operation": operation["operation_id"],
            "p_entity_type": operation["entity_type"], "p_entity_id": operation["entity_id"],
            "p_base_revision": operation["base_revision"], "p_payload": json.loads(operation["payload"]),
        })

    def pull(self, cursor: int, limit=100):
        return self.request("/rest/v1/rpc/hc_pull", {
            "p_workspace": self.config["workspace_id"], "p_after": cursor, "p_limit": limit,
        })

    def object_path(self, attachment: dict, authenticated=False) -> str:
        mode = "authenticated/" if authenticated else ""
        key = urllib.parse.quote(f"{self.config['workspace_id']}/{attachment['storage_key']}", safe="/")
        return f"/storage/v1/object/{mode}halloween-citations-evidence/{key}"

    def upload(self, attachment: dict, content: bytes):
        try:
            self.request(self.object_path(attachment), binary=content, mime=attachment["mime_type"])
        except SyncError as exc:
            if "HTTP 409" not in str(exc) and "HTTP 400" not in str(exc):
                raise
            # Lost upload acknowledgments must not overwrite immutable objects.
            if self.download(attachment) != content:
                raise SyncError("An existing evidence object does not match this attachment.") from None

    def download(self, attachment: dict) -> bytes:
        return self.request(self.object_path(attachment, True), method="GET")
