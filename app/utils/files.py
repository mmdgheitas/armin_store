"""ابزارهای فایل: اعتبارسنجی نوع واقعی، نام امن، مسیر امن، حجم خوانا."""
from __future__ import annotations

import os
import re
import secrets
import unicodedata
from pathlib import Path

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
VIDEO_EXTENSIONS = {"mp4", "webm", "mov"}
DOCUMENT_EXTENSIONS = {"pdf", "zip", "txt", "doc", "docx", "xls", "xlsx"}

_MAGIC_SIGNATURES = [
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
    (b"%PDF-", "application/pdf", "pdf"),
    (b"PK\x03\x04", "application/zip", "zip"),
]

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.\-]")


def human_size(num_bytes: int | None) -> str:
    size = float(num_bytes or 0)
    for unit in ["بایت", "کیلوبایت", "مگابایت", "گیگابایت"]:
        if size < 1024 or unit == "گیگابایت":
            if unit == "بایت":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} گیگابایت"


def unsafe_filename(name: str | None) -> bool:
    if not name:
        return True
    value = name.strip()
    return (
        "/" in value
        or "\\" in value
        or "\x00" in value
        or value.startswith(".")
        or ".." in value
    )


def unsafe_relative_path(value: str | None) -> bool:
    """بررسی مسیر نسبی ذخیره‌سازی (اجازه پوشه‌بندی، رد Traversal و مسیر مطلق)."""
    if not value:
        return True
    text = str(value).strip().replace("\\", "/")
    if not text or text.startswith("/") or "\x00" in text:
        return True
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return True
    return False


def safe_extension(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    ext = filename.rsplit(".", 1)[-1].lower().strip()
    return ext if re.fullmatch(r"[a-z0-9]{1,8}", ext) else ""


def normalize_original_name(filename: str | None) -> str:
    """نام اصلی فایل برای نمایش (نه برای ذخیره روی دیسک)."""
    if not filename:
        return ""
    base = os.path.basename(filename.replace("\\", "/"))
    base = unicodedata.normalize("NFKC", base)
    base = re.sub(r"[\x00-\x1f<>:\"|?*]", "", base).strip()
    return base[:180] or "file"


def unique_stored_name(filename: str | None) -> str:
    """نام تصادفی امن با پسوند مجاز."""
    ext = safe_extension(filename)
    token = secrets.token_hex(12)
    return f"{token}.{ext}" if ext else token


def sniff_mime(head: bytes) -> tuple[str | None, str | None]:
    """تشخیص نوع واقعی فایل از امضای باینری (بدون وابستگی خارجی)."""
    if not head:
        return None, None
    for signature, mime, ext in _MAGIC_SIGNATURES:
        if head.startswith(signature):
            return mime, ext
    # WEBP: RIFF....WEBP
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", "webp"
    # MP4 / MOV: ....ftyp
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in {b"qt  ", b"moov"}:
            return "video/quicktime", "mov"
        return "video/mp4", "mp4"
    # WebM / Matroska
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm", "webm"
    return None, None


class FileValidationError(ValueError):
    """خطای اعتبارسنجی فایل با پیام فارسی قابل نمایش."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def validate_upload(
    file_storage,
    kind: str = "image",
    max_bytes: int = 5 * 1024 * 1024,
) -> tuple[str, str]:
    """
    اعتبارسنجی فایل آپلودی بر اساس پسوند، نوع واقعی و حجم.
    بازگشتی: (extension, mime)
    """
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise FileValidationError("فایلی انتخاب نشده است.")

    filename = file_storage.filename
    ext = safe_extension(filename)
    if not ext:
        raise FileValidationError("پسوند فایل نامعتبر است.")

    allowed = {
        "image": IMAGE_EXTENSIONS,
        "video": VIDEO_EXTENSIONS,
        "document": DOCUMENT_EXTENSIONS,
        "asset": IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | DOCUMENT_EXTENSIONS,
    }.get(kind, IMAGE_EXTENSIONS | VIDEO_EXTENSIONS)

    if ext not in allowed:
        allowed_text = "، ".join(sorted(allowed))
        raise FileValidationError(f"فرمت فایل مجاز نیست. فرمت‌های مجاز: {allowed_text}")

    # بررسی حجم واقعی بدون اعتماد به Content-Length کلاینت
    stream = file_storage.stream
    position = stream.tell() if hasattr(stream, "tell") else 0
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    if size <= 0:
        raise FileValidationError("فایل خالی است.")
    if size > max_bytes:
        raise FileValidationError(
            f"حجم فایل بیش از حد مجاز ({human_size(max_bytes)}) است. "
            "لطفاً فایل کوچک‌تری انتخاب کنید یا از لینک خارجی استفاده کنید."
        )

    head = stream.read(32)
    stream.seek(position)
    mime, detected_ext = sniff_mime(head)
    if not mime:
        raise FileValidationError("نوع فایل قابل تشخیص نیست یا پشتیبانی نمی‌شود.")

    if kind == "image" and not mime.startswith("image/"):
        raise FileValidationError("فایل انتخاب‌شده تصویر معتبر نیست.")
    if kind == "video" and not mime.startswith("video/"):
        raise FileValidationError("فایل انتخاب‌شده ویدئوی معتبر نیست.")
    if kind == "document" and mime not in {
        "application/pdf",
        "application/zip",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        if ext not in {"doc", "docx", "xls", "xlsx", "txt"}:
            raise FileValidationError("نوع فایل با پسوند آن هم‌خوانی ندارد.")

    # هم‌خوانی پسوند و نوع واقعی (برای تصاویر/ویدئوها سخت‌گیرانه)
    if kind in {"image", "video"}:
        normalized = {"jpeg": "jpg", "quicktime": "mov", "mpeg": "mp4"}.get(detected_ext, detected_ext)
        if normalized and normalized != ext and not (ext == "jpeg" and normalized == "jpg"):
            if not (ext == "mov" and normalized == "mp4"):
                raise FileValidationError("نوع واقعی فایل با پسوند آن هم‌خوانی ندارد.")

    if mime in {"video/mp4", "video/quicktime", "video/webm"}:
        mp4_hint = head[0:11]
        if b"<html" in mp4_hint.lower() or b"<?xml" in mp4_hint.lower():
            raise FileValidationError("فایل انتخاب‌شده ویدئوی معتبر نیست.")

    return ext, mime


def is_public_path_allowed(path: Path, root: Path) -> bool:
    """جلوگیری از Path Traversal برای فایل‌های عمومی."""
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
    except (OSError, RuntimeError):
        return False
    return root_resolved == resolved or root_resolved in resolved.parents


def read_head(path: Path, size: int = 64) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read(size)
    except OSError:
        return b""


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
