"""
سیستم مهاجرت سبک و بدون نیاز به ترمینال.

هر مهاجرت یک نسخه عددی دارد و یک‌بار اجرا می‌شود. نسخه‌های اجراشده در جدول
schema_migrations ثبت می‌شوند. اجرا از Setup Wizard یا پنل مدیریت ممکن است.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from flask import current_app
from sqlalchemy import text

from app.extensions import db
from app.utils.dates import utcnow

CURRENT_SCHEMA_VERSION = 1


@dataclass
class Migration:
    version: int
    description: str
    upgrade: Callable[[], None]


def _create_base_schema() -> None:
    """ایجاد تمام جداول از متادیتای مدل‌ها (نسخه ۱)."""
    import app.models  # noqa: F401  ثبت مدل‌ها در متادیتا

    db.metadata.create_all(bind=db.engine)


MIGRATIONS: list[Migration] = [
    Migration(1, "ایجاد ساختار پایه دیتابیس", _create_base_schema),
]


def ensure_migration_table() -> None:
    db.session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                description VARCHAR(255) NOT NULL,
                applied_at DATETIME NOT NULL
            )
            """
        )
    )
    db.session.commit()


def applied_versions() -> set[int]:
    ensure_migration_table()
    rows = db.session.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {int(row[0]) for row in rows}


def pending_migrations() -> list[Migration]:
    applied = applied_versions()
    return [m for m in MIGRATIONS if m.version not in applied]


def upgrade(verbose: bool = False) -> list[str]:
    """اجرای مهاجرت‌های باقی‌مانده به ترتیب نسخه."""
    from app.services import settings_service

    ensure_migration_table()
    applied = applied_versions()
    executed: list[str] = []
    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        if migration.version in applied:
            continue
        migration.upgrade()
        db.session.execute(
            text(
                "INSERT INTO schema_migrations (version, description, applied_at) "
                "VALUES (:version, :description, :applied_at)"
            ),
            {"version": migration.version, "description": migration.description, "applied_at": utcnow()},
        )
        db.session.commit()
        executed.append(f"{migration.version}: {migration.description}")
        if verbose:
            current_app.logger.info("Migration applied: %s", migration.description)

    try:
        settings_service.set_value("schema_version", CURRENT_SCHEMA_VERSION)
        db.session.commit()
    except Exception:  # pragma: no cover - قبل از ساخت جدول settings
        db.session.rollback()
    return executed


def schema_status() -> dict:
    applied = applied_versions()
    return {
        "current": max(applied) if applied else 0,
        "target": CURRENT_SCHEMA_VERSION,
        "pending": [m.version for m in MIGRATIONS if m.version not in applied],
        "migrations": [
            {"version": m.version, "description": m.description, "applied": m.version in applied}
            for m in sorted(MIGRATIONS, key=lambda m: m.version)
        ],
    }


def verify_schema(required_tables: list[str] | None = None) -> tuple[bool, list[str]]:
    """بررسی وجود جداول ضروری (برای Setup و بازیابی بکاپ)."""
    from app.database.seed import REQUIRED_TABLES

    tables = set(
        row[0]
        for row in db.session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    )
    required = required_tables or REQUIRED_TABLES
    missing = [table for table in required if table not in tables]
    return len(missing) == 0, missing
