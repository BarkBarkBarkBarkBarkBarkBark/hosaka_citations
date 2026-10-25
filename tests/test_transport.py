import base64
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from citations.cli import OperatorContext
from citations.config import load_config
from citations.supabase import Supabase, SyncError
from citations.validation import DomainError


def token(role="authenticated"):
    claims = base64.urlsafe_b64encode(json.dumps({"role": role, "sub": str(uuid4())}).encode()).decode().rstrip("=")
    return "e30." + claims + ".test-signature"


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.context = OperatorContext(Path(self.directory.name), True)
        self.config = {"supabase_url": "https://project.supabase.co", "publishable_key": "sb_publishable_example",
                       "workspace_id": str(uuid4())}

    def test_auth_refresh_and_rpc_headers_are_private_and_use_verified_default_tls(self):
        self.context.write_data("supabase-auth.json", json.dumps({"refresh_token": "private-refresh", "expires_at": 0}))
        access = token()
        requests = []
        def open_url(request, **kwargs):
            requests.append(request)
            self.assertNotIn("context", kwargs)  # urllib's certificate-verifying default is retained.
            if "/auth/v1/token" in request.full_url:
                self.assertEqual(json.loads(request.data)["refresh_token"], "private-refresh")
                return io.BytesIO(json.dumps({"access_token": access, "refresh_token": "rotated-private", "expires_in": 3600}).encode())
            self.assertEqual(request.get_header("Authorization"), "Bearer " + access)
            self.assertEqual(request.get_header("Content-profile"), "hosaka_citations")
            self.assertEqual(request.get_header("Accept-profile"), "hosaka_citations")
            return io.BytesIO(b'{"changes":[],"cursor":0,"has_more":false}')
        with patch("urllib.request.urlopen", side_effect=open_url):
            client = Supabase(self.context, self.config)
            self.assertEqual(client.pull(0)["cursor"], 0)
        self.assertEqual(len(requests), 2)
        saved = json.loads(self.context.read_data("supabase-auth.json"))
        self.assertEqual(saved["refresh_token"], "rotated-private")
        self.assertEqual(Path(self.context.data_path("supabase-auth.json")).stat().st_mode & 0o777, 0o600)

    def test_http_error_body_and_tokens_are_not_disclosed(self):
        self.context.write_data("supabase-auth.json", json.dumps({"access_token": token(), "refresh_token": "private", "expires_at": time.time()+1000}))
        error = HTTPError("https://project.supabase.co", 401, "secret body", {}, io.BytesIO(b"private-credential"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(SyncError, "HTTP 401") as caught:
                Supabase(self.context, self.config).pull(0)
            self.assertNotIn("private-credential", str(caught.exception))

    def test_admin_keys_user_tokens_and_insecure_urls_are_rejected(self):
        for key in ("sb_secret_do-not-use", token("service_role")):
            self.context.write_data("citations-config.json", json.dumps({**self.config, "publishable_key": key}))
            with self.assertRaises(DomainError):
                load_config(self.context)
        self.context.write_data("citations-config.json", json.dumps({**self.config, "supabase_url": "http://insecure.test"}))
        with self.assertRaises(DomainError):
            load_config(self.context)
        self.context.write_data("supabase-auth.json", json.dumps({"access_token": token("service_role"), "expires_at": time.time()+1000}))
        with self.assertRaises(SyncError):
            Supabase(self.context, self.config).pull(0)

    def test_upload_lost_ack_checks_immutable_private_object(self):
        client = Supabase(self.context, self.config)
        attachment = {"storage_key": "object.pdf", "mime_type": "application/pdf"}
        with patch.object(client, "request", side_effect=[SyncError("Supabase request failed (HTTP 409)."), b"same"]) as request:
            client.upload(attachment, b"same")
            self.assertIn("/storage/v1/object/authenticated/halloween-citations-evidence/", request.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
