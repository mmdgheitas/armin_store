from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    IS_PROD = FLASK_ENV == "production"

    SECRET_KEY = os.getenv("SECRET_KEY") or (
        "" if IS_PROD else "dev-insecure-secret-key-change-me"
    )
    OTP_SECRET = os.getenv("OTP_SECRET") or (
        "" if IS_PROD else "dev-insecure-otp-secret-change-me"
    )

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool(os.getenv("SESSION_COOKIE_SECURE"), IS_PROD)
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.getenv("SESSION_IDLE_MINUTES", "120"))
    )
    WTF_CSRF_TIME_LIMIT = 7200

    ADMIN_URL_PREFIX = "/" + os.getenv("ADMIN_URL_PREFIX", "/admin").strip("/")
    BASE_URL = os.getenv("BASE_URL", "http://localhost:5000").rstrip("/")

    DATABASE_PATH = str(
        Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "instance" / "armin_store.db")))
        if Path(os.getenv("DATABASE_PATH", "")).is_absolute()
        else BASE_DIR / os.getenv("DATABASE_PATH", "instance/armin_store.db")
    )
    UPLOAD_FOLDER = str(
        Path(os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "instance" / "uploads")))
        if Path(os.getenv("UPLOAD_FOLDER", "")).is_absolute()
        else BASE_DIR / os.getenv("UPLOAD_FOLDER", "instance/uploads")
    )
    BACKUP_FOLDER = str(
        Path(os.getenv("BACKUP_FOLDER", str(BASE_DIR / "instance" / "backups")))
        if Path(os.getenv("BACKUP_FOLDER", "")).is_absolute()
        else BASE_DIR / os.getenv("BACKUP_FOLDER", "instance/backups")
    )
    LOG_FOLDER = str(
        Path(os.getenv("LOG_FOLDER", str(BASE_DIR / "instance" / "logs")))
        if Path(os.getenv("LOG_FOLDER", "")).is_absolute()
        else BASE_DIR / os.getenv("LOG_FOLDER", "instance/logs")
    )

    MAX_IMAGE_SIZE_MB = int(os.getenv("MAX_IMAGE_SIZE_MB", "5"))
    MAX_VIDEO_SIZE_MB = int(os.getenv("MAX_VIDEO_SIZE_MB", "40"))
    MAX_CONTENT_LENGTH = max(MAX_IMAGE_SIZE_MB, MAX_VIDEO_SIZE_MB, 50) * 1024 * 1024

    PAYMENT_MODE = os.getenv("PAYMENT_MODE", "mock").strip().lower()
    BITPAY_API_KEY = os.getenv("BITPAY_API_KEY", "")
    BITPAY_TIMEOUT_SECONDS = int(os.getenv("BITPAY_TIMEOUT_SECONDS", "15"))
    BITPAY_RETRIES = int(os.getenv("BITPAY_RETRIES", "2"))

    SMS_MODE = os.getenv("SMS_MODE", "mock").strip().lower()
    SMSIR_API_KEY = os.getenv("SMSIR_API_KEY", "")
    SMSIR_LINE_NUMBER = os.getenv("SMSIR_LINE_NUMBER", "")
    SMSIR_TIMEOUT_SECONDS = int(os.getenv("SMSIR_TIMEOUT_SECONDS", "10"))
    SMSIR_OTP_TEMPLATE_ID = int(os.getenv("SMSIR_OTP_TEMPLATE_ID", "100000") or "100000")

    OTP_EXPIRY_SECONDS = int(os.getenv("OTP_EXPIRY_SECONDS", "120"))
    OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv("OTP_RESEND_COOLDOWN_SECONDS", "60"))
    OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))

    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    TESTING = False


class TestingConfig(Config):
    TESTING = True
    FLASK_ENV = "testing"
    IS_PROD = False
    SECRET_KEY = "test-secret-key"
    OTP_SECRET = "test-otp-secret"
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = True
    PAYMENT_MODE = "mock"
    SMS_MODE = "mock"
    RATELIMIT_ENABLED = False
