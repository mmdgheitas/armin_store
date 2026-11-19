"""ابزارهای HTTP: پاسخ JSON استاندارد، IP کلاینت، ریدایرکت امن."""
from __future__ import annotations

from urllib.parse import urlparse

from flask import jsonify, request


def wants_json() -> bool:
    if request.path.startswith("/api/"):
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "application/json" and request.accept_mimetypes[best] > 0


def json_error(message: str, status: int = 400, errors: dict | None = None):
    payload = {"success": False, "message": message}
    if errors:
        payload["errors"] = errors
    return jsonify(payload), status


def json_ok(message: str = "", status: int = 200, **extra):
    payload = {"success": True, "message": message}
    payload.update(extra)
    return jsonify(payload), status


def client_ip() -> str:
    """IP کلاینت با احترام به هدرهای پروکسی معتبر هاست اشتراکی."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()[:64]
    return (request.remote_addr or "0.0.0.0")[:64]


def is_safe_redirect(target: str | None) -> bool:
    """جلوگیری از Open Redirect."""
    if not target:
        return False
    if target.startswith("/") and not target.startswith("//") and not target.startswith("/\\"):
        return True
    host_url = urlparse(request.host_url)
    parsed = urlparse(target)
    return parsed.scheme in {"http", "https"} and parsed.netloc == host_url.netloc


def safe_next_url(default: str = "/") -> str:
    target = request.values.get("next") or request.args.get("next")
    return target if is_safe_redirect(target) else default
