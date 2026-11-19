"""
تنظیمات پایه پروژه (Configuration Layer).

تمام مسیرها به صورت مطلق و مستقل از Current Working Directory محاسبه می‌شوند
تا پروژه هم روی cPanel (Passenger) و هم به صورت محلی بدون تغییر کار کند.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    """بارگذاری ساده فایل .env بدون وابستگی اجباری به python-dotenv."""
    if not path.exists():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


# بارگذاری .env از ریشه پروژه و از instance/ (نسخه سرور)
_load_env_file(BASE_DIR / ".env")
_load_env_file(BASE_DIR / "instance" / ".env")


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


INSTANCE_DIR = Path(os.environ.get("INSTANCE_DIR", BASE_DIR / "instance")).resolve()
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", BASE_DIR / "storage")).resolve()
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", BASE_DIR / "backups")).resolve()
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", INSTANCE_DIR / "store.db")).resolve()


def _resolve_secret_key() -> str:
    """کلید نشست: از env، در غیر این صورت یک کلید پایدار در instance/."""
    env_key = os.environ.get("SECRET_KEY", "").strip()
    if env_key:
        return env_key
    key_file = INSTANCE_DIR / "secret_key.txt"
    try:
        if key_file.exists():
            value = key_file.read_text(encoding="utf-8").strip()
            if value:
                return value
        INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        key_file.write_text(value, encoding="utf-8")
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return value
    except OSError:
        # آخرین راه‌حل: کلید موقت (نشست‌ها پس از ری‌استارت باطل می‌شوند)
        return secrets.token_hex(32)


class Config:
    """تنظیمات پیش‌فرض برنامه."""

    # --- پایه ---
    BASE_DIR = BASE_DIR
    INSTANCE_DIR = INSTANCE_DIR
    STORAGE_DIR = STORAGE_DIR
    BACKUP_DIR = BACKUP_DIR
    DATABASE_PATH = DATABASE_PATH

    SECRET_KEY = _resolve_secret_key()
    JSON_AS_ASCII = False
    TEMPLATES_AUTO_RELOAD = _as_bool(os.environ.get("TEMPLATES_AUTO_RELOAD"), False)
    EXPLAIN_TEMPLATE_LOADING = False

    # --- دیتابیس ---
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{DATABASE_PATH.as_posix()}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "connect_args": {"timeout": 20, "check_same_thread": False},
    }

    # --- نشست و امنیت ---
    SESSION_COOKIE_NAME = "armin_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _as_bool(os.environ.get("SESSION_COOKIE_SECURE"), False)
    PERMANENT_SESSION_LIFETIME = int(os.environ.get("SESSION_LIFETIME_SECONDS", 60 * 60 * 24 * 7))
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # --- آپلود ---
    # سقف کلی درخواست؛ سقف هر نوع فایل از تنظیمات پنل مدیریت خوانده می‌شود.
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH_MB", 80)) * 1024 * 1024
    MAX_FORM_MEMORY_SIZE = 4 * 1024 * 1024
    UPLOAD_PUBLIC_DIR = STORAGE_DIR / "public"
    UPLOAD_PRIVATE_DIR = STORAGE_DIR / "private"
    BACKUP_DIR = BACKUP_DIR

    # --- مسیر نصب و اجرا ---
    # APPLICATION_ROOT برای اجرا در Subdirectory توسط Passenger/وب‌سرور تنظیم می‌شود.
    APPLICATION_ROOT = os.environ.get("APPLICATION_ROOT", "/")
    PREFERRED_URL_SCHEME = os.environ.get("PREFERRED_URL_SCHEME", "https")
    TRUST_PROXY_HEADERS = _as_bool(os.environ.get("TRUST_PROXY_HEADERS"), True)

    # --- لاگ ---
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    LOG_DIR = INSTANCE_DIR / "logs"

    # --- ویژگی‌ها ---
    WTF_CSRF_ENABLED = True
    SEND_FILE_MAX_AGE_DEFAULT = 60 * 60 * 24 * 7
    TESTING = False

    @classmethod
    def ensure_directories(cls) -> None:
        for path in (
            cls.INSTANCE_DIR,
            cls.INSTANCE_DIR / "logs",
            cls.STORAGE_DIR,
            cls.UPLOAD_PUBLIC_DIR,
            cls.UPLOAD_PRIVATE_DIR,
            cls.BACKUP_DIR,
        ):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass


class DevelopmentConfig(Config):
    TESTING = False
    TEMPLATES_AUTO_RELOAD = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = _as_bool(os.environ.get("SESSION_COOKIE_SECURE"), True)
    TEMPLATES_AUTO_RELOAD = False


class TestConfig(Config):
    TESTING = True
    FORCE_INSTALLED = True  # در تست‌ها نصب انجام‌شده فرض می‌شود
    SECRET_KEY = "test-secret-key-not-for-production"
    SESSION_COOKIE_SECURE = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"timeout": 20, "check_same_thread": False},
    }
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
    WTF_CSRF_ENABLED = True


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestConfig,
}


def get_config(name: str | None = None):
    env = (name or os.environ.get("FLASK_ENV") or "production").lower()
    return CONFIG_MAP.get(env, ProductionConfig)
