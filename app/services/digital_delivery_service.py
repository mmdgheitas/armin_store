"""
تحویل محصولات دیجیتال پس از پرداخت تأییدشده.

امنیت:
  * لینک دانلود دارای امضای HMAC متصل به شناسه قلم سفارش است و حدس آن ممکن نیست.
  * مسیر واقعی فایل هرگز در URL یا صفحه نمایش داده نمی‌شود.
  * تعداد و مهلت دانلود از تنظیمات محصول اعمال می‌شود.
  * کد لایسنس در صورت تعریف، یکتا به هر سفارش اختصاص می‌یابد.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

from flask import current_app, url_for

from app.extensions import db
from app.models import DigitalAsset, DownloadToken, OrderItem
from app.utils.dates import utcnow
from app.utils.security import hash_token


def _signature(order_item: OrderItem) -> str:
    secret = str(current_app.config["SECRET_KEY"]).encode()
    payload = f"dl:{order_item.id}:{order_item.order_id}".encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def asset_for_item(item: OrderItem) -> DigitalAsset | None:
    """انتخاب دارایی دیجیتال: اول مخصوص تنوع، سپس مخصوص محصول."""
    assets = [a for a in (item.product.digital_assets if item.product else []) if a.is_active]
    if not assets:
        assets = [a for a in (item.digital_asset.digital_assets if item.digital_asset else []) if a.is_active]
    for asset in assets:
        if item.variant_id and asset.variant_id == item.variant_id:
            return asset
    for asset in assets:
        if asset.variant_id is None:
            return asset
    return assets[0] if assets else None


def _assign_license(asset: DigitalAsset) -> str | None:
    codes = [line.strip() for line in (asset.license_code or "").splitlines() if line.strip()]
    if not codes:
        return None
    used = {
        row[0]
        for row in db.session.query(DownloadToken.issued_license)
        .filter(DownloadToken.issued_license.isnot(None))
        .all()
    }
    for code in codes:
        if code not in used:
            return code
    return None


def ensure_tokens(order) -> list[DownloadToken]:
    """ایجاد (یک‌بار) توکن‌های دانلود برای اقلام دیجیتال سفارش پرداخت‌شده."""
    if order is None or not order.is_paid:
        return []
    created: list[DownloadToken] = []
    changed = False
    for item in order.items:
        if item.delivery_type != "digital":
            continue
        asset = asset_for_item(item)
        if asset is None:
            continue
        existing = (
            db.session.query(DownloadToken)
            .filter(DownloadToken.order_item_id == item.id)
            .first()
        )
        if existing is not None:
            created.append(existing)
            continue
        max_downloads = int(asset.download_limit or 5)
        expiry_days = int(asset.download_expiry_days or 0)
        token = DownloadToken(
            order_item_id=item.id,
            digital_asset_id=asset.id,
            token_hash=hash_token(_signature(item)),
            download_count=0,
            max_downloads=max_downloads,
            expires_at=(utcnow() + timedelta(days=expiry_days)) if expiry_days > 0 else None,
            issued_license=_assign_license(asset) if asset.delivery_type == "license" else None,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.session.add(token)
        item.delivered_at = item.delivered_at or utcnow()
        created.append(token)
        changed = True
    if changed:
        db.session.commit()
    return created


def fulfill_order(order) -> int:
    """تحویل اقلام دیجیتال. فراخوانی پس از پرداخت موفق."""
    tokens = ensure_tokens(order)
    return len(tokens)


def download_url(order_item: OrderItem) -> str:
    return url_for("account.download_item", item_id=order_item.id, sig=_signature(order_item))


def verify_signature(order_item: OrderItem, signature: str) -> bool:
    if order_item is None or not signature:
        return False
    try:
        return hmac.compare_digest(_signature(order_item), str(signature))
    except Exception:  # pragma: no cover
        return False


def get_token(order_item: OrderItem) -> DownloadToken | None:
    return (
        db.session.query(DownloadToken)
        .filter(DownloadToken.order_item_id == order_item.id)
        .first()
    )


def item_state(item: OrderItem) -> dict:
    """وضعیت تحویل یک قلم دیجیتال برای نمایش در حساب کاربری."""
    asset = asset_for_item(item)
    token = get_token(item)
    if item.order_id and not token and item.order and item.order.is_paid:
        tokens = ensure_tokens(item.order)
        token = next((t for t in tokens if t.order_item_id == item.id), None)
    state = {
        "asset": asset,
        "token": token,
        "can_download": False,
        "remaining": 0,
        "message": "",
        "license": token.issued_license if token else None,
        "external_url": None,
        "delivery_type": asset.delivery_type if asset else None,
    }
    if asset is None:
        state["message"] = "فایل این محصول هنوز توسط فروشنده بارگذاری نشده است."
        return state
    if token is None:
        state["message"] = "تحویل پس از تأیید پرداخت فعال می‌شود."
        return state
    if token.is_expired:
        state["message"] = "مهلت دانلود این محصول به پایان رسیده است."
        return state
    if token.is_exhausted:
        state["message"] = "سقف تعداد دانلود این محصول تکمیل شده است."
        return state

    state["remaining"] = max(0, token.max_downloads - token.download_count)
    if asset.delivery_type == "link":
        state["external_url"] = asset.external_url
        state["can_download"] = bool(asset.external_url)
        state["message"] = "لینک دانلود آماده است."
    elif asset.delivery_type == "license":
        state["can_download"] = bool(state["license"] or asset.file_path)
        state["message"] = "کد لایسنس شما صادر شد." if state["license"] else "کد لایسنس در حال صدور است."
    else:
        state["can_download"] = bool(asset.file_path)
        state["message"] = "فایل آماده دانلود است."
    return state


def register_download(item: OrderItem, token: DownloadToken) -> None:
    token.download_count = int(token.download_count or 0) + 1
    token.last_download_at = utcnow()
    token.updated_at = utcnow()
    db.session.commit()
