from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass

from flask import current_app

from app.repositories.settings import get_int_setting
from app.services.smsir import enqueue_sms
from app.utils.persian import normalize_iran_mobile, now_utc_iso, parse_utc_iso, to_persian_digits


@dataclass
class OtpRequestResult:
    ok: bool
    message: str
    cooldown_remaining: int = 0
    dev_code: str = ""


@dataclass
class OtpVerifyResult:
    ok: bool
    message: str


def _hash_otp(mobile: str, purpose: str, code: str) -> str:
    secret = (current_app.config.get("OTP_SECRET") or "dev-otp").encode("utf-8")
    msg = f"{mobile}:{purpose}:{code}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def request_otp(
    conn: sqlite3.Connection,
    *,
    raw_mobile: str,
    purpose: str = "login",
    ip_address: str = "",
) -> OtpRequestResult:
    mobile = normalize_iran_mobile(raw_mobile)
    if not mobile:
        return OtpRequestResult(ok=False, message="شماره موبایل معتبر نیست (مثال: ۰۹۱۲۳۴۵۶۷۸۹).")

    cooldown_sec = int(current_app.config.get("OTP_RESEND_COOLDOWN_SECONDS", 60))
    expiry_sec = int(current_app.config.get("OTP_EXPIRY_SECONDS", 120))

    last_row = conn.execute(
        """
        SELECT * FROM otp_codes
        WHERE mobile = ? AND purpose = ? AND used_at IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        (mobile, purpose),
    ).fetchone()

    now_dt = dt.datetime.now(dt.timezone.utc)
    if last_row:
        created_dt = parse_utc_iso(last_row["created_at"])
        if created_dt:
            elapsed = (now_dt - created_dt).total_seconds()
            if elapsed < cooldown_sec:
                rem = int(cooldown_sec - elapsed) + 1
                return OtpRequestResult(
                    ok=False,
                    message=f"لطفاً {to_persian_digits(rem)} ثانیه دیگر برای ارسال مجدد کد صبر کنید.",
                    cooldown_remaining=rem,
                )

    # Invalidate previous unused OTPs for this mobile/purpose
    now_iso = now_utc_iso()
    conn.execute(
        "UPDATE otp_codes SET used_at = ? WHERE mobile = ? AND purpose = ? AND used_at IS NULL",
        (now_iso, mobile, purpose),
    )

    code = f"{secrets.randbelow(900000) + 100000:06d}"
    code_hash = _hash_otp(mobile, purpose, code)
    expires_at = (now_dt + dt.timedelta(seconds=expiry_sec)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    conn.execute(
        """
        INSERT INTO otp_codes (mobile, purpose, code_hash, expires_at, attempts, ip_address, created_at)
        VALUES (?, ?, ?, ?, 0, ?, ?)
        """,
        (mobile, purpose, code_hash, expires_at, ip_address[:64], now_iso),
    )

    tpl_id = get_int_setting(
        conn, "smsir_otp_template_id", int(current_app.config.get("SMSIR_OTP_TEMPLATE_ID", 100000))
    )
    enqueue_sms(
        conn,
        recipient=mobile,
        event_type=f"otp_{purpose}",
        template_id=tpl_id if tpl_id > 0 else None,
        params={"CODE": code},
        message_text=f"کد تایید شما در فروشگاه آرمین: {code}",
        deliver_now=True,
    )

    is_mock_sms = current_app.config.get("SMS_MODE", "mock") == "mock"
    return OtpRequestResult(
        ok=True,
        message="کد تایید ۶ رقمی به شماره موبایل شما ارسال شد.",
        dev_code=code if (is_mock_sms or not current_app.config.get("IS_PROD")) else "",
    )


def verify_otp(
    conn: sqlite3.Connection,
    *,
    raw_mobile: str,
    raw_code: str,
    purpose: str = "login",
) -> OtpVerifyResult:
    from app.utils.persian import to_english_digits

    mobile = normalize_iran_mobile(raw_mobile)
    if not mobile:
        return OtpVerifyResult(ok=False, message="شماره موبایل معتبر نیست.")

    code = to_english_digits((raw_code or "").strip())
    if not code.isdigit() or len(code) != 6:
        return OtpVerifyResult(ok=False, message="کد تایید باید ۶ رقم باشد.")

    max_attempts = int(current_app.config.get("OTP_MAX_ATTEMPTS", 5))
    row = conn.execute(
        """
        SELECT * FROM otp_codes
        WHERE mobile = ? AND purpose = ? AND used_at IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        (mobile, purpose),
    ).fetchone()

    if not row:
        return OtpVerifyResult(ok=False, message="کد تاییدی یافت نشد یا منقضی شده است.")

    if int(row["attempts"]) >= max_attempts:
        return OtpVerifyResult(
            ok=False, message="تعداد تلاش‌های ناموفق بیش از حد مجاز است. کد جدید دریافت کنید."
        )

    now_dt = dt.datetime.now(dt.timezone.utc)
    exp_dt = parse_utc_iso(row["expires_at"])
    if not exp_dt or now_dt > exp_dt:
        return OtpVerifyResult(ok=False, message="کد تایید منقضی شده است. لطفاً کد جدید بگیرید.")

    expected_hash = _hash_otp(mobile, purpose, code)
    if not hmac.compare_digest(row["code_hash"], expected_hash):
        conn.execute(
            "UPDATE otp_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],)
        )
        rem = max(0, max_attempts - (int(row["attempts"]) + 1))
        return OtpVerifyResult(
            ok=False,
            message=f"کد تایید اشتباه است ({to_persian_digits(rem)} تلاش باقی‌مانده).",
        )

    conn.execute(
        "UPDATE otp_codes SET used_at = ? WHERE id = ?", (now_utc_iso(), row["id"])
    )
    return OtpVerifyResult(ok=True, message="شماره موبایل با موفقیت تایید شد.")
