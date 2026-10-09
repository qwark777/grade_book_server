import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test_secret_key_123456789012345678901234567890")
os.environ.setdefault("ENCRYPTION_KEY", "ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k=")

from fastapi.testclient import TestClient

from app.api import health
from app.main import app


class FakeCursor:
    def __init__(self, row):
        self.row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query):
        self.query = query

    async def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.row = row
        self.closed = False

    def cursor(self):
        return FakeCursor(self.row)

    def close(self):
        self.closed = True


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_health_accepts_tuple_database_rows(self):
        async def fake_db():
            return FakeConnection((1,))

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_settings = SimpleNamespace(
                PROJECT_NAME="Grade Book API",
                PROFILE_PHOTOS_DIR=tmpdir,
                ACHIEVEMENTS_DIR=tmpdir,
                HOMEWORK_SUBMISSIONS_DIR=tmpdir,
                HOMEWORK_ATTACHMENTS_DIR=tmpdir,
                MESSAGE_ATTACHMENTS_DIR=tmpdir,
            )
            with patch.object(health, "get_db_connection", fake_db), patch.object(health, "settings", fake_settings):
                response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertTrue(response.json()["checks"]["database"])
        self.assertTrue(response.json()["checks"]["storage"])

    def test_health_returns_503_when_storage_is_unavailable(self):
        async def fake_db():
            return FakeConnection({"ok": 1})

        missing_path = "/tmp/gradebook-health-missing-path"
        if os.path.exists(missing_path):
            os.rmdir(missing_path)

        fake_settings = SimpleNamespace(
            PROJECT_NAME="Grade Book API",
            PROFILE_PHOTOS_DIR=missing_path,
            ACHIEVEMENTS_DIR=missing_path,
            HOMEWORK_SUBMISSIONS_DIR=missing_path,
            HOMEWORK_ATTACHMENTS_DIR=missing_path,
            MESSAGE_ATTACHMENTS_DIR=missing_path,
        )
        with patch.object(health, "get_db_connection", fake_db), patch.object(health, "settings", fake_settings):
            response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertFalse(response.json()["checks"]["storage"])


if __name__ == "__main__":
    unittest.main()
