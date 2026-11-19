"""محافظت CSRF و هدرهای امنیتی (بدون وابستگی خارجی)."""
from __future__ import annotations

from functools import wraps

from flask import abort, current_app, request, session

from app.utils.security import constant_time_equals, generate_csrf_token as _new_token

CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = _new_token()
        session[CSRF_SESSION_KEY] = token
    return token


def regenerate_csrf_token() -> str:
    token = _new_token()
    session[CSRF_SESSION_KEY] = token
    return token


def csrf_exempt(view):
    """علامت‌گذاری ویوهایی که از سامانه‌های خارجی فراخوانی می‌شوند (Callback درگاه)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        return view(*args, **kwargs)

    wrapper._csrf_exempt = True
    return wrapper


def _is_exempt() -> bool:
    view = current_app.view_functions.get(request.endpoint or "")
    return bool(view and getattr(view, "_csrf_exempt", False))


def _submitted_token() -> str:
    token = request.form.get(CSRF_FORM_FIELD) or request.headers.get(CSRF_HEADER, "")
    if not token and request.is_json:
        payload = request.get_json(silent=True) or {}
        token = payload.get(CSRF_FORM_FIELD, "")
    return token or ""


def validate_csrf() -> None:
    """اجرای اعتبارسنجی CSRF برای متدهای تغییردهنده داده."""
    if not current_app.config.get("WTF_CSRF_ENABLED", True):
        return
    if request.method in SAFE_METHODS or _is_exempt():
        return
    expected = session.get(CSRF_SESSION_KEY)
    submitted = _submitted_token()
    if not expected or not submitted or not constant_time_equals(expected, submitted):
        abort(400, description="اعتبارسنجی امنیتی فرم انجام نشد. صفحه را دوباره بارگذاری کنید.")


def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: https:; media-src 'self' https:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; connect-src 'self'; frame-src 'self' https:; "
        "base-uri 'self'; form-action 'self' https://bitpay.ir https://www.bitpay.ir",
    )
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    if request.path.startswith(("/admin", "/account", "/checkout", "/payment")):
        response.headers.setdefault("Cache-Control", "no-store, private")
    return response
