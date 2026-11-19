"""سرویس فایل‌های عمومی (تصاویر محصولات، لوگو و ...) با محافظت از Path Traversal."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, current_app, send_from_directory

from app.utils.files import is_public_path_allowed, unsafe_relative_path

media_bp = Blueprint("media", __name__, url_prefix="/media")


@media_bp.get("/<path:filename>")
def public_file(filename: str):
    if unsafe_relative_path(filename):
        abort(404)
    root = Path(current_app.config["UPLOAD_PUBLIC_DIR"])
    target = root / filename
    if not is_public_path_allowed(target, root) or not target.is_file():
        abort(404)
    return send_from_directory(
        root,
        filename,
        conditional=True,
        max_age=60 * 60 * 24 * 7,
        mimetype=None,
    )
