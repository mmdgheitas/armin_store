from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, g, redirect, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

from app.config import Config
from app.db import close_db, get_db, run_migrations
from app.repositories.catalog import list_categories
from app.repositories.cms import find_redirect, list_pages
from app.repositories.settings import get_all_settings
from app.services.auth import load_current_user
from app.services.cart import build_cart_summary
from app.utils.persian import (
    format_rial,
    format_toman,
    to_jalali,
    to_jalali_date,
    to_persian_digits,
)

csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

ASSET_VERSION = "20261119"


def _configure_logging(app: Flask) -> None:
    if app.config.get("TESTING"):
        return
    log_dir = Path(app.config["LOG_FOLDER"])
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    app_handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    app_handler.setFormatter(fmt)
    app_handler.setLevel(logging.INFO)

    sec_handler = RotatingFileHandler(
        log_dir / "security.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    sec_handler.setFormatter(fmt)
    sec_handler.setLevel(logging.INFO)

    root_logger = logging.getLogger("armin_store")
    root_logger.setLevel(logging.INFO)
    if not root_logger.handlers:
        root_logger.addHandler(app_handler)

    sec_logger = logging.getLogger("armin_store.security")
    sec_logger.setLevel(logging.INFO)
    if not sec_logger.handlers:
        sec_logger.addHandler(sec_handler)


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config.from_object(config_class)

    if app.config.get("IS_PROD") and not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY must be set in production environment.")

    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    _configure_logging(app)

    run_migrations(app.config["DATABASE_PATH"])

    csrf.init_app(app)
    limiter.init_app(app)
    app.teardown_appcontext(close_db)

    # Register blueprints
    from app.blueprints.account import account_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.cart import cart_bp
    from app.blueprints.checkout import checkout_bp
    from app.blueprints.payment import payment_bp
    from app.blueprints.shop import shop_bp

    app.register_blueprint(shop_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(cart_bp)
    app.register_blueprint(checkout_bp)
    app.register_blueprint(payment_bp)
    app.register_blueprint(admin_bp, url_prefix=app.config["ADMIN_URL_PREFIX"])

    # Jinja filters
    app.jinja_env.filters["toman"] = lambda v: format_toman(v, with_unit=True)
    app.jinja_env.filters["toman_num"] = lambda v: format_toman(v, with_unit=False)
    app.jinja_env.filters["rial"] = format_rial
    app.jinja_env.filters["jalali"] = to_jalali
    app.jinja_env.filters["jalali_date"] = to_jalali_date
    app.jinja_env.filters["fa_digits"] = to_persian_digits
    app.jinja_env.filters["to_json"] = lambda v: json.dumps(v, ensure_ascii=False)

    @app.before_request
    def _before_request():
        if request.path.startswith("/static/"):
            return None
        conn = get_db()
        load_current_user(conn)

        # Handle dynamic 301/302 redirects for GET requests
        if request.method == "GET":
            decoded_path = unquote(request.path)
            r_row = find_redirect(conn, decoded_path) or find_redirect(conn, request.path)
            if r_row:
                return redirect(r_row["new_path"], code=int(r_row["status_code"]))
        return None

    @app.after_request
    def _security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if app.config.get("IS_PROD"):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            frame_ancestors = "frame-ancestors 'self';"
        else:
            frame_ancestors = ""
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data: blob: https:; "
            "media-src 'self' blob:; "
            "frame-src 'self' https://www.aparat.com https://aparat.com https://www.youtube.com https://www.youtube-nocookie.com; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
            f"{frame_ancestors}"
        ).strip()
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=2592000"
        return response

    @app.context_processor
    def _inject_globals():
        if request.path.startswith("/static/"):
            return {}
        conn = get_db()
        settings = get_all_settings(conn)
        nav_categories = list_categories(conn, active_only=True)
        footer_pages = list_pages(conn, active_only=True)
        uid = int(g.current_user["id"]) if getattr(g, "current_user", None) else None
        cart_summary = build_cart_summary(conn, user_id=uid)
        return {
            "site": settings,
            "nav_categories": nav_categories,
            "footer_pages": footer_pages,
            "current_user": getattr(g, "current_user", None),
            "cart_count": cart_summary.item_count,
            "admin_prefix": app.config["ADMIN_URL_PREFIX"],
            "asset_v": ASSET_VERSION,
        }

    # Error handlers
    @app.errorhandler(403)
    def _err_403(_e):
        return render_template("errors/403.html", noindex=True), 403

    @app.errorhandler(404)
    def _err_404(_e):
        return render_template("errors/404.html", noindex=True), 404

    @app.errorhandler(410)
    def _err_410(_e):
        return render_template("errors/410.html", noindex=True), 410

    @app.errorhandler(500)
    def _err_500(_e):
        return render_template("errors/500.html", noindex=True), 500

    # CLI commands
    @app.cli.command("expire-orders")
    def _cli_expire_orders():
        from app.services.inventory import expire_unpaid_orders

        conn = get_db()
        n = expire_unpaid_orders(conn)
        print(f"Expired {n} unpaid orders.")

    @app.cli.command("process-sms")
    def _cli_process_sms():
        from app.services.smsir import process_sms_queue

        conn = get_db()
        n = process_sms_queue(conn, limit=50)
        print(f"Processed {n} SMS queue entries.")

    return app
