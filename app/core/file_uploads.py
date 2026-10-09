import mimetypes
import os
import re
import struct
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
DOCUMENT_EXTS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx"}
MESSAGE_ATTACHMENT_EXTS = IMAGE_EXTS | DOCUMENT_EXTS | {"txt", "csv"}
DANGEROUS_EXTS = {
    "bat",
    "cmd",
    "com",
    "exe",
    "html",
    "js",
    "msi",
    "php",
    "ps1",
    "sh",
    "svg",
}

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_MESSAGE_ATTACHMENT_BYTES = 20 * 1024 * 1024
MIN_AVATAR_SIDE = 32

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_original_filename(filename: str | None, fallback: str = "file") -> str:
    name = os.path.basename((filename or "").strip())
    if not name:
        name = fallback
    return _SAFE_FILENAME_RE.sub("_", name)[:180] or fallback


def file_extension(filename: str | None) -> str:
    name = safe_original_filename(filename)
    suffix = Path(name).suffix.lower().lstrip(".")
    return suffix


def safe_storage_path(base_dir: str, relative_path: str | None) -> str:
    relative = (relative_path or "").strip()
    if not relative:
        raise HTTPException(status_code=404, detail="File path is missing")
    if os.path.isabs(relative):
        raise HTTPException(status_code=400, detail="Invalid file path")

    base = os.path.realpath(base_dir)
    candidate = os.path.realpath(os.path.join(base, relative))
    if os.path.commonpath([base, candidate]) != base:
        raise HTTPException(status_code=400, detail="Invalid file path")
    return candidate


def validate_upload_metadata(
    file: UploadFile,
    *,
    allowed_exts: Iterable[str],
    allowed_mime_prefixes: Iterable[str] = (),
    allowed_mime_types: Iterable[str] = (),
) -> tuple[str, str]:
    filename = safe_original_filename(file.filename)
    ext = file_extension(filename)
    allowed = {item.lower().lstrip(".") for item in allowed_exts}
    if not ext or ext in DANGEROUS_EXTS or ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(sorted(allowed))}",
        )

    content_type = (file.content_type or mimetypes.guess_type(filename)[0] or "").lower()
    if allowed_mime_prefixes or allowed_mime_types:
        mime_ok = content_type in {m.lower() for m in allowed_mime_types}
        mime_ok = mime_ok or any(
            content_type.startswith(prefix.lower()) for prefix in allowed_mime_prefixes
        )
        if content_type and not mime_ok:
            raise HTTPException(status_code=400, detail="Unsupported media type")

    return filename, ext


async def save_upload_limited(
    file: UploadFile,
    destination: str,
    *,
    max_bytes: int,
) -> int:
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    written = 0
    try:
        with open(destination, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail="File is too large")
                out.write(chunk)
    except Exception:
        if os.path.exists(destination):
            os.remove(destination)
        raise
    finally:
        await file.close()
    return written


def image_dimensions(path: str) -> tuple[int, int] | None:
    """Read PNG/JPEG dimensions without decoding untrusted image pixels."""
    try:
        with open(path, "rb") as source:
            header = source.read(24)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                return struct.unpack(">II", header[16:24])

            if not header.startswith(b"\xff\xd8"):
                return None
            source.seek(2)
            while True:
                marker_start = source.read(1)
                if not marker_start:
                    return None
                if marker_start != b"\xff":
                    continue
                marker = source.read(1)
                while marker == b"\xff":
                    marker = source.read(1)
                if not marker or marker in {b"\xd8", b"\xd9"}:
                    continue

                length_bytes = source.read(2)
                if len(length_bytes) != 2:
                    return None
                segment_length = struct.unpack(">H", length_bytes)[0]
                if segment_length < 2:
                    return None
                if marker[0] in {
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                }:
                    dimensions = source.read(5)
                    if len(dimensions) != 5:
                        return None
                    height, width = struct.unpack(">HH", dimensions[1:5])
                    return width, height
                source.seek(segment_length - 2, os.SEEK_CUR)
    except (OSError, struct.error):
        return None


def validate_avatar_dimensions(path: str) -> tuple[int, int]:
    dimensions = image_dimensions(path)
    if dimensions is None:
        raise HTTPException(status_code=400, detail="Invalid image file")
    width, height = dimensions
    if width < MIN_AVATAR_SIDE or height < MIN_AVATAR_SIDE:
        raise HTTPException(
            status_code=400,
            detail=f"Profile photo must be at least {MIN_AVATAR_SIDE}x{MIN_AVATAR_SIDE} pixels",
        )
    return dimensions
