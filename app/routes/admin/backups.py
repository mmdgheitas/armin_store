"""پشتیبان‌گیری و بازیابی دیتابیس از پنل مدیریت."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from flask import (
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from app.decorators import permission_required
from app.models import BackupLog
from app.routes.admin import admin_bp
from app.routes.admin.helpers import log_admin
from app.services import backup_service, settings_service
from app.utils.dates import utcnow


@admin_bp.get("/backups")
@permission_required("backups.manage")
def backups():
    items = backup_service.list_backups()
    logs = (
        BackupLog.query.order_by(BackupLog.created_at.desc()).limit(25).all()
    )
    from flask import current_app

    db_path = Path(current_app.config["DATABASE_PATH"])
    try:
        db_size = db_path.stat().st_size
    except OSError:
        db_size = 0
    return render_template(
        "admin/backups.html",
        backups=items,
        logs=logs,
        db_size=db_size,
        keep=settings_service.get_int("backup_keep_count", 10),
    )


@admin_bp.post("/backups/create")
@permission_required("backups.manage")
def backup_create():
    try:
        result = backup_service.create_backup(
            admin_id=g.user.id, note=request.form.get("note", "بکاپ دستی")
        )
        log_admin("backup.create", "backup", new={"filename": result["filename"]})
        flash(f"بکاپ «{result['filename']}» ساخته شد.", "success")
    except backup_service.BackupError as exc:
        flash(exc.message, "error")
    return redirect(url_for("admin.backups"))


@admin_bp.get("/backups/download/<path:filename>")
@permission_required("backups.manage")
def backup_download(filename: str):
    path = backup_service.get_backup_file(filename)
    if path is None:
        abort(404)
    log_admin("backup.download", "backup", new={"filename": filename})
    return send_file(path, as_attachment=True, download_name=path.name)


@admin_bp.post("/backups/delete")
@permission_required("backups.manage")
def backup_delete():
    filename = request.form.get("filename", "")
    ok, message = backup_service.delete_backup(filename, admin_id=g.user.id)
    if ok:
        log_admin("backup.delete", "backup", new={"filename": filename})
    flash(message, "success" if ok else "error")
    return redirect(url_for("admin.backups"))


@admin_bp.post("/backups/restore")
@permission_required("backups.manage")
def backup_restore():
    filename = request.form.get("filename", "")
    if request.form.get("confirm") != "1":
        flash("برای بازیابی باید تأیید نهایی را فعال کنید.", "error")
        return redirect(url_for("admin.backups"))
    ok, message = backup_service.restore_backup(filename, admin_id=g.user.id)
    if ok:
        log_admin("backup.restore", "backup", new={"filename": filename})
    flash(message, "success" if ok else "error")
    return redirect(url_for("admin.backups"))


@admin_bp.post("/backups/upload")
@permission_required("backups.manage")
def backup_upload():
    file = request.files.get("file")
    if file is None or not file.filename:
        flash("فایل بکاپ را انتخاب کنید.", "error")
        return redirect(url_for("admin.backups"))
    if not file.filename.lower().endswith(".db"):
        flash("فقط فایل بکاپ با پسوند .db مجاز است.", "error")
        return redirect(url_for("admin.backups"))

    target_name = f"uploaded-{utcnow().strftime('%Y%m%d-%H%M%S')}.db"
    target = backup_service.backups_dir() / target_name
    try:
        file.save(target)
        os.chmod(target, 0o600)
        info = backup_service.validate_backup(target)
    except backup_service.BackupError as exc:
        target.unlink(missing_ok=True)
        flash(exc.message, "error")
        return redirect(url_for("admin.backups"))
    except OSError:
        target.unlink(missing_ok=True)
        flash("ذخیره فایل بکاپ ناموفق بود.", "error")
        return redirect(url_for("admin.backups"))

    flash(
        f"فایل بکاپ معتبر است (نسخه {info['version']}، کاربران: {info['counts'].get('users', 0)}). "
        "برای بازیابی دکمه «بازیابی» را بزنید.",
        "success",
    )
    log_admin("backup.upload", "backup", new={"filename": target_name})
    return redirect(url_for("admin.backups"))
