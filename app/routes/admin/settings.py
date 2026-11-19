"""تنظیمات فروشگاه به تفکیک گروه (بدون نیاز به کد)."""
from __future__ import annotations

from flask import abort, flash, g, redirect, render_template, request, url_for

from app.decorators import permission_required
from app.extensions import db
from app.routes.admin import admin_bp
from app.routes.admin.helpers import log_admin
from app.services import media_service, settings_service
from app.utils.files import FileValidationError
from app.utils.security import mask_secret

GROUP_PERMISSIONS = {
    "general": ("settings.manage",),
    "currency": ("settings.manage",),
    "commerce": ("settings.manage",),
    "upload": ("settings.manage",),
    "auth": ("settings.manage",),
    "security": ("settings.manage",),
    "backup": ("backups.manage",),
    "seo": ("seo.manage",),
    "support": ("settings.manage",),
    "payment": ("payment_settings.manage",),
    "sms": ("sms_settings.manage",),
}


def _coerce(defn, form) -> object:
    if defn.type == "bool":
        return defn.key in form
    raw = form.get(defn.key)
    if raw is None:
        return defn.default
    raw = raw.strip()
    if defn.type == "int":
        try:
            return int(raw or 0)
        except ValueError:
            return defn.default
    return raw


@admin_bp.route("/settings/<group>", methods=["GET", "POST"])
@permission_required(
    "settings.manage",
    "payment_settings.manage",
    "sms_settings.manage",
    "seo.manage",
    "backups.manage",
    any_of=True,
)
def settings_group(group: str):
    if group not in settings_service.GROUPS:
        abort(404)
    required = GROUP_PERMISSIONS.get(group, ("settings.manage",))
    keys = g.user.permission_keys
    if "*" not in keys and not any(perm in keys for perm in required):
        abort(403)

    defs = settings_service.group_definitions(group)
    if request.method == "POST":
        updates = {}
        for defn in defs:
            if defn.sensitive:
                raw = (request.form.get(defn.key) or "").strip()
                if raw and not raw.startswith("•"):
                    updates[defn.key] = raw
                continue
            updates[defn.key] = _coerce(defn, request.form)
        settings_service.set_many(updates)
        log_admin(
            "settings.update",
            "settings",
            new={"group": group, "keys": list(updates.keys())},
        )
        flash("تنظیمات ذخیره شد.", "success")
        return redirect(url_for("admin.settings_group", group=group))

    values = settings_service.load_all(force=True)
    display_values = {}
    for defn in defs:
        value = values.get(defn.key)
        if defn.sensitive and value:
            display_values[defn.key] = mask_secret(str(value), visible=4)
        else:
            display_values[defn.key] = value
    return render_template(
        "admin/settings.html",
        group=group,
        group_title=settings_service.GROUPS.get(group, group),
        groups=settings_service.GROUPS,
        defs=defs,
        values=display_values,
        raw_values=values,
    )


@admin_bp.post("/settings/upload-branding")
@permission_required("settings.manage")
def upload_branding():
    kind = request.form.get("kind", "logo")
    file = request.files.get("file")
    if file is None or not file.filename:
        flash("فایلی انتخاب نشده است.", "error")
        return redirect(url_for("admin.settings_group", group="general"))
    try:
        saved = media_service.save_upload(file, "branding", kind="image", max_mb=2)
    except FileValidationError as exc:
        flash(exc.message, "error")
        return redirect(url_for("admin.settings_group", group="general"))
    key = "store_favicon" if kind == "favicon" else "store_logo"
    settings_service.set_many({key: saved["path"]})
    log_admin("settings.branding", "settings", new={"key": key, "path": saved["path"]})
    flash("فایل بارگذاری و ذخیره شد.", "success")
    return redirect(url_for("admin.settings_group", group="general"))


@admin_bp.get("/system/status")
@permission_required("dashboard.view")
def system_status():
    from app.database.migrator import schema_status
    from app.services import setup_service

    return render_template(
        "admin/system_status.html",
        checks=setup_service.environment_checks(),
        schema=schema_status(),
    )
