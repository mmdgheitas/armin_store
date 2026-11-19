"""
گزارش‌های مالی و آماری بر مبنای داده واقعی.

تعریف‌ها:
  * فروش قطعی = سفارش‌هایی که پرداخت آن‌ها تأیید شده (payment_status=paid) و لغو نشده‌اند.
  * سود ناخالص = (فروش − تخفیف) − بهای تمام‌شده Snapshot اقلام.
  * سود عملیاتی تخمینی = سود ناخالص − هزینه‌های ثبت‌شده − بازپرداخت‌ها.
  * هیچ هزینه‌ای به‌صورت حدسی وارد گزارش نمی‌شود؛ فقط اقلام ثبت‌شده.
"""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from sqlalchemy import func

from app.extensions import db
from app.models import (
    Category,
    CustomerVideo,
    Expense,
    Order,
    OrderItem,
    Payment,
    Product,
    ProductVariant,
    Review,
    Ticket,
    User,
)
from app.utils.dates import as_tehran, jalali_date_string, parse_date, utcnow

PAID_STATUSES = ("paid", "processing", "ready_to_ship", "shipped", "delivered", "returned", "refunded")


def _range_from_args(args, default_days: int = 30):
    """
    بازه تاریخ از پارامترهای درخواست (پشتیبانی از تاریخ میلادی و شمسی).
    بازگشت: (start_utc, end_utc, from_date, to_date)
    """
    from datetime import datetime

    from_d = parse_date(args.get("from"))
    to_d = parse_date(args.get("to"))
    if to_d is None:
        to_d = as_tehran(utcnow()).date()
    if from_d is None:
        from_d = to_d - timedelta(days=max(1, default_days))
    # تبدیل بازه محلی تهران (UTC+3:30) به UTC naive
    start_dt = datetime(from_d.year, from_d.month, from_d.day) - timedelta(hours=3, minutes=30)
    end_dt = datetime(to_d.year, to_d.month, to_d.day) + timedelta(days=1) - timedelta(hours=3, minutes=30)
    return start_dt, end_dt, from_d, to_d


def resolve_period(args, default_days: int = 30):
    return _range_from_args(args, default_days)


def _paid_orders_query(start_dt=None, end_dt=None, include_refunded: bool = True):
    stmt = db.session.query(Order).filter(Order.payment_status == "paid")
    stmt = stmt.filter(Order.status.notin_(["cancelled"]))
    if start_dt is not None:
        stmt = stmt.filter(Order.paid_at >= start_dt)
    if end_dt is not None:
        stmt = stmt.filter(Order.paid_at < end_dt)
    return stmt


def sales_summary(start_dt=None, end_dt=None) -> dict:
    orders = _paid_orders_query(start_dt, end_dt).all()
    gross_sales = sum(o.total_amount for o in orders)
    subtotal = sum(o.subtotal for o in orders)
    discounts = sum(o.discount_amount for o in orders)
    shipping = sum(o.shipping_cost for o in orders)
    tax = sum(o.tax_amount or 0 for o in orders)
    refunds = sum(o.refund_amount or 0 for o in orders)
    item_revenue = subtotal - discounts
    cogs = sum((item.unit_cost_snapshot or 0) * item.quantity for o in orders for item in o.items)
    units = sum(item.quantity for o in orders for item in o.items)
    expense_total = expenses_total(start_dt, end_dt)
    gross_profit = item_revenue - cogs
    operating_profit = gross_profit - expense_total - refunds
    count = len(orders)

    return {
        "orders_count": count,
        "gross_sales": gross_sales,
        "subtotal": subtotal,
        "discounts": discounts,
        "shipping": shipping,
        "tax": tax,
        "refunds": refunds,
        "net_revenue": item_revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "expenses": expense_total,
        "operating_profit": operating_profit,
        "units": units,
        "average_order": int(gross_sales / count) if count else 0,
        "margin_percent": round((gross_profit * 100 / item_revenue), 1) if item_revenue else 0.0,
    }


def expenses_total(start_dt=None, end_dt=None) -> int:
    stmt = db.session.query(func.coalesce(func.sum(Expense.amount), 0))
    if start_dt is not None:
        stmt = stmt.filter(Expense.expense_date >= start_dt.date())
    if end_dt is not None:
        stmt = stmt.filter(Expense.expense_date <= end_dt.date())
    return int(stmt.scalar() or 0)


def sales_series(start_dt, end_dt, granularity: str = "day") -> list[dict]:
    """سری زمانی فروش برای نمودار (تجمیع در دیتابیس)."""
    fmt = "%Y-%m-%d" if granularity == "day" else "%Y-%m"
    rows = (
        db.session.query(
            func.strftime(fmt, Order.paid_at).label("bucket"),
            func.coalesce(func.sum(Order.total_amount), 0),
            func.count(Order.id),
        )
        .filter(
            Order.payment_status == "paid",
            Order.status != "cancelled",
            Order.paid_at >= start_dt,
            Order.paid_at < end_dt,
        )
        .group_by("bucket")
        .order_by("bucket")
        .all()
    )
    return [{"key": row[0], "value": int(row[1] or 0), "orders": int(row[2] or 0)} for row in rows]


def daily_series(days: int = 30) -> list[dict]:
    """سری روزانه با پر کردن روزهای بدون فروش."""
    end = utcnow()
    start = end - timedelta(days=days - 1)
    rows = {r["key"]: r for r in sales_series(start, end + timedelta(days=1), "day")}
    series = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        key = as_tehran(day).strftime("%Y-%m-%d")
        item = rows.get(key, {"value": 0, "orders": 0})
        series.append(
            {
                "key": key,
                "label": jalali_date_string(day),
                "value": item["value"],
                "orders": item["orders"],
            }
        )
    return series


def top_products(start_dt=None, end_dt=None, limit: int = 10) -> list[dict]:
    stmt = (
        db.session.query(
            OrderItem.product_name_snapshot,
            OrderItem.product_id,
            func.sum(OrderItem.quantity),
            func.sum(OrderItem.total_price),
            func.sum((OrderItem.unit_price - OrderItem.unit_cost_snapshot) * OrderItem.quantity),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.payment_status == "paid", Order.status != "cancelled")
    )
    if start_dt is not None:
        stmt = stmt.filter(Order.paid_at >= start_dt)
    if end_dt is not None:
        stmt = stmt.filter(Order.paid_at < end_dt)
    rows = (
        stmt.group_by(OrderItem.product_name_snapshot, OrderItem.product_id)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "name": row[0],
            "product_id": row[1],
            "quantity": int(row[2] or 0),
            "revenue": int(row[3] or 0),
            "profit": int(row[4] or 0),
        }
        for row in rows
    ]


def top_customers(limit: int = 10, start_dt=None, end_dt=None) -> list[dict]:
    stmt = (
        db.session.query(
            Order.user_id,
            func.count(Order.id),
            func.sum(Order.total_amount),
        )
        .filter(Order.payment_status == "paid", Order.status != "cancelled")
        .group_by(Order.user_id)
        .order_by(func.sum(Order.total_amount).desc())
        .limit(limit)
    )
    rows = stmt.all()
    result = []
    for user_id, count, total in rows:
        user = db.session.get(User, user_id) if user_id else None
        result.append(
            {
                "user": user,
                "name": user.full_name if user else "مهمان",
                "mobile": user.mobile if user else "-",
                "orders": int(count or 0),
                "total": int(total or 0),
            }
        )
    return result


def dashboard_stats() -> dict:
    today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=3, minutes=30)
    month_start = today_start - timedelta(days=30)
    all_time = sales_summary()
    today = sales_summary(today_start, None)
    month = sales_summary(month_start, None)
    counts = {
        "products": db.session.query(func.count(Product.id)).filter(Product.deleted_at.is_(None)).scalar() or 0,
        "customers": db.session.query(func.count(User.id)).filter(User.deleted_at.is_(None)).scalar() or 0,
        "orders": db.session.query(func.count(Order.id)).scalar() or 0,
        "pending_orders": db.session.query(func.count(Order.id)).filter(Order.status == "pending_payment").scalar() or 0,
        "pending_reviews": db.session.query(func.count(Review.id)).filter(Review.status == "pending").scalar() or 0,
        "pending_videos": db.session.query(func.count(CustomerVideo.id)).filter(CustomerVideo.status == "pending").scalar() or 0,
        "open_tickets": db.session.query(func.count(Ticket.id)).filter(Ticket.status.in_(["open", "answered"])).scalar() or 0,
        "categories": db.session.query(func.count(Category.id)).scalar() or 0,
        "low_stock": db.session.query(func.count(ProductVariant.id))
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(
            ProductVariant.is_active.is_(True),
            Product.deleted_at.is_(None),
            ProductVariant.stock_quantity <= ProductVariant.low_stock_threshold,
            ProductVariant.stock_quantity > 0,
        )
        .scalar()
        or 0,
        "out_of_stock": db.session.query(func.count(ProductVariant.id))
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(
            ProductVariant.is_active.is_(True),
            Product.deleted_at.is_(None),
            ProductVariant.stock_quantity <= 0,
            Product.product_type == "physical",
        )
        .scalar()
        or 0,
    }
    return {
        "today": today,
        "month": month,
        "all_time": all_time,
        "counts": counts,
        "series": daily_series(14),
        "top_products": top_products(limit=5),
        "recent_orders": db.session.query(Order).order_by(Order.created_at.desc()).limit(8).all(),
        "recent_payments": db.session.query(Payment)
        .join(Order, Order.id == Payment.order_id)
        .filter(Payment.status == "successful")
        .order_by(Payment.created_at.desc())
        .limit(8)
        .all(),
    }


def inventory_report() -> dict:
    from app.services import inventory_service

    rows = (
        db.session.query(ProductVariant, Product)
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(Product.deleted_at.is_(None))
        .order_by(ProductVariant.stock_quantity)
        .all()
    )
    value = inventory_service.inventory_value()
    return {"rows": rows, "value": value}


# --- خروجی‌ها ---

def build_csv(headers: list[str], rows: list[list]) -> str:
    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM برای نمایش صحیح فارسی در Excel
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return buffer.getvalue()


def build_excel(title: str, headers: list[str], rows: list[list], meta: dict | None = None) -> bytes | None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except Exception:
        return None

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = (title or "گزارش")[:31]
    sheet.sheet_view.rightToLeft = True

    sheet.append([(meta or {}).get("store", "فروشگاه")])
    sheet.append([(meta or {}).get("subtitle", "")])
    if meta and meta.get("period"):
        sheet.append([f"بازه گزارش: {meta['period']}"])
    sheet.append([])
    header_row = sheet.max_row + 1
    sheet.append(headers)
    for cell in sheet[header_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0F766E")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row in rows:
        sheet.append(row)

    for index, _ in enumerate(headers, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = 22
    for row in sheet.iter_rows(min_row=header_row + 1):
        for cell in row:
            cell.alignment = Alignment(horizontal="right")

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _shape(text) -> str:
    """آماده‌سازی متن فارسی برای PDF (شکل‌دهی حروف و راست‌به‌چپ)."""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(str(text)))
    except Exception:  # pragma: no cover
        return str(text)


def build_pdf(title: str, headers: list[str], rows: list[list], meta: dict | None = None) -> bytes | None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except Exception:
        return None

    from pathlib import Path

    from flask import current_app

    font_path = Path(current_app.config["BASE_DIR"]) / "app" / "static" / "fonts" / "Vazirmatn-Regular.ttf"
    bold_path = Path(current_app.config["BASE_DIR"]) / "app" / "static" / "fonts" / "Vazirmatn-Bold.ttf"
    try:
        if "Vazirmatn" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("Vazirmatn", str(font_path)))
            if bold_path.exists():
                pdfmetrics.registerFont(TTFont("Vazirmatn-Bold", str(bold_path)))
    except Exception:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=_shape(title),
    )
    normal = ParagraphStyle("fa", fontName="Vazirmatn", fontSize=9, leading=14, alignment=2)
    heading = ParagraphStyle("fa-h", fontName="Vazirmatn", fontSize=15, leading=22, alignment=2)
    story = [Paragraph(_shape((meta or {}).get("store", "فروشگاه")), heading)]
    story.append(Paragraph(_shape(title), normal))
    if meta and meta.get("subtitle"):
        story.append(Paragraph(_shape(meta["subtitle"]), normal))
    if meta and meta.get("period"):
        story.append(Paragraph(_shape(f"بازه گزارش: {meta['period']}"), normal))
    story.append(Spacer(1, 6 * mm))

    shaped_headers = [_shape(h) for h in reversed(headers)]
    data = [shaped_headers]
    for row in rows:
        data.append([_shape("" if value is None else value) for value in reversed(row)])

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Vazirmatn"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(_shape("این گزارش به‌صورت خودکار توسط سامانه تولید شده است."), normal))
    doc.build(story)
    return buffer.getvalue()


def orders_export_rows(orders: list[Order]) -> tuple[list[str], list[list]]:
    headers = [
        "شماره سفارش",
        "تاریخ",
        "مشتری",
        "موبایل",
        "وضعیت",
        "وضعیت پرداخت",
        "مبلغ کل",
        "تخفیف",
        "هزینه ارسال",
        "تعداد اقلام",
        "کد رهگیری",
    ]
    rows = []
    for order in orders:
        rows.append(
            [
                order.order_number,
                jalali_date_string(order.created_at),
                order.customer_name,
                order.customer_mobile,
                order.status_label,
                order.payment_status_label,
                order.total_amount,
                order.discount_amount,
                order.shipping_cost,
                order.items_count,
                order.tracking_code or "-",
            ]
        )
    return headers, rows


def profit_report(start_dt, end_dt) -> dict:
    summary = sales_summary(start_dt, end_dt)
    by_category = (
        db.session.query(
            Category.name,
            func.coalesce(func.sum(OrderItem.total_price), 0),
            func.coalesce(func.sum((OrderItem.unit_price - OrderItem.unit_cost_snapshot) * OrderItem.quantity), 0),
        )
        .join(Product, Product.id == OrderItem.product_id)
        .join(Category, Category.id == Product.category_id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.payment_status == "paid", Order.status != "cancelled")
        .filter(Order.paid_at >= start_dt, Order.paid_at < end_dt)
        .group_by(Category.name)
        .all()
    )
    expenses = (
        db.session.query(Expense.category, func.coalesce(func.sum(Expense.amount), 0))
        .filter(Expense.expense_date >= start_dt.date(), Expense.expense_date <= end_dt.date())
        .group_by(Expense.category)
        .all()
    )
    return {
        "summary": summary,
        "by_category": [
            {"name": row[0], "revenue": int(row[1] or 0), "profit": int(row[2] or 0)} for row in by_category
        ],
        "expense_breakdown": [{"name": row[0] or "سایر", "amount": int(row[1] or 0)} for row in expenses],
        "top_products": top_products(start_dt, end_dt, limit=10),
    }


def dashboard_payments(limit: int = 6):
    from app.models import Payment

    return (
        db.session.query(Payment)
        .order_by(Payment.created_at.desc())
        .limit(limit)
        .all()
    )


def today_range():
    from datetime import datetime

    now = as_tehran(utcnow())
    start_local = datetime(now.year, now.month, now.day) - timedelta(hours=3, minutes=30)
    return start_local, start_local + timedelta(days=1)


def parse_date_safe(value: str | None) -> date | None:
    return parse_date(value)
