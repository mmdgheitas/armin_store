"""Setup Wizard: نصب اولیه از مرورگر."""
from __future__ import annotations

import os

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.services import setup_service

setup_bp = Blueprint("setup", __name__, url_prefix="/setup")


def _guard():
    if setup_service.is_installed():
        return redirect(url_for("storefront.home"))
    return None


@setup_bp.get("/")
def index():
    blocked = _guard()
    if blocked:
        return blocked
    checks = setup_service.environment_checks()
    key = setup_service.install_key(create=True)
    env_key = (os.environ.get("SETUP_KEY") or "").strip()
    return render_template(
        "setup/index.html",
        checks=checks,
        required_ok=setup_service.required_checks_passed(checks),
        install_key_hint=("کلید نصب از متغیر محیطی SETUP_KEY خوانده می‌شود." if env_key else
                          "کلید نصب در فایل instance/install_key.txt قرار دارد."),
        key_available=bool(key),
        python_version=__import__("platform").python_version(),
    )


@setup_bp.post("/install")
def install():
    blocked = _guard()
    if blocked:
        return blocked
    checks = setup_service.environment_checks()
    if not setup_service.required_checks_passed(checks):
        flash("پیش‌نیازهای نصب کامل نیست. موارد قرمز را برطرف کنید.", "error")
        return redirect(url_for("setup.index"))

    form = request.form.to_dict()
    env_key = (os.environ.get("SETUP_KEY") or "").strip()
    if env_key and not form.get("install_key"):
        form["install_key"] = env_key
    if "include_demo" in form:
        form["include_demo"] = True
    if "force_https" in form:
        form["force_https"] = True

    ok, message = setup_service.install(form)
    if not ok:
        flash(message, "error")
        return render_template(
            "setup/index.html",
            checks=setup_service.environment_checks(),
            required_ok=True,
            install_key_hint="کلید نصب در فایل instance/install_key.txt قرار دارد.",
            key_available=True,
            form=form,
            python_version=__import__("platform").python_version(),
        )

    flash("نصب با موفقیت انجام شد. اکنون با حساب مدیر کل وارد شوید.", "success")
    return redirect(url_for("auth.login"))


@setup_bp.get("/finish")
def finish():
    if not setup_service.is_installed():
        return redirect(url_for("setup.index"))
    return render_template("setup/finish.html")
