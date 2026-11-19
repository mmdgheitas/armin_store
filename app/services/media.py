from __future__ import annotations

import io
import os
import secrets
from pathlib import Path
from typing import Any

from flask import current_app
from PIL import Image, ImageOps
from werkzeug.datastructures import FileStorage

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_VIDEO_EXTS = {".mp4", ".webm"}


class MediaValidationError(Exception):
    pass


def _upload_root() -> Path:
    root = Path(current_app.config["UPLOAD_FOLDER"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_and_check_size(file_storage: FileStorage, max_mb: int) -> bytes:
    data = file_storage.read()
    max_bytes = max_mb * 1024 * 1024
    if not data:
        raise MediaValidationError("فایل انتخاب‌شده خالی است.")
    if len(data) > max_bytes:
        raise MediaValidationError(f"حجم فایل نباید بیشتر از {max_mb} مگابایت باشد.")
    return data


def _detect_image_magic(data: bytes) -> bool:
    if data.startswith(b"\xff\xd8\xff"):  # JPEG
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):  # PNG
        return True
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":  # WebP
        return True
    return False


def _detect_video_magic(data: bytes, ext: str) -> bool:
    if ext == ".mp4":
        return len(data) >= 12 and data[4:8] == b"ftyp"
    if ext == ".webm":
        return data.startswith(b"\x1a\x45\xdf\xa3")
    return False


def save_uploaded_image(
    file_storage: FileStorage,
    *,
    subdir: str = "products",
    max_dim: int = 1200,
    thumb_dim: int = 420,
) -> dict[str, Any]:
    if not file_storage or not file_storage.filename:
        raise MediaValidationError("لطفاً یک فایل تصویر انتخاب کنید.")
    ext = Path(file_storage.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise MediaValidationError("فرمت تصویر مجاز نیست (فقط JPG، PNG یا WebP).")

    max_mb = int(current_app.config.get("MAX_IMAGE_SIZE_MB", 5))
    raw = _read_and_check_size(file_storage, max_mb)
    if not _detect_image_magic(raw):
        raise MediaValidationError("ساختار فایل تصویر معتبر نیست.")

    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.verify()
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            img_main = img.copy()
            img_main.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            w, h = img_main.size

            img_thumb = img.copy()
            img_thumb.thumbnail((thumb_dim, thumb_dim), Image.Resampling.LANCZOS)
    except Exception as exc:
        raise MediaValidationError("فایل تصویر قابل پردازش نیست.") from exc

    rel_dir = Path(subdir)
    out_dir = _upload_root() / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    token = secrets.token_hex(12)
    has_alpha = img_main.mode == "RGBA"
    main_ext = ".png" if has_alpha else ".jpg"
    main_name = f"{token}{main_ext}"
    webp_name = f"{token}.webp"
    thumb_name = f"{token}_thumb.webp"

    main_path = out_dir / main_name
    webp_path = out_dir / webp_name
    thumb_path = out_dir / thumb_name

    if has_alpha:
        img_main.save(main_path, format="PNG", optimize=True)
    else:
        img_main.convert("RGB").save(main_path, format="JPEG", quality=86, optimize=True)

    img_main.save(webp_path, format="WEBP", quality=84, method=4)
    img_thumb.save(thumb_path, format="WEBP", quality=80, method=4)

    return {
        "file_path": (rel_dir / main_name).as_posix(),
        "webp_path": (rel_dir / webp_name).as_posix(),
        "thumb_path": (rel_dir / thumb_name).as_posix(),
        "width": w,
        "height": h,
    }


def save_uploaded_video(
    file_storage: FileStorage, *, subdir: str = "videos"
) -> str:
    if not file_storage or not file_storage.filename:
        raise MediaValidationError("لطفاً یک فایل ویدیو انتخاب کنید.")
    ext = Path(file_storage.filename).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTS:
        raise MediaValidationError("فرمت ویدیو مجاز نیست (فقط MP4 یا WebM).")

    max_mb = int(current_app.config.get("MAX_VIDEO_SIZE_MB", 40))
    raw = _read_and_check_size(file_storage, max_mb)
    if not _detect_video_magic(raw, ext):
        raise MediaValidationError("ساختار فایل ویدیو معتبر نیست.")

    rel_dir = Path(subdir)
    out_dir = _upload_root() / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    fname = f"{secrets.token_hex(12)}{ext}"
    (out_dir / fname).write_bytes(raw)
    return (rel_dir / fname).as_posix()


def delete_media_file(rel_path: str | None) -> None:
    if not rel_path:
        return
    clean = rel_path.strip().lstrip("/")
    if ".." in clean:
        return
    root = _upload_root().resolve()
    target = (root / clean).resolve()
    if root in target.parents and target.is_file():
        try:
            os.remove(target)
        except OSError:
            pass
