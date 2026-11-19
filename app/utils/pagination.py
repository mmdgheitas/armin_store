"""صفحه‌بندی سمت سرور (بدون بارگذاری کل رکوردها)."""
from __future__ import annotations

from flask import request
from sqlalchemy import func, select

from app.extensions import db


class Page:
    """شیء صفحه با API سازگار با الگوها."""

    def __init__(self, items, page: int, per_page: int, total: int):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = int(total or 0)
        self.pages = max(1, (self.total + per_page - 1) // per_page) if self.total else 1

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def prev_num(self) -> int:
        return max(1, self.page - 1)

    @property
    def next_num(self) -> int:
        return min(self.pages, self.page + 1)

    @property
    def first_item_index(self) -> int:
        return (self.page - 1) * self.per_page + 1 if self.total else 0

    @property
    def last_item_index(self) -> int:
        return min(self.total, self.page * self.per_page)

    def iter_pages(self, edge: int = 1, around: int = 2):
        last = 0
        for num in range(1, self.pages + 1):
            if (
                num <= edge
                or num > self.pages - edge
                or abs(num - self.page) <= around
            ):
                if last and num - last > 1:
                    yield None
                yield num
                last = num


def paginate(stmt, page=None, per_page: int = 12) -> Page:
    """اجرای صفحه‌بندی روی یک select SQLAlchemy 2.x."""
    try:
        page = int(page if page is not None else request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)
    try:
        per_page = int(request.args.get("per_page", per_page)) if page else per_page
    except (TypeError, ValueError):
        per_page = per_page
    per_page = max(1, min(per_page, 100))

    if hasattr(stmt, "statement"):  # سازگاری با Query قدیمی Flask-SQLAlchemy
        stmt = stmt.statement
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = db.session.scalar(count_stmt) or 0
    result = db.session.execute(stmt.limit(per_page).offset((page - 1) * per_page))
    rows = result.all()
    if rows and len(rows[0]) == 1:
        rows = [row[0] for row in rows]
    return Page(rows, page, per_page, total)
