"""محدودیت نرخ درخواست بر پایه دیتابیس (سازگار با Passenger چندپروسه‌ای)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.content import RateLimitEntry
from app.utils.dates import utcnow


def _bucket_start(window_seconds: int):
    now = utcnow()
    epoch = int(now.timestamp())
    bucket = epoch - (epoch % max(1, window_seconds))
    return now.replace(microsecond=0) - timedelta(seconds=epoch - bucket), bucket


def consume(key: str, limit: int, window_seconds: int) -> tuple[bool, int, int]:
    """
    ثبت یک درخواست در پنجره زمانی.
    بازگشتی: (allowed, remaining, retry_after_seconds)
    """
    if limit <= 0:
        return True, 999, 0
    window_start, bucket = _bucket_start(window_seconds)
    retry_after = window_seconds - (int(utcnow().timestamp()) - bucket)

    entry = (
        db.session.query(RateLimitEntry)
        .filter(RateLimitEntry.key == key, RateLimitEntry.window_start == window_start)
        .first()
    )
    if entry is None:
        entry = RateLimitEntry(key=key, window_start=window_start, count=1, updated_at=utcnow())
        db.session.add(entry)
        try:
            db.session.commit()
            return True, limit - 1, retry_after
        except IntegrityError:
            db.session.rollback()
            entry = (
                db.session.query(RateLimitEntry)
                .filter(RateLimitEntry.key == key, RateLimitEntry.window_start == window_start)
                .first()
            )
            if entry is None:
                return True, limit - 1, retry_after
    entry.count = (entry.count or 0) + 1
    entry.updated_at = utcnow()
    db.session.commit()
    allowed = entry.count <= limit
    return allowed, max(0, limit - entry.count), retry_after


def is_limited(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """بررسی وضعیت بدون ثبت درخواست. بازگشتی: (limited, retry_after)."""
    window_start, bucket = _bucket_start(window_seconds)
    entry = (
        db.session.query(RateLimitEntry)
        .filter(RateLimitEntry.key == key, RateLimitEntry.window_start == window_start)
        .first()
    )
    count = entry.count if entry else 0
    retry_after = window_seconds - (int(utcnow().timestamp()) - bucket)
    return count >= limit, retry_after


def reset(key: str) -> None:
    db.session.query(RateLimitEntry).filter(RateLimitEntry.key == key).delete()
    db.session.commit()


def cleanup_old(days: int = 2) -> int:
    cutoff = utcnow() - timedelta(days=days)
    deleted = (
        db.session.query(RateLimitEntry)
        .filter(RateLimitEntry.updated_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.session.commit()
    return int(deleted or 0)
