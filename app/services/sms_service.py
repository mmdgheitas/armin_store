"""
سرویس پیامک SMS.ir بر اساس API الگو (Pattern) رسمی.

پیاده‌سازی با urllib انجام می‌شود تا وابستگی اضافه لازم نباشد.
آدرس پایه و مسیرها از تنظیمات خوانده می‌شوند تا با تغییرات سرویس سازگار بمانند.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from flask import current_app

from app.extensions import db
from app.models.content import SmsLog
from app.services import settings_service
from app.utils.dates import utcnow
from app.utils.security import normalize_mobile

SMS_EVENT_LABELS = {
    "otp": "کد ورود",
    "order_created": "ثبت سفارش",
    "payment_success": "پرداخت موفق",
    "order_processing": "پردازش سفارش",
    "order_ready": "آماده ارسال",
    "order_shipped": "ارسال سفارش",
    "order_delivered": "تحویل سفارش",
    "order_cancelled": "لغو سفارش",
    "ticket_reply": "پاسخ تیکت",
}

EVENT_PATTERN_KEYS = {
    "otp": "sms_pattern_otp",
    "order_created": "sms_pattern_order_created",
    "payment_success": "sms_pattern_payment_success",
    "order_processing": "sms_pattern_order_processing",
    "order_ready": "sms_pattern_order_ready",
    "order_shipped": "sms_pattern_order_shipped",
    "order_delivered": "sms_pattern_order_delivered",
    "order_cancelled": "sms_pattern_order_cancelled",
    "ticket_reply": "sms_pattern_ticket_reply",
}

EVENT_ENABLED_KEYS = {
    "order_created": "sms_notify_order_created",
    "payment_success": "sms_notify_payment_success",
    "order_processing": "sms_notify_order_processing",
    "order_ready": "sms_notify_order_ready",
    "order_shipped": "sms_notify_order_shipped",
    "order_delivered": "sms_notify_order_delivered",
    "order_cancelled": "sms_notify_order_cancelled",
    "ticket_reply": "sms_notify_ticket_reply",
}


@dataclass
class SmsResult:
    ok: bool
    message: str
    status: str = "failed"
    provider_response: dict | None = None


def is_configured() -> bool:
    return bool(
        settings_service.get_bool("sms_enabled")
        and settings_service.get_str("sms_api_key")
        and settings_service.get_str("sms_base_url")
    )


def _log(mobile: str, event: str, pattern: str, parameters: dict, status: str, message: str, response) -> None:
    try:
        db.session.add(
            SmsLog(
                mobile=mobile,
                pattern_code=str(pattern or ""),
                event=event,
                parameters_json=json.dumps(parameters, ensure_ascii=False)[:2000] if parameters else None,
                status=status,
                error_message=(message or "")[:400],
                provider_response=json.dumps(response, ensure_ascii=False)[:2000] if response else None,
                created_at=utcnow(),
            )
        )
        db.session.commit()
    except Exception:  # pragma: no cover - لاگ نباید عملیات اصلی را بشکند
        db.session.rollback()


def _post_json(url: str, payload: dict, api_key: str) -> tuple[bool, dict | str, int]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-api-key": api_key,
            "User-Agent": "ArminStore/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                return True, json.loads(raw), response.status
            except json.JSONDecodeError:
                return True, raw[:800], response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return False, f"HTTP {exc.code}: {raw[:400]}", exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"خطای ارتباط با سرویس پیامک: {exc}", 0


def send_by_event(event: str, mobile: str, parameters: dict) -> SmsResult:
    """ارسال پیامک بر اساس رویداد (الگوی متصل به آن رویداد)."""
    mobile = normalize_mobile(mobile)
    if not mobile:
        return SmsResult(False, "شماره موبایل نامعتبر است.", "skipped")

    enabled_key = EVENT_ENABLED_KEYS.get(event)
    if enabled_key and not settings_service.get_bool(enabled_key, True):
        return SmsResult(False, "ارسال پیامک این رویداد غیرفعال است.", "skipped")

    pattern_key = EVENT_PATTERN_KEYS.get(event)
    pattern = settings_service.get_str(pattern_key) if pattern_key else ""
    if not is_configured():
        _log(mobile, event, pattern, parameters, "skipped", "سامانه پیامک پیکربندی نشده است.", None)
        return SmsResult(False, "سامانه پیامک پیکربندی نشده است.", "skipped")
    if not pattern and event != "otp":
        _log(mobile, event, pattern, parameters, "skipped", "الگوی پیامک تعریف نشده است.", None)
        return SmsResult(False, "برای این رویداد الگوی پیامک تعریف نشده است.", "skipped")

    return _send_pattern(mobile, pattern, parameters, event)


def _send_pattern(mobile: str, pattern: str, parameters: dict, event: str) -> SmsResult:
    api_key = settings_service.get_str("sms_api_key")
    base_url = settings_service.get_str("sms_base_url").rstrip("/")
    path = settings_service.get_str("sms_verify_path") or "/v1/send/verify"

    try:
        template_id = int(pattern)
    except (TypeError, ValueError):
        message = "کد الگوی پیامک باید عددی باشد (شناسه الگو در SMS.ir)."
        _log(mobile, event, pattern, parameters, "failed", message, None)
        return SmsResult(False, message)

    payload = {
        "mobile": mobile,
        "templateId": template_id,
        "parameters": [
            {"name": str(name), "value": str(value)} for name, value in (parameters or {}).items()
        ],
    }
    ok, response, status_code = _post_json(f"{base_url}{path}", payload, api_key)
    provider_status = None
    if isinstance(response, dict):
        provider_status = response.get("status")

    if ok and (provider_status in (1, "1", None)):
        _log(mobile, event, pattern, parameters, "sent", "ارسال شد", response)
        return SmsResult(True, "پیامک ارسال شد.", "sent", response if isinstance(response, dict) else None)

    provider_message = ""
    if isinstance(response, dict):
        provider_message = str(response.get("message") or "")
    elif isinstance(response, str):
        provider_message = response
    message = f"ارسال پیامک ناموفق بود. {provider_message}".strip()
    current_app.logger.warning("SMS send failed event=%s status=%s", event, status_code)
    _log(mobile, event, pattern, parameters, "failed", message, response)
    return SmsResult(False, message, "failed", response if isinstance(response, dict) else None)


def send_otp(mobile: str, code: str) -> SmsResult:
    """ارسال کد یکبارمصرف با الگوی OTP.

    پارامترهای الگو: CODE (و در صورت نیاز VERIFY_CODE). هر دو ارسال می‌شوند تا
    با الگوهای رایج SMS.ir سازگار باشد؛ پارامتر تعریف‌نشده در الگو نادیده گرفته می‌شود.
    """
    return send_by_event("otp", mobile, {"CODE": code, "VERIFY_CODE": code})


def send_test(mobile: str) -> SmsResult:
    """ارسال پیامک آزمایشی از پنل (فقط با الگوی OTP یا الگوی عمومی)."""
    from flask import current_app

    code = "123456"
    result = send_otp(mobile, code)
    if result.ok and current_app.config.get("TESTING"):
        return result
    return result


def send_ticket_reply(mobile: str, ticket_number: str) -> SmsResult:
    return send_by_event("ticket_reply", mobile, {"TICKET": ticket_number})


def last_logs(limit: int = 20):
    return (
        db.session.query(SmsLog)
        .order_by(SmsLog.created_at.desc())
        .limit(limit)
        .all()
    )
