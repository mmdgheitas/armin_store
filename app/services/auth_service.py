"""سرویس احراز هویت: ثبت‌نام، ورود، خروج و اتصال سفارش مهمان."""
from __future__ import annotations

from datetime import timedelta

from flask import current_app, session
from sqlalchemy import or_

from app.extensions import db
from app.models.order import Order
from app.models.user import User
from app.services import rate_limit_service, settings_service
from app.utils.dates import utcnow
from app.utils.security import (
    hash_password,
    is_valid_mobile,
    normalize_mobile,
    validate_password_strength,
    verify_password,
)

SESSION_USER_KEY = "user_id"


def get_user(user_id) -> User | None:
    if not user_id:
        return None
    user = db.session.get(User, int(user_id))
    if user is None or not user.is_active or user.is_deleted:
        return None
    return user


def login_user(user: User, ip: str | None = None) -> None:
    """ورود امن: جلوگیری از Session Fixation با بازسازی نشست (سبد خرید حفظ می‌شود)."""
    cart = session.get("cart")
    compare = session.get("compare")
    session.clear()
    if cart:
        session["cart"] = cart
    if compare:
        session["compare"] = compare
    session.permanent = True
    hours = max(1, settings_service.get_int("session_lifetime_hours", 168))
    current_app.permanent_session_lifetime = timedelta(hours=hours)
    session[SESSION_USER_KEY] = user.id
    session["auth_at"] = utcnow().isoformat()
    session["user_agent_hash"] = None

    user.last_login_at = utcnow()
    user.last_login_ip = (ip or "")[:64] or None
    user.failed_login_count = 0
    user.locked_until = None
    db.session.commit()


def logout_user() -> None:
    session.pop(SESSION_USER_KEY, None)
    session.pop("auth_at", None)


def authenticate(mobile: str, password: str, ip: str | None = None) -> tuple[User | None, str | None]:
    """ورود با موبایل و رمز عبور، همراه با محدودیت نرخ و قفل حساب."""
    mobile = normalize_mobile(mobile)
    if not is_valid_mobile(mobile):
        return None, "شماره موبایل معتبر نیست."
    if not password:
        return None, "رمز عبور را وارد کنید."

    max_per_ip = max(5, settings_service.get_int("max_login_attempts_per_ip", 20))
    allowed_ip, _, retry_ip = rate_limit_service.consume(f"login_ip:{ip or '-'}", max_per_ip, 3600)
    if not allowed_ip:
        return None, f"تعداد تلاش‌های ورود از این شبکه زیاد است. {max(1, retry_ip // 60)} دقیقه دیگر تلاش کنید."

    user = db.session.query(User).filter(User.mobile == mobile).first()
    max_attempts = max(3, settings_service.get_int("login_max_attempts", 5))
    lock_minutes = max(1, settings_service.get_int("login_lock_minutes", 15))

    generic_error = "شماره موبایل یا رمز عبور اشتباه است."

    if user is None:
        rate_limit_service.consume(f"login_fail:{mobile}", max_attempts, lock_minutes * 60)
        return None, generic_error

    if user.is_deleted or not user.is_active:
        return None, "این حساب غیرفعال شده است. با پشتیبانی تماس بگیرید."

    if user.is_locked:
        remaining = int((user.locked_until - utcnow()).total_seconds() // 60) + 1
        return None, f"حساب شما به دلیل تلاش‌های ناموفق موقتاً قفل شده است. {remaining} دقیقه دیگر تلاش کنید."

    limited, retry = rate_limit_service.is_limited(f"login_fail:{mobile}", max_attempts, lock_minutes * 60)
    if limited:
        user.locked_until = utcnow() + timedelta(minutes=lock_minutes)
        db.session.commit()
        return None, f"حساب موقتاً قفل شد. {lock_minutes} دقیقه دیگر تلاش کنید."

    if user.password_hash and verify_password(user.password_hash, password):
        rate_limit_service.reset(f"login_fail:{mobile}")
        login_user(user, ip=ip)
        return user, None

    rate_limit_service.consume(f"login_fail:{mobile}", max_attempts, lock_minutes * 60)
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= max_attempts:
        user.locked_until = utcnow() + timedelta(minutes=lock_minutes)
        user.failed_login_count = 0
        db.session.commit()
        return None, f"حساب شما موقتاً قفل شد. {lock_minutes} دقیقه دیگر تلاش کنید."
    db.session.commit()
    return None, generic_error


def register_user(
    full_name: str,
    mobile: str,
    password: str,
    password_confirm: str,
    email: str | None = None,
) -> tuple[User | None, list[str]]:
    errors: list[str] = []
    full_name = (full_name or "").strip()
    mobile = normalize_mobile(mobile)
    email = (email or "").strip() or None

    if len(full_name) < 3:
        errors.append("نام و نام خانوادگی را کامل وارد کنید.")
    if not is_valid_mobile(mobile):
        errors.append("شماره موبایل باید با ۰۹ شروع شده و ۱۱ رقم باشد.")
    min_length = max(6, settings_service.get_int("password_min_length", 8))
    errors.extend(validate_password_strength(password, min_length))
    if password != password_confirm:
        errors.append("تکرار رمز عبور مطابقت ندارد.")
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        errors.append("ایمیل وارد شده معتبر نیست.")
    if mobile and db.session.query(User.id).filter(User.mobile == mobile).first():
        errors.append("این شماره موبایل قبلاً ثبت شده است. وارد شوید یا رمز عبور را بازیابی کنید.")

    if errors:
        return None, errors

    user = User(
        full_name=full_name,
        mobile=mobile,
        email=email,
        password_hash=hash_password(password),
        is_verified=False,
        is_active=True,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.session.add(user)
    db.session.commit()
    link_guest_orders(user)
    return user, []


def get_or_create_user_by_mobile(mobile: str, full_name: str = "") -> tuple[User | None, str | None]:
    """ورود/ثبت‌نام با OTP: اگر کاربر وجود نداشت، حساب جدید ساخته می‌شود."""
    mobile = normalize_mobile(mobile)
    if not is_valid_mobile(mobile):
        return None, "شماره موبایل معتبر نیست."
    user = db.session.query(User).filter(User.mobile == mobile).first()
    if user is not None:
        if user.is_deleted or not user.is_active:
            return None, "این حساب غیرفعال شده است."
        user.is_verified = True
        db.session.commit()
        return user, None

    if not settings_service.get_bool("allow_registration", True):
        return None, "ثبت‌نام در حال حاضر غیرفعال است. با پشتیبانی تماس بگیرید."

    user = User(
        full_name=(full_name or "").strip() or "کاربر",
        mobile=mobile,
        is_verified=True,
        is_active=True,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.session.add(user)
    db.session.commit()
    link_guest_orders(user)
    return user, None


def mark_verified(user: User) -> None:
    user.is_verified = True
    user.updated_at = utcnow()
    db.session.commit()


def change_password(user: User, current_password: str | None, new_password: str, confirm: str) -> list[str]:
    errors: list[str] = []
    if user.password_hash:
        if not verify_password(user.password_hash, current_password or ""):
            errors.append("رمز عبور فعلی صحیح نیست.")
    min_length = max(6, settings_service.get_int("password_min_length", 8))
    errors.extend(validate_password_strength(new_password, min_length))
    if new_password != confirm:
        errors.append("تکرار رمز عبور جدید مطابقت ندارد.")
    if errors:
        return errors
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.updated_at = utcnow()
    db.session.commit()
    return []


def set_password(user: User, new_password: str) -> None:
    user.password_hash = hash_password(new_password)
    user.updated_at = utcnow()
    # خروج از همه نشست‌های دیگر با پاک‌سازی قفل و شمارنده
    user.failed_login_count = 0
    user.locked_until = None
    db.session.commit()


def link_guest_orders(user: User) -> int:
    """اتصال سفارش‌های مهمان با همان موبایل به حساب کاربر (پس از احراز مالکیت)."""
    if not user or not user.mobile:
        return 0
    orders = (
        db.session.query(Order)
        .filter(Order.user_id.is_(None), Order.guest_mobile == user.mobile)
        .all()
    )
    for order in orders:
        order.user_id = user.id
        if not order.guest_name and user.full_name:
            order.guest_name = user.full_name
    if orders:
        db.session.commit()
    return len(orders)


def find_user_by_mobile_or_email(identifier: str) -> User | None:
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    mobile = normalize_mobile(identifier)
    return (
        db.session.query(User)
        .filter(or_(User.mobile == mobile, User.email == identifier))
        .filter(User.deleted_at.is_(None))
        .first()
    )
