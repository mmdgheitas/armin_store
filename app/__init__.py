"""Application Factory — ساخت و پیکربندی برنامه Flask."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, g, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from app.extensions import db
from config import Config, get_config


def create_app(config_name: str | None = None, test_config: dict | None = None) -> Flask:
    base_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        static_folder=str(base_dir / "static"),
        template_folder=str(base_dir / "templates"),
        instance_path=str(Path(Config.INSTANCE_DIR)),
        instance_relative_config=False,
    )
    config_object = get_config(config_name)
    app.config.from_object(config_object)
    if test_config:
        app.config.update(test_config)

    if str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).endswith(":memory:"):
        from sqlalchemy.pool import StaticPool

        app.config.setdefault(
            "SQLALCHEMY_ENGINE_OPTIONS",
            {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}},
        )

    Config.ensure_directories()
    _configure_logging(app)
    _configure_proxy(app)

    db.init_app(app)
    _configure_sqlite(app)

    from app.routes.account import account_bp
    from app.routes.admin import admin_bp
    from app.routes.api import api_bp
    from app.routes.auth import auth_bp
    from app.routes.cart import cart_bp
    from app.routes.checkout import checkout_bp
    from app.routes.media import media_bp
    from app.routes.payment import payment_bp
    from app.routes.seo import seo_bp
    from app.routes.setup import setup_bp
    from app.routes.storefront import storefront_bp

    app.register_blueprint(storefront_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(cart_bp)
    app.register_blueprint(checkout_bp)
    app.register_blueprint(payment_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(seo_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(setup_bp)

    _register_template_helpers(app)
    _register_hooks(app)
    _register_error_handlers(app)
    _register_healthcheck(app)
    _register_commands(app)
    return app


def _configure_logging(app: Flask) -> None:
    if app.config.get("TESTING"):
        return
    log_dir = Path(app.config["LOG_DIR"])
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "app.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        handler.setLevel(getattr(logging, str(app.config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO))
        if not any(isinstance(h, RotatingFileHandler) for h in app.logger.handlers):
            app.logger.addHandler(handler)
    except OSError:
        pass
    app.logger.setLevel(logging.INFO)


def _configure_proxy(app: Flask) -> None:
    """پشت Passenger/وب‌سرور، پروتکل و دامنه واقعی از هدرهای پروکسی خوانده می‌شود."""
    if app.config.get("TRUST_PROXY_HEADERS", True):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=1, x_port=0, x_prefix=1)


def _configure_sqlite(app: Flask) -> None:
    """فعال‌سازی کلید خارجی، WAL و مهلت قفل در همه اتصال‌ها."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    with app.app_context():
        try:
            engine = db.engine
        except Exception:  # pragma: no cover
            return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # pragma: no cover - وابسته به درایور
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=20000")
            if ":memory:" not in str(app.config.get("SQLALCHEMY_DATABASE_URI", "")):
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
        except Exception:
            pass

    app.config.setdefault("SQLALCHEMY_ENGINE", Engine)


def _register_template_helpers(app: Flask) -> None:
    from app.constants import (
        ORDER_STATUS_LABELS,
        PAYMENT_STATUS_LABELS,
        SHIPPING_STATUS_LABELS,
        IRAN_PROVINCES,
    )
    from app.security import csrf_token
    from app.services import settings_service
    from app.utils.dates import (
        humanize_delta,
        jalali_date_string,
        jalali_datetime_string,
        jalali_long_datetime,
    )
    from app.utils.files import human_size
    from app.utils.money import format_money, format_number, to_persian_digits

    @app.template_filter("money")
    def money_filter(amount, with_label=True):
        return settings_service.money(amount, with_label=with_label)

    @app.template_filter("number")
    def number_filter(value):
        return settings_service.number(value)

    @app.template_filter("jdate")
    def jdate_filter(value):
        return jalali_date_string(value)

    @app.template_filter("jdatetime")
    def jdatetime_filter(value):
        return jalali_datetime_string(value)

    @app.template_filter("jlong")
    def jlong_filter(value):
        return jalali_long_datetime(value)

    @app.template_filter("timeago")
    def timeago_filter(value):
        return humanize_delta(value)

    @app.template_filter("filesize")
    def filesize_filter(value):
        return human_size(value)

    @app.template_filter("digits")
    def digits_filter(value):
        return to_persian_digits(value)

    @app.template_filter("nl2br")
    def nl2br_filter(value):
        from markupsafe import Markup, escape

        if not value:
            return ""
        return Markup("<br>".join(escape(str(value)).split("\n")))

    @app.template_filter("intcomma")
    def intcomma_filter(value):
        return format_number(value or 0, persian=False)

    @app.template_global("csrf_token")
    def csrf_token_global():
        return csrf_token()

    from app.services.digital_delivery_service import download_url as _download_url
    from app.services.media_service import public_url as _media_public_url

    app.jinja_env.globals.update(
        media_url=lambda path: _media_public_url(path) if path else "",
        download_url=_download_url,
        order_status_labels=ORDER_STATUS_LABELS,
        payment_status_labels=PAYMENT_STATUS_LABELS,
        shipping_status_labels=SHIPPING_STATUS_LABELS,
        provinces=IRAN_PROVINCES,
        format_money=lambda amount, with_label=True: format_money(
            amount,
            unit=settings_service.get_str("currency_unit", "toman"),
            label=settings_service.get_str("currency_label", "تومان"),
            persian=settings_service.get_bool("display_persian_digits", True),
            custom_ratio=settings_service.get_int("currency_ratio", 1),
            with_label=with_label,
        ),
    )


def _register_hooks(app: Flask) -> None:
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from app.security import apply_security_headers, validate_csrf
    from app.services import auth_service, settings_service

    @app.before_request
    def _prepare_request():
        # مسیرهای ایستا و فایل‌های عمومی نیازی به بوت‌استرپ کامل ندارند
        if request.endpoint in {"static", "media.public_file", "healthcheck"}:
            return None

        g.settings = settings_service.load_all(force=True)
        g.user = auth_service.get_user(session.get("user_id"))
        if g.user is None and "user_id" in session:
            session.pop("user_id", None)

        from app.services.setup_service import is_installed

        installed = is_installed()
        g.installed = installed
        public_endpoints = {
            "setup.index",
            "setup.install",
            "setup.check",
            "setup.finish",
            "seo.robots_txt",
            "seo.sitemap_xml",
        }
        if not installed and request.endpoint not in public_endpoints:
            return redirect(url_for("setup.index"))

        # هدایت به HTTPS در محیط عملیاتی
        if (
            installed
            and not app.config.get("TESTING")
            and settings_service.get_bool("force_https", True)
            and not request.is_secure
            and request.headers.get("X-Forwarded-Proto", "") != "https"
            and request.host.split(":")[0] not in {"localhost", "127.0.0.1", "0.0.0.0"}
        ):
            secure_url = request.url.replace("http://", "https://", 1)
            return redirect(secure_url, code=301)

        try:
            validate_csrf()
        except HTTPException:
            raise
        except Exception:
            from flask import abort

            abort(400, description="اعتبارسنجی امنیتی فرم انجام نشد.")

        # حالت تعمیرات: فروشگاه برای مشتری بسته، پنل مدیریت باز
        if (
            installed
            and settings_service.get_bool("maintenance_mode", False)
            and not request.path.startswith(("/admin", "/setup", "/media", "/static", "/health"))
            and not (getattr(g, "user", None) and g.user.is_admin)
            and request.endpoint not in public_endpoints
        ):
            return (
                render_template(
                    "errors/maintenance.html",
                    message=settings_service.get_str("maintenance_message"),
                ),
                503,
            )

        # پاک‌سازی دوره‌ای رزروهای منقضی (Lazy Expiration بدون Worker)
        if installed and (not app.config.get("TESTING") or app.config.get("ENABLE_LAZY_EXPIRY")):
            try:
                from app.services.order_service import sweep_expired_reservations

                sweep_expired_reservations(min_interval=60)
            except (OperationalError, ProgrammingError):
                db.session.rollback()
            except Exception:
                db.session.rollback()
                app.logger.exception("lazy expiry failed")

        return None

    @app.after_request
    def _after_request(response):
        return apply_security_headers(response)

    @app.teardown_request
    def _teardown(exception=None):
        if exception is not None:
            try:
                db.session.rollback()
            except Exception:  # pragma: no cover
                pass

    @app.context_processor
    def _inject_globals():
        from app.models import Category
        from app.services import settings_service

        values = getattr(g, "settings", None) or settings_service.load_all()
        user = getattr(g, "user", None)
        nav_categories = getattr(g, "_nav_categories", None)
        if nav_categories is None:
            try:
                nav_categories = (
                    Category.query.filter(Category.is_active.is_(True), Category.parent_id.is_(None))
                    .order_by(Category.sort_order, Category.id)
                    .limit(12)
                    .all()
                )
            except Exception:
                nav_categories = []
            g._nav_categories = nav_categories

        footer_pages = getattr(g, "_footer_pages", None)
        if footer_pages is None:
            try:
                from app.models import Page

                footer_pages = (
                    Page.query.filter(Page.is_published.is_(True), Page.show_in_footer.is_(True))
                    .order_by(Page.sort_order, Page.title)
                    .limit(10)
                    .all()
                )
            except Exception:
                footer_pages = []
            g._footer_pages = footer_pages

        cart_count = 0
        cart_total = 0
        if request.endpoint not in (None, "static"):
            try:
                from app.services import cart_service

                summary = cart_service.summary()
                cart_count = summary["count"]
                cart_total = summary["total"]
            except Exception:
                cart_count, cart_total = 0, 0

        active_ad = None
        try:
            from app.models import Advertisement

            active_ad = (
                Advertisement.query.filter(Advertisement.is_active.is_(True))
                .order_by(Advertisement.sort_order, Advertisement.id)
                .all()
            )
            active_ad = next((ad for ad in active_ad if ad.is_visible_now()), None)
        except Exception:
            active_ad = None

        return {
            "settings": values,
            "current_user": user,
            "nav_categories": nav_categories,
            "footer_pages": footer_pages,
            "cart_count": cart_count,
            "cart_total": cart_total,
            "active_ad": active_ad,
            "store_name": values.get("store_name", "فروشگاه"),
            "is_admin_area": request.path.startswith("/admin"),
        }

    @app.before_request
    def _enforce_session_lifetime():
        from datetime import timedelta

        from app.utils.dates import utcnow

        if request.endpoint == "static":
            return None
        hours = int((getattr(g, "settings", None) or {}).get("session_lifetime_hours", 168) or 168)
        app.permanent_session_lifetime = timedelta(hours=max(1, hours))
        return None


def _register_error_handlers(app: Flask) -> None:
    from app.utils.http import wants_json

    def _render_error(code: int, title: str, message: str, extra: dict | None = None):
        if wants_json():
            return {"success": False, "message": message, "error": code}, code
        return (
            render_template(
                "errors/error.html",
                code=code,
                title=title,
                message=message,
                extra=extra or {},
            ),
            code,
        )

    @app.errorhandler(400)
    def _bad_request(error):
        description = getattr(error, "description", None) or "درخواست نامعتبر است."
        return _render_error(400, "درخواست نامعتبر", description)

    @app.errorhandler(403)
    def _forbidden(error):
        return _render_error(403, "دسترسی غیرمجاز", "شما مجوز دسترسی به این بخش را ندارید.")

    @app.errorhandler(404)
    def _not_found(error):
        return _render_error(404, "صفحه پیدا نشد", "صفحه‌ای که به دنبال آن هستید وجود ندارد یا حذف شده است.")

    @app.errorhandler(405)
    def _method_not_allowed(error):
        return _render_error(405, "روش نامعتبر", "این عملیات از طریق این روش مجاز نیست.")

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(error):
        limit_mb = int(app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024))
        return _render_error(
            413,
            "حجم فایل زیاد است",
            f"حجم ارسال‌شده بیش از حد مجاز است (حداکثر {limit_mb} مگابایت). لطفاً فایل کوچک‌تری انتخاب کنید.",
        )

    @app.errorhandler(429)
    def _too_many(error):
        return _render_error(429, "تعداد درخواست زیاد", "تعداد درخواست‌های شما بیش از حد مجاز است. کمی بعد تلاش کنید.")

    @app.errorhandler(500)
    def _server_error(error):
        app.logger.exception("Internal server error: %s", error)
        try:
            db.session.rollback()
        except Exception:  # pragma: no cover
            pass
        return _render_error(500, "خطای سرور", "خطایی در پردازش درخواست رخ داد. لطفاً بعداً تلاش کنید.")

    @app.errorhandler(HTTPException)
    def _http_error(error):
        return _render_error(
            error.code or 500,
            error.name or "خطا",
            error.description or "خطایی رخ داد.",
        )

    @app.errorhandler(Exception)
    def _unhandled(error):
        app.logger.exception("Unhandled exception")
        try:
            db.session.rollback()
        except Exception:  # pragma: no cover
            pass
        return _render_error(500, "خطای سرور", "خطایی غیرمنتظره رخ داد. لطفاً بعداً تلاش کنید.")


def _register_healthcheck(app: Flask) -> None:
    @app.route("/health")
    def healthcheck():
        from flask import jsonify

        from app.services.setup_service import is_installed

        status = {"status": "ok", "installed": is_installed()}
        try:
            db.session.execute(db.text("SELECT 1"))
            status["database"] = "ok"
        except Exception:
            status["database"] = "error"
            status["status"] = "degraded"
        return jsonify(status)


def _register_commands(app: Flask) -> None:
    import click

    @app.cli.command("init-db")
    def init_db_command():
        """ساخت/به‌روزرسانی دیتابیس از طریق خط فرمان (اختیاری؛ معادل Setup Wizard)."""
        from app.database.migrator import upgrade
        from app.database.seed import seed_permissions_and_roles, seed_settings

        executed = upgrade(verbose=True)
        seed_permissions_and_roles()
        seed_settings()
        click.echo(f"Applied migrations: {executed or 'none'}")

    @app.cli.command("create-admin")
    @click.option("--name", prompt=True)
    @click.option("--mobile", prompt=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin_command(name, mobile, password):
        """ساخت مدیر کل (اختیاری؛ از Setup Wizard هم ممکن است)."""
        from app.database.seed import create_admin, seed_permissions_and_roles

        seed_permissions_and_roles()
        user = create_admin(name, mobile, password)
        click.echo(f"Admin created: {user.mobile}")
