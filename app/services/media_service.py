"""مدیریت فایل‌ها: ذخیره امن، مسیر عمومی، حذف و بهینه‌سازی تصویر."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from flask import current_app, url_for
from werkzeug.utils import secure_filename

from app.utils.files import (
    FileValidationError,
    ensure_parent,
    human_size,
    normalize_original_name,
    safe_extension,
    unique_stored_name,
    unsafe_relative_path,
    validate_upload,
)
from app.utils.security import generate_token

try:  # Pillow اختیاری است؛ در صورت نبود، فایل اصلی ذخیره می‌شود
    from PIL import Image

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    PIL_AVAILABLE = False


def public_root() -> Path:
    return Path(current_app.config["UPLOAD_PUBLIC_DIR"])


def private_root() -> Path:
    return Path(current_app.config["UPLOAD_PRIVATE_DIR"])


def public_url(relative_path: str | None) -> str | None:
    """تبدیل مسیر ذخیره‌شده به URL قابل استفاده در قالب‌ها."""
    if not relative_path:
        return None
    value = str(relative_path).strip()
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("/"):
        return value
    try:
        return url_for("media.public_file", filename=value)
    except Exception:  # pragma: no cover - قبل از ثبت بلوپرینت
        return f"/media/{value}"


def _relative_path(subdir: str, extension: str) -> str:
    from app.utils.dates import as_tehran, utcnow

    year = as_tehran(utcnow()).strftime("%Y")
    safe_subdir = "/".join(secure_filename(part) for part in str(subdir).split("/") if part)
    return f"{safe_subdir}/{year}/{unique_stored_name(f'x.{extension}')}"


def _optimize_image(path: Path, max_dimension: int) -> tuple[int, int] | None:
    if not PIL_AVAILABLE:
        return None
    try:
        with Image.open(path) as image:
            image.load()
            size = image.size
            if max_dimension and max(size) > max_dimension:
                ratio = max_dimension / max(size)
                new_size = (max(1, int(size[0] * ratio)), max(1, int(size[1] * ratio)))
                image = image.resize(new_size, Image.LANCZOS)
            if path.suffix.lower() in {".jpg", ".jpeg"}:
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(path, "JPEG", quality=85, optimize=True, progressive=True)
            elif path.suffix.lower() == ".png":
                image.save(path, "PNG", optimize=True)
            elif path.suffix.lower() == ".webp":
                image.save(path, "WEBP", quality=85, method=4)
            return image.size
    except Exception:  # pragma: no cover
        return None


def save_upload(
    file_storage,
    subdir: str,
    kind: str = "image",
    max_mb: int = 5,
    private: bool = False,
    optimize: bool | None = None,
) -> dict:
    """
    ذخیره فایل آپلودی به صورت امن.
    بازگشتی: {"path": relative, "name": original, "size": bytes, "extension": ext, "mime": mime}
    """
    from app.services import settings_service

    if not settings_service.get_bool("uploads_enabled", True):
        raise FileValidationError("آپلود فایل در حال حاضر غیرفعال است.")

    ext, mime = validate_upload(file_storage, kind=kind, max_bytes=max(1, max_mb) * 1024 * 1024)
    relative = _relative_path(subdir, ext)
    root = private_root() if private else public_root()
    target = root / relative
    ensure_parent(target)

    file_storage.stream.seek(0)
    file_storage.save(target)

    if kind == "image" and (optimize if optimize is not None else settings_service.get_bool("optimize_images", True)):
        _optimize_image(target, settings_service.get_int("image_max_dimension", 1600))

    size = target.stat().st_size if target.exists() else 0
    return {
        "path": relative,
        "name": normalize_original_name(getattr(file_storage, "filename", "")),
        "size": size,
        "extension": ext,
        "mime": mime,
    }


def save_from_path(source: Path, subdir: str, private: bool = False) -> str:
    """انتقال فایل موجود (مثلاً بکاپ آپلودی) به مسیر ذخیره‌سازی."""
    ext = safe_extension(source.name) or "bin"
    relative = _relative_path(subdir, ext)
    root = private_root() if private else public_root()
    target = root / relative
    ensure_parent(target)
    shutil.move(str(source), str(target))
    return relative


def delete_public(relative_path: str | None) -> bool:
    if not relative_path or unsafe_relative_path(relative_path):
        return False
    path = public_root() / relative_path
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:  # pragma: no cover
        return False
    return False


def delete_private(relative_path: str | None) -> bool:
    if not relative_path or unsafe_relative_path(relative_path):
        return False
    path = private_root() / relative_path
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:  # pragma: no cover
        return False
    return False


def private_file_path(relative_path: str | None) -> Path | None:
    """مسیر امن فایل خصوصی با جلوگیری از Path Traversal."""
    if not relative_path:
        return None
    root = private_root().resolve()
    candidate = (root / relative_path).resolve()
    if root == candidate or root in candidate.parents:
        return candidate if candidate.is_file() else None
    return None


def file_size_label(relative_path: str | None, private: bool = True) -> str:
    path = private_file_path(relative_path) if private else (public_root() / (relative_path or ""))
    try:
        return human_size(path.stat().st_size) if path and path.exists() else "-"
    except OSError:
        return "-"


def new_upload_token() -> str:
    return generate_token(32)


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o755)
    except OSError:  # pragma: no cover
        pass
    return path
