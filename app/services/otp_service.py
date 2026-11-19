"""سرویس کد یکبارمصرف: تولید، ارسال، اعتبارسنجی و محدودیت نرخ."""
from __future__ import annotations

from datetime import timedelta

from flask import current_app

from app.extensions import db
from app.models.content import OtpCode
from app.services import rate_limit_service, settings_service, sms_service
from app.utils.dates import utcnow
from app.utils.security import generate_numeric_code, hash_token, normalize_mobile

PURPOSES = {"login", "register", "verify_mobile", "track_order", "reset_password"}

# فقط در محیط تست برای خواندن کد؛ در تولید استفاده نمی‌شود.
_DEBUG_CODES: dict[str, str] = {}


class OtpError(Exception):
    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


def _hash(code: str) -> str:
    return hash_token(code, current_app.config["SECRET_KEY"])


def _cleanup_old() -> None:
    cutoff = utcnow() - timedelta(days=2)
    try:
        db.session.query(OtpCode).filter(OtpCode.created_at < cutoff).delete(synchronize_session=False)
        db.session.commit()
    except Exception:  # pragma: no cover
        db.session.rollback()


def send_code(mobile: str, purpose: str = "login", ip: str | None = None) -> dict:
    """
    تولید و ارسال کد. در صورت محدودیت، OtpError با پیام فارسی پرتاب می‌شود.
    بازگشتی: {"mobile", "code" (فقط محیط توسعه/تست), "sent": bool, "message": str}
    """
    if purpose not in PURPOSES:
        raise OtpError("نوع درخواست کد نامعتبر است.")
    mobile = normalize_mobile(mobile)
    if len(mobile) != 11 or not mobile.startswith("09"):
        raise OtpError("شماره موبایل نامعتبر است.")

    resend_seconds = max(30, settings_service.get_int("otp_resend_seconds", 90))
    last = (
        db.session.query(OtpCode)
        .filter(OtpCode.mobile == mobile)
        .order_by(OtpCode.created_at.desc())
        .first()
    )
    if last and last.created_at:
        elapsed = (utcnow() - last.created_at).total_seconds()
        if elapsed < resend_seconds:
            raise OtpError(
                f"لطفاً {int(resend_seconds - elapsed)} ثانیه دیگر برای دریافت مجدد کد تلاش کنید.",
                int(resend_seconds - elapsed),
            )

    max_per_hour = settings_service.get_int("otp_max_per_hour", 6)
    allowed, _, retry_after = rate_limit_service.consume(f"otp:{mobile}", max_per_hour, 3600)
    if not allowed:
        raise OtpError(
            f"تعداد درخواست کد بیش از حد مجاز است. لطفاً {max(1, retry_after // 60)} دقیقه دیگر تلاش کنید.",
            retry_after,
        )

    length = min(8, max(4, settings_service.get_int("otp_length", 6)))
    code = generate_numeric_code(length)
    ttl = max(1, settings_service.get_int("otp_ttl_minutes", 5))
    record = OtpCode(
        mobile=mobile,
        code_hash=_hash(code),
        purpose=purpose,
        attempts=0,
        ip_address=(ip or "")[:64] or None,
        expires_at=utcnow() + timedelta(minutes=ttl),
        created_at=utcnow(),
    )
    db.session.add(record)
    db.session.commit()

    result = sms_service.send_otp(mobile, code)
    if current_app.config.get("TESTING") or current_app.debug:
        _DEBUG_CODES[mobile] = code
    _cleanup_old()

    if not result.ok:
        # کد در دیتابیس باقی نمی‌ماند تا در صورت عدم ارسال، ورود ممکن نباشد.
        if result.status == "skipped":
            # در محیط تست/توسعه اجازه ادامه می‌دهیم، در تولید خطا برمی‌گردد.
            if not (current_app.config.get("TESTING") or current_app.debug):
                db.session.delete(record)
                db.session.commit()
                raise OtpError("سامانه پیامک پیکربندی نشده است. لطفاً با پشتیبانی تماس بگیرید.")
        else:
            db.session.delete(record)
            db.session.commit()
            raise OtpError("ارسال پیامک انجام نشد. لطفاً دوباره تلاش کنید.")

    payload = {
        "mobile": mobile,
        "sent": result.ok,
        "message": "کد تأیید برای شما ارسال شد." if result.ok else "کد تأیید در محیط آزمایشی تولید شد.",
        "ttl_minutes": ttl,
    }
    if mobile in _DEBUG_CODES and (current_app.config.get("TESTING") or current_app.debug):
        payload["debug_code"] = _DEBUG_CODES[mobile]
    return payload


def verify_code(mobile: str, code: str, purpose: str = "login") -> tuple[bool, str]:
    """اعتبارسنجی کد. در صورت موفقیت کد مصرف می‌شود."""
    mobile = normalize_mobile(mobile)
    code = (code or "").strip()
    if not code:
        return False, "کد تأیید را وارد کنید."

    max_attempts = max(1, settings_service.get_int("otp_max_attempts", 5))
    record = (
        db.session.query(OtpCode)
        .filter(
            OtpCode.mobile == mobile,
            OtpCode.purpose == purpose,
            OtpCode.consumed_at.is_(None),
        )
        .order_by(OtpCode.created_at.desc())
        .first()
    )
    if record is None:
        return False, "کدی برای این شماره ثبت نشده است. لطفاً کد جدید درخواست کنید."
    if record.expires_at < utcnow():
        record.consumed_at = utcnow()
        db.session.commit()
        return False, "اعتبار کد به پایان رسیده است. لطفاً کد جدید درخواست کنید."
    if record.attempts >= max_attempts:
        record.consumed_at = utcnow()
        db.session.commit()
        return False, "تعداد تلاش‌های ناموفق زیاد است. لطفاً کد جدید درخواست کنید."

    if not hash_token(code, current_app.config["SECRET_KEY"]) == record.code_hash:
        record.attempts = (record.attempts or 0) + 1
        db.session.commit()
        remaining = max(0, max_attempts - record.attempts)
        return False, f"کد وارد شده صحیح نیست. {remaining} تلاش دیگر باقی مانده است."

    record.consumed_at = utcnow()
    db.session.commit()
    _DEBUG_CODES.pop(mobile, None)
    return True, "کد تأیید شد."


def get_debug_code(mobile: str) -> str | None:
    """فقط برای تست‌های خودکار."""
    return _DEBUG_CODES.get(normalize_mobile(mobile))
