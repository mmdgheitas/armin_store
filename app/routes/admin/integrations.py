"""تنظیمات درگاه پرداخت و پیامک با امکان تست کنترل‌شده."""
from __future__ import annotations

from flask import flash, g, redirect, render_template, request, url_for

from app.constants import SMS_EVENTS
from app.decorators import permission_required
from app.extensions import db
from app.models import Payment, SmsLog
from app.routes.admin import admin_bp
from app.routes.admin.helpers import log_admin
from app.services import bitpay_service, settings_service, sms_service
from app.services.sms_service import EVENT_ENABLED_KEYS, EVENT_PATTERN_KEYS
from app.utils.security import mask_secret

PAYMENT_KEYS = [
    "payment_online_enabled",
    "bitpay_enabled",
    "bitpay_api_key",
    "bitpay_base_url",
    "bitpay_token_path",
    "bitpay_verify_path",
    "bitpay_gateway_url",
    "bitpay_amount_multiplier",
    "bitpay_test_mode",
    "payment_timeout_minutes",
    "payment_success_note",
]

SMS_BASE_KEYS = [
    "sms_enabled",
    "sms_api_key",
    "sms_base_url",
    "sms_verify_path",
    "sms_bulk_path",
    "sms_line_number",
]


@admin_bp.route("/integrations/payment", methods=["GET", "POST"])
@permission_required("payment_settings.manage")
def payment_settings():
    defs = {d.key: d for d in settings_service.DEFINITIONS}
    if request.method == "POST":
        updates = {}
        for key in PAYMENT_KEYS:
            defn = defs[key]
            if defn.sensitive:
                raw = (request.form.get(key) or "").strip()
                if raw and not raw.startswith("•"):
                    updates[key] = raw
                continue
            if defn.type == "bool":
                updates[key] = key in request.form
            elif defn.type == "int":
                try:
                    updates[key] = int(request.form.get(key) or 0)
                except ValueError:
                    updates[key] = defn.default
            else:
                updates[key] = (request.form.get(key) or "").strip()
        settings_service.set_many(updates)
        log_admin("settings.payment", "settings", new={"keys": list(updates.keys())})
        flash("تنظیمات درگاه پرداخت ذخیره شد.", "success")
        return redirect(url_for("admin.payment_settings"))

    payments = db.session.query(Payment).order_by(Payment.created_at.desc()).limit(10).all()
    values = settings_service.load_all(force=True)
    display = dict(values)
    if values.get("bitpay_api_key"):
        display["bitpay_api_key"] = mask_secret(values["bitpay_api_key"], 4)
    return render_template(
        "admin/payment_settings.html",
        values=display,
        defs=defs,
        payments=payments,
        configured=bitpay_service.is_enabled(),
        key_present=bool(values.get("bitpay_api_key")),
        test_mode=bitpay_service.is_test_mode(),
    )


@admin_bp.route("/integrations/sms", methods=["GET", "POST"])
@permission_required("sms_settings.manage")
def sms_settings():
    defs = {d.key: d for d in settings_service.DEFINITIONS}
    if request.method == "POST":
        updates = {}
        for key, defn in defs.items():
            if not key.startswith("sms_"):
                continue
            if defn.sensitive:
                raw = (request.form.get(key) or "").strip()
                if raw and not raw.startswith("•"):
                    updates[key] = raw
                continue
            value = request.form.get(key)
            if value is not None:
                updates[key] = value
            elif defn.type == "bool" and request.form.get("_sms_form") == "1":
                updates[key] = False
        settings_service.set_many(updates)
        log_admin("settings.sms", "settings", new={"keys": list(updates.keys())})
        flash("تنظیمات پیامک ذخیره شد.", "success")
        return redirect(url_for("admin.sms_settings"))

    values = settings_service.load_all(force=True)
    display = dict(values)
    if values.get("sms_api_key"):
        display["sms_api_key"] = mask_secret(values["sms_api_key"], 4)
    logs = (
        db.session.query(SmsLog).order_by(SmsLog.created_at.desc()).limit(20).all()
    )
    return render_template(
        "admin/sms_settings.html",
        values=display,
        defs=defs,
        logs=logs,
        configured=sms_service.is_configured(),
        events=SMS_EVENTS,
        event_pattern_keys=EVENT_PATTERN_KEYS,
        event_enabled_keys=EVENT_ENABLED_KEYS,
    )


@admin_bp.post("/integrations/sms/test")
@permission_required("sms_settings.manage")
def sms_test():
    mobile = request.form.get("mobile", "")
    if not sms_service.is_configured():
        flash("ابتدا کلید API و الگوی پیامک را ذخیره کنید.", "error")
        return redirect(url_for("admin.sms_settings"))
    if not settings_service.get_str("sms_pattern_otp"):
        flash("برای ارسال پیامک آزمایشی، کد الگوی OTP را تنظیم کنید.", "error")
        return redirect(url_for("admin.sms_settings"))
    result = sms_service.send_test(mobile)
    log_admin("sms.test", "settings", new={"mobile": mobile, "ok": result.ok})
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("admin.sms_settings"))


@admin_bp.post("/integrations/payment/check")
@permission_required("payment_settings.manage")
def payment_check():
    """بررسی پیکربندی درگاه (بدون ارسال تراکنش واقعی)."""
    issues = []
    if not settings_service.get_bool("bitpay_enabled"):
        issues.append("درگاه BitPay غیرفعال است.")
    if not settings_service.get_str("bitpay_api_key") and not bitpay_service.is_test_mode():
        issues.append("کلید API درگاه ثبت نشده است.")
    for key in ("bitpay_base_url", "bitpay_token_path", "bitpay_verify_path", "bitpay_gateway_url"):
        if not settings_service.get_str(key):
            issues.append(f"مقدار «{key}» خالی است.")
    if bitpay_service.is_test_mode():
        issues.append("حالت آزمایشی فعال است؛ پرداخت واقعی انجام نمی‌شود.")
    if issues:
        for issue in issues:
            flash(issue, "warning")
    else:
        flash("پیکربندی درگاه کامل است. برای اطمینان، یک سفارش آزمایشی ثبت کنید.", "success")
    return redirect(url_for("admin.payment_settings"))
