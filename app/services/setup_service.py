"""نصب اولیه از مرورگر (Setup Wizard) بدون نیاز به ترمینال."""
from __future__ import annotations

import os
import platform
import secrets
import sqlite3
import sys
from pathlib import Path

from flask import current_app

from config import Config

INSTALL_LOCK_NAME = "installed.lock"
INSTALL_KEY_NAME = "install_key.txt"

APACHE_DENY = """# جلوگیری از دسترسی عمومی به فایل‌های حساس (cPanel / Apache)
<IfModule mod_authz_core.c>
    Require all denied
</IfModule>
<IfModule !mod_authz_core.c>
    Order allow,deny
    Deny from all
</IfModule>
"""


def instance_dir() -> Path:
    return Path(current_app.config["INSTANCE_DIR"])


def lock_path() -> Path:
    return instance_dir() / INSTALL_LOCK_NAME


def install_key_path() -> Path:
    return instance_dir() / INSTALL_KEY_NAME


def is_installed() -> bool:
    from flask import current_app, has_app_context

    if has_app_context() and current_app.config.get("FORCE_INSTALLED"):
        return True
    return lock_path().exists()


def install_key(create: bool = True) -> str:
    path = install_key_path()
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        if not create:
            return ""
        instance_dir().mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(12)
        path.write_text(value, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return value
    except OSError:
        return ""


def verify_install_key(candidate: str) -> bool:
    expected = install_key(create=True)
    if not expected:
        # در صورت عدم امکان ساخت فایل، نصب برای جلوگیری از قفل شدن، مجاز است.
        return True
    return secrets.compare_digest(expected, (candidate or "").strip())


def ensure_protection_files() -> None:
    """فایل‌های .htaccess برای پوشه‌های حساس (در صورت پشتیبانی Apache)."""
    targets = [
        Path(current_app.config["INSTANCE_DIR"]),
        Path(current_app.config["STORAGE_DIR"]),
        Path(current_app.config["BACKUP_DIR"]),
    ]
    for directory in targets:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            htaccess = directory / ".htaccess"
            if not htaccess.exists():
                htaccess.write_text(APACHE_DENY, encoding="utf-8")
        except OSError:  # pragma: no cover
            continue


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write_test_{secrets.token_hex(4)}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def environment_checks() -> list[dict]:
    """بررسی پیش‌نیازها برای نمایش در Setup Wizard."""
    checks: list[dict] = []

    py_version = sys.version_info
    checks.append(
        {
            "key": "python",
            "label": "نسخه Python",
            "ok": py_version >= (3, 9),
            "detail": f"{platform.python_version()} — نیازمند ۳.۹ یا بالاتر",
            "required": True,
        }
    )

    sqlite_version = sqlite3.sqlite_version_info
    checks.append(
        {
            "key": "sqlite",
            "label": "نسخه SQLite",
            "ok": sqlite_version >= (3, 24, 0),
            "detail": f"{sqlite3.sqlite_version}",
            "required": True,
        }
    )

    for key, label in (
        ("INSTANCE_DIR", "پوشه instance"),
        ("STORAGE_DIR", "پوشه storage"),
        ("BACKUP_DIR", "پوشه backups"),
    ):
        directory = Path(current_app.config[key])
        checks.append(
            {
                "key": key.lower(),
                "label": f"دسترسی نوشتن {label}",
                "ok": _writable(directory),
                "detail": str(directory),
                "required": True,
            }
        )

    db_path = Path(current_app.config["DATABASE_PATH"])
    db_dir = db_path.parent
    checks.append(
        {
            "key": "database",
            "label": "مسیر دیتابیس",
            "ok": _writable(db_dir) or db_path.exists(),
            "detail": str(db_path),
            "required": True,
        }
    )

    for module, label, required in (
        ("flask", "Flask", True),
        ("flask_sqlalchemy", "Flask-SQLAlchemy", True),
        ("sqlalchemy", "SQLAlchemy", True),
        ("werkzeug", "Werkzeug", True),
        ("openpyxl", "openpyxl (خروجی Excel)", False),
        ("reportlab", "ReportLab (خروجی PDF)", False),
        ("PIL", "Pillow (بهینه‌سازی تصویر)", False),
    ):
        try:
            __import__(module)
            ok = True
            detail = "نصب شده"
        except Exception:
            ok = False
            detail = "نصب نشده" + ("" if required else " (اختیاری)")
        checks.append(
            {"key": f"pkg_{module}", "label": label, "ok": ok or not required, "detail": detail, "required": required}
        )

    checks.append(
        {
            "key": "passenger",
            "label": "فایل اجرای Passenger",
            "ok": (Path(current_app.config["BASE_DIR"]) / "passenger_wsgi.py").exists(),
            "detail": "passenger_wsgi.py",
            "required": True,
        }
    )
    checks.append(
        {
            "key": "installed",
            "label": "وضعیت نصب",
            "ok": True,
            "detail": "نصب انجام شده است" if is_installed() else "در انتظار نصب",
            "required": False,
        }
    )
    return checks


def required_checks_passed(checks: list[dict]) -> bool:
    return all(item["ok"] for item in checks if item.get("required"))


def install(form: dict) -> tuple[bool, str]:
    """
    اجرای نصب کامل: ساخت جداول، داده اولیه، مدیر کل و ثبت وضعیت نصب.
    بازگشتی: (ok, message)
    """
    from app.database.migrator import upgrade
    from app.database.seed import create_admin, seed_all
    from app.extensions import db
    from app.services import settings_service
    from app.utils.security import is_valid_mobile, normalize_mobile, validate_password_strength

    if is_installed():
        return False, "نصب قبلاً انجام شده است."
    if not verify_install_key(form.get("install_key", "")):
        return False, "کلید نصب صحیح نیست. مقدار فایل instance/install_key.txt را وارد کنید."

    store_name = (form.get("store_name") or "").strip()
    admin_name = (form.get("admin_name") or "").strip()
    admin_mobile = normalize_mobile(form.get("admin_mobile", ""))
    password = form.get("admin_password") or ""
    confirm = form.get("admin_password_confirm") or ""

    errors: list[str] = []
    if len(store_name) < 3:
        errors.append("نام فروشگاه را وارد کنید.")
    if len(admin_name) < 3:
        errors.append("نام و نام خانوادگی مدیر کل را وارد کنید.")
    if not is_valid_mobile(admin_mobile):
        errors.append("شماره موبایل مدیر کل معتبر نیست.")
    errors.extend(validate_password_strength(password, 8))
    if password != confirm:
        errors.append("تکرار رمز عبور مطابقت ندارد.")
    if errors:
        return False, " ".join(errors)

    try:
        ensure_protection_files()
        executed = upgrade(verbose=True)
        seed_all(include_demo=bool(form.get("include_demo")))
        overrides = {
            "store_name": store_name,
            "store_slogan": (form.get("store_slogan") or "خرید آسان و مطمئن").strip(),
            "currency_label": (form.get("currency_label") or "تومان").strip() or "تومان",
            "currency_unit": form.get("currency_unit") or "toman",
            "contact_email": (form.get("contact_email") or "").strip(),
            "contact_mobile": (form.get("contact_mobile") or "").strip(),
            "force_https": bool(form.get("force_https")),
            "setup_completed": True,
        }
        settings_service.set_many(overrides, commit=False)
        db.session.commit()

        create_admin(admin_name, admin_mobile, password)

        from app.services import audit_service

        audit_service.log(
            "install.completed",
            entity_type="system",
            new_data={"store_name": store_name, "migrations": executed},
            commit=True,
        )

        lock = {
            "version": str(settings_service.get("schema_version", 1)),
            "python": platform.python_version(),
        }
        from app.utils.dates import utcnow

        lock_path().write_text(
            f"installed_at={utcnow().isoformat()}\nversion={lock['version']}\npython={lock['python']}\n",
            encoding="utf-8",
        )
        try:
            os.chmod(lock_path(), 0o600)
        except OSError:
            pass
        if install_key_path().exists():
            try:
                os.chmod(install_key_path(), 0o600)
            except OSError:
                pass
        return True, "نصب با موفقیت انجام شد."
    except Exception as exc:  # pragma: no cover - خطای نصب
        from app.extensions import db

        db.session.rollback()
        current_app.logger.exception("installation failed")
        return False, f"نصب با خطا مواجه شد: {exc}"


def reset_installation_lock_for_tests() -> None:
    """فقط برای تست‌ها."""
    try:
        if lock_path().exists():
            lock_path().unlink()
    except OSError:
        pass
