"""
درگاه پرداخت BitPay (bitpay.ir).

پیاده‌سازی بر پایه الگوی رسمی این درگاه انجام شده و مسیرها/آدرس‌ها از تنظیمات
خوانده می‌شوند تا در صورت تغییر مستندات، بدون تغییر کد قابل به‌روزرسانی باشد.
کلید API هرگز در سمت کلاینت یا لاگ عمومی قرار نمی‌گیرد.

جریان:
  1) دریافت توکن: POST {base}{token_path}  با کلید API در مسیر
  2) هدایت کاربر به {gateway_url} با توکن
  3) بازگشت کاربر با trans_id و id_get
  4) تأیید سمت سرور: POST {base}{verify_path}
"""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from flask import current_app

from app.services import settings_service
from app.utils.security import generate_token

# کلیدهای مجاز پاسخ درگاه (جلوگیری از ذخیره اطلاعات حساس کارت)
SAFE_RESPONSE_KEYS = {
    "status",
    "amount",
    "trans_id",
    "id_get",
    "factorId",
    "reference",
    "ref_code",
    "message",
    "date",
    "card_holder",
}


@dataclass
class GatewayResult:
    ok: bool
    message: str = ""
    token: str | None = None
    redirect_url: str | None = None
    raw: dict = field(default_factory=dict)


def is_test_mode() -> bool:
    return settings_service.get_bool("bitpay_test_mode", False)


def is_enabled() -> bool:
    if not settings_service.get_bool("payment_online_enabled", True):
        return False
    if not settings_service.get_bool("bitpay_enabled", False):
        return False
    if is_test_mode():
        return True
    return bool(settings_service.get_str("bitpay_api_key"))


def amount_multiplier() -> int:
    try:
        return max(1, int(settings_service.get("bitpay_amount_multiplier", 10) or 10))
    except (TypeError, ValueError):
        return 10


def gateway_amount(base_amount: int) -> int:
    """مبلغ ارسالی به درگاه (تبدیل تومان به ریال در صورت نیاز)."""
    return int(base_amount) * amount_multiplier()


def _api_key() -> str:
    return settings_service.get_str("bitpay_api_key")


def _base_url() -> str:
    return settings_service.get_str("bitpay_base_url", "https://bitpay.ir").rstrip("/")


def _http_post(url: str, data: dict, timeout: int = 20) -> tuple[bool, dict | str]:
    encoded = urllib.parse.urlencode({k: v for k, v in data.items() if v is not None}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "ArminStore/1.0",
            "x-api-key": _api_key(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
                return True, payload if isinstance(payload, dict) else {"raw": payload}
            except json.JSONDecodeError:
                return False, raw[:600]
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return False, f"HTTP {exc.code}: {raw[:400]}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f"خطای ارتباط با درگاه: {exc}"


def _sanitize(payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if key in SAFE_RESPONSE_KEYS}


def request_token(payment, order, callback_url: str) -> GatewayResult:
    """درخواست توکن پرداخت از درگاه."""
    if settings_service.get_bool("bitpay_test_mode", False):
        token = f"TEST-{generate_token(20)}"
        return GatewayResult(
            ok=True,
            message="درگاه آزمایشی فعال است.",
            token=token,
            redirect_url=f"/payment/test/{token}",
        )

    if not is_enabled():
        return GatewayResult(ok=False, message="درگاه پرداخت پیکربندی نشده یا غیرفعال است.")

    path = settings_service.get_str("bitpay_token_path", "/payment/gateway-{api}-get-token")
    try:
        url = _base_url() + path.format(api=_api_key())
    except (KeyError, IndexError, ValueError):
        return GatewayResult(ok=False, message="مسیر درخواست توکن درگاه نامعتبر است.")

    data = {
        "api": _api_key(),
        "amount": gateway_amount(order.total_amount),
        "redirect": callback_url,
        "name": (order.customer_name or "مشتری")[:100],
        "email": (order.guest_email or (getattr(order.user, "email", None) if order.user else None) or "customer@example.com"),
        "description": f"پرداخت سفارش {order.order_number}"[:200],
        "order_id": order.order_number,
    }
    try:
        ok, response = _http_post(url, data)
    except Exception as exc:  # pragma: no cover - خطای غیرمنتظره شبکه
        current_app.logger.exception("bitpay token request crashed")
        return GatewayResult(ok=False, message=f"خطا در ارتباط با درگاه: {exc}")

    if not ok or not isinstance(response, dict):
        current_app.logger.warning("bitpay token request failed: %s", str(response)[:200])
        return GatewayResult(ok=False, message="ارتباط با درگاه پرداخت برقرار نشد.", raw=_sanitize(response))

    status = str(response.get("status", ""))
    token = str(response.get("token") or "").strip()
    if status == "1" and token:
        gateway_url = settings_service.get_str(
            "bitpay_gateway_url", "https://bitpay.ir/payment/gateway-{token}"
        )
        try:
            redirect_url = gateway_url.format(token=token)
        except (KeyError, IndexError, ValueError):
            return GatewayResult(ok=False, message="آدرس صفحه پرداخت درگاه نامعتبر است.", raw=_sanitize(response))
        return GatewayResult(
            ok=True,
            message="در حال انتقال به درگاه پرداخت…",
            token=token,
            redirect_url=redirect_url,
            raw=_sanitize(response),
        )

    error_message = str(response.get("message") or response.get("error") or "درگاه پرداخت پاسخ معتبری برنگرداند.")
    return GatewayResult(ok=False, message=error_message[:300], raw=_sanitize(response))


def verify(payment, params: dict) -> dict:
    """
    استعلام و تأیید تراکنش از سرور درگاه.
    بازگشتی: {verified, message, amount, trans_id, reference, raw}
    """
    trans_id = str(params.get("trans_id") or params.get("transId") or "").strip()
    id_get = str(params.get("id_get") or params.get("idGet") or "").strip()

    if settings_service.get_bool("bitpay_test_mode", False):
        return _verify_test(payment, params, trans_id)

    if not trans_id:
        return {"verified": False, "message": "شناسه تراکنش در بازگشت از درگاه وجود ندارد.", "raw": {}}

    path = settings_service.get_str(
        "bitpay_verify_path", "/payment/verify-{api}/{trans_id}/{id_get}"
    )
    try:
        url = _base_url() + path.format(api=_api_key(), trans_id=trans_id, id_get=id_get or payment.authority_or_token or "")
    except (KeyError, IndexError, ValueError):
        return {"verified": False, "message": "مسیر تأیید تراکنش درگاه نامعتبر است.", "raw": {}}

    ok, response = _http_post(url, {})
    if not ok or not isinstance(response, dict):
        current_app.logger.warning("bitpay verify failed: %s", str(response)[:200])
        return {
            "verified": False,
            "message": "استعلام تراکنش از درگاه ناموفق بود. لطفاً وضعیت سفارش را بعداً بررسی کنید.",
            "raw": _sanitize(response),
        }

    status = str(response.get("status", ""))
    safe = _sanitize(response)
    if status != "1":
        return {
            "verified": False,
            "message": str(response.get("message") or "تراکنش توسط درگاه تأیید نشد.")[:300],
            "raw": safe,
        }

    try:
        paid_amount = int(str(response.get("amount", "0")).strip() or 0)
    except (TypeError, ValueError):
        paid_amount = 0

    return {
        "verified": True,
        "message": "تراکنش تأیید شد.",
        "amount": paid_amount,
        "trans_id": trans_id,
        "reference": str(response.get("reference") or response.get("ref_code") or trans_id)[:120],
        "raw": safe,
    }


# --- درگاه آزمایشی داخلی (فقط برای تست فرآیند؛ پرداخت واقعی انجام نمی‌شود) ---

def test_signature(payment) -> str:
    import hashlib
    import hmac

    secret = current_app.config["SECRET_KEY"].encode()
    return hmac.new(secret, f"testpay:{payment.id}:{payment.authority_or_token}".encode(), hashlib.sha256).hexdigest()[:32]


def _verify_test(payment, params: dict, trans_id: str) -> dict:
    import hashlib
    import hmac

    if not trans_id:
        return {"verified": False, "message": "درگاه آزمایشی: شناسه تراکنش یافت نشد.", "raw": {}}
    signature = str(params.get("sig") or "")
    secret = current_app.config["SECRET_KEY"].encode()
    expected = hmac.new(
        secret, f"testpay:{payment.id}:{payment.authority_or_token}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    if not hmac.compare_digest(signature, expected):
        return {"verified": False, "message": "درگاه آزمایشی: امضای بازگشت نامعتبر است.", "raw": {}}
    try:
        amount = int(params.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    return {
        "verified": True,
        "message": "پرداخت آزمایشی تأیید شد.",
        "amount": amount,
        "trans_id": trans_id,
        "reference": f"TEST-{trans_id[:12]}",
        "raw": {"status": "1", "mode": "test"},
    }


def new_test_token() -> str:
    return f"TEST-{secrets.token_hex(10)}"
