import io
import os
import tempfile
import unittest

from fastapi import HTTPException, UploadFile

os.environ.setdefault("SECRET_KEY", "test_secret_key_123456789012345678901234567890")
os.environ.setdefault("ENCRYPTION_KEY", "ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k=")

from app.core.file_uploads import safe_original_filename, save_upload_limited, validate_upload_metadata


class FileUploadSecurityTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_dangerous_extension(self):
        upload = UploadFile(filename="payload.exe", file=io.BytesIO(b"x"))

        with self.assertRaises(HTTPException) as raised:
            validate_upload_metadata(upload, allowed_exts={"jpg", "png"})

        self.assertEqual(raised.exception.status_code, 400)

    def test_sanitizes_path_like_filename(self):
        self.assertEqual(safe_original_filename("../../secret.png"), "secret.png")
        self.assertEqual(safe_original_filename("bad name;.png"), "bad_name_.png")

    async def test_rejects_oversized_file_and_removes_partial_file(self):
        upload = UploadFile(filename="report.pdf", file=io.BytesIO(b"x" * 8))

        with tempfile.TemporaryDirectory() as tmp:
            destination = os.path.join(tmp, "report.pdf")

            with self.assertRaises(HTTPException) as raised:
                await save_upload_limited(upload, destination, max_bytes=4)

            self.assertEqual(raised.exception.status_code, 413)
            self.assertFalse(os.path.exists(destination))


if __name__ == "__main__":
    unittest.main()
