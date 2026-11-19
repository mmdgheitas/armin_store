"""پشتیبان‌گیری و بازیابی دیتابیس با SQLite Backup API."""
from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path

from flask import current_app
from sqlalchemy import text

from app.extensions import db
from app.models import BackupLog
from app.utils.dates import as_tehran, utcnow
from app.utils.files import human_size, unsafe_filename

REQUIRED_BACKUP_TABLES = {"users", "orders", "products", "settings", "schema_migrations"}


class BackupError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def backups_dir() -> Path:
    path = Path(current_app.config["BACKUP_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    """مسیر فایل دیتابیس؛ در صورت تفاوت تنظیمات، از موتور SQLAlchemy خوانده می‌شود."""
    configured = Path(current_app.config["DATABASE_PATH"])
    if configured.exists():
        return configured
    try:
        from app.extensions import db

        raw = db.engine.url.database
    except Exception:  # pragma: no cover - نبود بستر برنامه
        return configured
    if raw and raw != ":memory:":
        return Path(raw)
    return configured


def _safe_path(filename: str) -> Path | None:
    if not filename or unsafe_filename(filename) or not filename.endswith(".db"):
        return None
    root = backups_dir().resolve()
    candidate = (root / filename).resolve()
    if root in candidate.parents and candidate.is_file():
        return candidate
    return None


def create_backup(admin_id: int | None = None, note: str = "", prefix: str = "backup") -> dict:
    """
    ساخت بکاپ با SQLite Backup API (سازگار با دیتابیس در حال استفاده).
    """
    source_path = database_path()
    if not source_path.exists():
        raise BackupError("فایل دیتابیس یافت نشد.")

    stamp = as_tehran(utcnow()).strftime("%Y%m%d-%H%M%S")
    filename = f"{prefix}-{stamp}-{secrets.token_hex(3)}.db"
    target = backups_dir() / filename
    temp_target = target.with_suffix(".tmp")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    try:
        with sqlite3.connect(str(source_path), timeout=20) as source:
            with sqlite3.connect(str(temp_target)) as destination:
                source.backup(destination)
                destination.execute("PRAGMA integrity_check")
                result = destination.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise BackupError("فایل بکاپ معتبر نیست (integrity check ناموفق).")
        os.replace(temp_target, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    except BackupError:
        if temp_target.exists():
            temp_target.unlink(missing_ok=True)
        raise
    except (sqlite3.Error, OSError) as exc:
        if temp_target.exists():
            temp_target.unlink(missing_ok=True)
        current_app.logger.exception("backup failed")
        raise BackupError(f"ساخت بکاپ ناموفق بود: {exc}") from exc

    size = target.stat().st_size
    log = BackupLog(
        filename=filename,
        size=size,
        status="success",
        backup_type="manual" if prefix == "backup" else prefix,
        note=(note or "")[:300] or None,
        admin_id=admin_id,
        created_at=utcnow(),
    )
    db.session.add(log)
    db.session.commit()
    prune_old_backups()
    return {"filename": filename, "size": size, "path": target, "log": log}


def list_backups() -> list[dict]:
    entries: list[dict] = []
    directory = backups_dir()
    for path in sorted(directory.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        log = (
            db.session.query(BackupLog)
            .filter(BackupLog.filename == path.name)
            .order_by(BackupLog.created_at.desc())
            .first()
        )
        entries.append(
            {
                "filename": path.name,
                "size": stat.st_size,
                "size_label": human_size(stat.st_size),
                "created_at": log.created_at if log else None,
                "note": log.note if log else "",
                "status": log.status if log else "success",
            }
        )
    return entries


def prune_old_backups() -> int:
    from app.services import settings_service

    try:
        keep = max(1, settings_service.get_int("backup_keep_count", 10))
    except Exception:
        keep = 10
    files = sorted(backups_dir().glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for path in files[keep:]:
        try:
            path.unlink()
            removed += 1
            db.session.query(BackupLog).filter(BackupLog.filename == path.name).delete()
        except OSError:  # pragma: no cover
            continue
    if removed:
        db.session.commit()
    return removed


def delete_backup(filename: str, admin_id: int | None = None) -> tuple[bool, str]:
    path = _safe_path(filename)
    if path is None:
        return False, "فایل بکاپ یافت نشد."
    try:
        path.unlink()
        db.session.query(BackupLog).filter(BackupLog.filename == filename).delete()
        db.session.commit()
        return True, "بکاپ حذف شد."
    except OSError:
        return False, "حذف بکاپ انجام نشد."


def get_backup_file(filename: str) -> Path | None:
    return _safe_path(filename)


def validate_backup(path: Path) -> dict:
    """اعتبارسنجی فایل بکاپ پیش از بازیابی."""
    if not path or not path.exists():
        raise BackupError("فایل بکاپ یافت نشد.")
    head = path.open("rb").read(16)
    if not head.startswith(b"SQLite format 3"):
        raise BackupError("فایل انتخاب‌شده یک دیتابیس SQLite معتبر نیست.")

    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=10) as connection:
            check = connection.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise BackupError("ساختار فایل بکاپ خراب است.")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            missing = REQUIRED_BACKUP_TABLES - tables
            if missing:
                raise BackupError(
                    "فایل بکاپ جداول ضروری را ندارد: " + "، ".join(sorted(missing))
                )
            version_row = None
            if "schema_migrations" in tables:
                version_row = connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()
            version = int(version_row[0] or 0) if version_row else 0
            counts = {}
            for table in ("users", "orders", "products"):
                counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error as exc:
        raise BackupError(f"خواندن فایل بکاپ ناموفق بود: {exc}") from exc

    from app.database.migrator import CURRENT_SCHEMA_VERSION

    if version > CURRENT_SCHEMA_VERSION:
        raise BackupError(
            "نسخه ساختار این بکاپ از نسخه فعلی نرم‌افزار جدیدتر است و بازیابی آن پشتیبانی نمی‌شود."
        )
    return {"version": version, "counts": counts, "size": path.stat().st_size}


def restore_backup(filename: str, admin_id: int | None = None) -> tuple[bool, str]:
    """
    بازیابی دیتابیس از بکاپ:
      1) اعتبارسنجی کامل فایل
      2) ساخت بکاپ اضطراری از وضعیت فعلی
      3) جایگزینی اتمیک فایل دیتابیس
      4) اجرای مهاجرت‌های لازم
    فایل‌های رسانه و storage دست‌نخورده می‌مانند.
    """
    from app.database.migrator import upgrade, verify_schema

    path = _safe_path(filename)
    if path is None:
        return False, "فایل بکاپ یافت نشد."
    try:
        info = validate_backup(path)
    except BackupError as exc:
        return False, exc.message

    try:
        emergency = create_backup(admin_id=admin_id, note="بکاپ اضطراری پیش از بازیابی", prefix="pre-restore")
    except BackupError as exc:
        return False, f"ساخت بکاپ اضطراری ناموفق بود؛ بازیابی انجام نشد. {exc.message}"

    db_path = database_path()
    temp_target = db_path.with_suffix(".restore.tmp")
    try:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        db.session.remove()
        db.engine.dispose()

        # کپی محتوا به فایل موقت و سپس جایگزینی اتمیک
        with sqlite3.connect(str(path), timeout=20) as source:
            with sqlite3.connect(str(temp_target)) as destination:
                source.backup(destination)
        os.replace(temp_target, db_path)
        for suffix in ("-wal", "-shm"):
            stale = Path(str(db_path) + suffix)
            if stale.exists():
                stale.unlink(missing_ok=True)
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass

        executed = upgrade(verbose=True)
        ok, missing = verify_schema()
        if not ok:
            return False, "بازیابی انجام شد اما ساختار دیتابیس کامل نیست: " + "، ".join(missing)
    except (sqlite3.Error, OSError) as exc:
        current_app.logger.exception("restore failed")
        return False, f"بازیابی ناموفق بود: {exc}"

    db.session.add(
        BackupLog(
            filename=filename,
            size=info["size"],
            status="restored",
            backup_type="restore",
            note=f"بازیابی انجام شد. بکاپ اضطراری: {emergency['filename']}",
            admin_id=admin_id,
            created_at=utcnow(),
        )
    )
    db.session.commit()
    return True, f"دیتابیس با موفقیت بازیابی شد. بکاپ اضطراری: {emergency['filename']}"
