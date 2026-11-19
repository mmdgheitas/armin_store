"""گزارش‌های مالی، سود، انبار و هزینه‌ها همراه با خروجی Excel/PDF/CSV."""
from __future__ import annotations

import io
from datetime import date

from flask import Response, abort, flash, g, redirect, render_template, request, send_file, url_for

from app.decorators import permission_required
from app.extensions import db
from app.models import Expense
from app.routes.admin import admin_bp
from app.routes.admin.helpers import int_field, log_admin, text_field
from app.services import inventory_service, report_service, settings_service
from app.utils.dates import jalali_date_string, parse_date, utcnow
from app.utils.pagination import paginate


def _period_context():
    start_dt, end_dt, from_d, to_d = report_service.resolve_period(request.args, default_days=30)
    return {
        "start_dt": start_dt,
        "end_dt": end_dt,
        "from_date": from_d,
        "to_date": to_d,
        "from_value": (request.args.get("from") or from_d.isoformat()),
        "to_value": (request.args.get("to") or to_d.isoformat()),
        "period_label": f"{jalali_date_string(_to_dt(from_d))} تا {jalali_date_string(_to_dt(to_d))}",
    }


def _to_dt(value: date):
    from datetime import datetime

    return datetime(value.year, value.month, value.day)


@admin_bp.get("/reports")
@permission_required("reports.view")
def reports():
    context = _period_context()
    summary = report_service.sales_summary(context["start_dt"], context["end_dt"])
    series = report_service.sales_series(context["start_dt"], context["end_dt"], "day")
    top = report_service.top_products(context["start_dt"], context["end_dt"], 10)
    top_customers = report_service.top_customers(8, context["start_dt"], context["end_dt"])
    return render_template(
        "admin/reports/index.html",
        context=context,
        summary=summary,
        series=series,
        top_products=top,
        top_customers=top_customers,
    )


@admin_bp.get("/reports/profit")
@permission_required("reports.view")
def reports_profit():
    context = _period_context()
    data = report_service.profit_report(context["start_dt"], context["end_dt"])
    return render_template("admin/reports/profit.html", context=context, **data)


@admin_bp.get("/reports/inventory")
@permission_required("inventory.view", "reports.view", any_of=True)
def reports_inventory():
    data = report_service.inventory_report()
    return render_template(
        "admin/reports/inventory.html",
        rows=data["rows"],
        value=data["value"],
        low_stock=inventory_service.low_stock_variants(30),
        out_of_stock=inventory_service.out_of_stock_variants(30),
    )


@admin_bp.get("/reports/export/<kind>/<fmt>")
@permission_required("reports.view")
def reports_export(kind: str, fmt: str):
    if fmt not in {"excel", "csv", "pdf"}:
        abort(404)
    context = _period_context()
    start_dt, end_dt = context["start_dt"], context["end_dt"]
    meta = {
        "store": settings_service.get_str("store_name", "فروشگاه"),
        "period": context["period_label"],
    }
    if kind == "sales":
        summary = report_service.sales_summary(start_dt, end_dt)
        headers = ["شاخص", "مقدار (تومان)"]
        rows = [
            ["مجموع فروش", summary["gross_sales"]],
            ["تعداد سفارش", summary["orders_count"]],
            ["میانگین سفارش", summary["average_order"]],
            ["جمع تخفیف", summary["discounts"]],
            ["هزینه ارسال", summary["shipping"]],
            ["بهای تمام‌شده کالا", summary["cogs"]],
            ["سود ناخالص", summary["gross_profit"]],
            ["هزینه‌های ثبت‌شده", summary["expenses"]],
            ["بازپرداخت", summary["refunds"]],
            ["سود عملیاتی تخمینی", summary["operating_profit"]],
        ]
        title = "گزارش فروش"
        meta["subtitle"] = "خلاصه دوره"
    elif kind == "products":
        data = report_service.top_products(start_dt, end_dt, 200)
        headers = ["محصول", "تعداد فروش", "درآمد", "سود"]
        rows = [[item["name"], item["quantity"], item["revenue"], item["profit"]] for item in data]
        title = "گزارش فروش محصولات"
        meta["subtitle"] = "بر اساس تعداد فروش"
    elif kind == "inventory":
        data = report_service.inventory_report()
        headers = ["محصول", "SKU", "موجودی", "قیمت خرید", "قیمت فروش", "ارزش موجودی"]
        rows = [
            [
                product.name,
                variant.sku,
                variant.stock_quantity,
                variant.cost_price,
                variant.price,
                int(variant.stock_quantity or 0) * int(variant.cost_price or 0),
            ]
            for variant, product in data["rows"]
        ]
        title = "گزارش موجودی انبار"
        meta["subtitle"] = f"ارزش کل انبار: {data['value']['cost_value']}"
    elif kind == "profit":
        data = report_service.profit_report(start_dt, end_dt)
        summary = data["summary"]
        headers = ["شاخص", "مقدار (تومان)"]
        rows = [
            ["فروش کالا پس از تخفیف", summary["net_revenue"]],
            ["بهای تمام‌شده کالا", summary["cogs"]],
            ["سود ناخالص", summary["gross_profit"]],
            ["هزینه‌های عملیاتی", summary["expenses"]],
            ["مرجوعی و بازپرداخت", summary["refunds"]],
            ["سود عملیاتی", summary["operating_profit"]],
            ["تعداد سفارش", summary["orders_count"]],
        ]
        for item in data["by_category"]:
            rows.append([f"سود دسته {item['name']}", item["profit"]])
        title = "گزارش سود و زیان"
        meta["subtitle"] = "سود ناخالص و عملیاتی"
    elif kind == "expenses":
        items = db.session.query(Expense).filter(
            Expense.expense_date >= start_dt.date(), Expense.expense_date <= end_dt.date()
        ).all()
        headers = ["عنوان", "دسته", "مبلغ", "تاریخ", "توضیح"]
        rows = [
            [item.title, item.category or "-", item.amount, item.expense_date.isoformat(), item.description or ""]
            for item in items
        ]
        title = "گزارش هزینه‌ها"
        meta["subtitle"] = "هزینه‌های ثبت‌شده"
    else:
        abort(404)

    stamp = utcnow().strftime("%Y%m%d")
    if fmt == "csv":
        return Response(
            report_service.build_csv(headers, rows),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={kind}-{stamp}.csv"},
        )
    if fmt == "excel":
        data = report_service.build_excel(title, headers, rows, meta)
        if data is None:
            flash("کتابخانه Excel نصب نیست.", "error")
            return redirect(url_for("admin.reports"))
        return send_file(
            io.BytesIO(data),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"{kind}-{stamp}.xlsx",
        )
    data = report_service.build_pdf(title, headers, rows, meta)
    if data is None:
        flash("کتابخانه PDF نصب نیست.", "error")
        return redirect(url_for("admin.reports"))
    return send_file(
        io.BytesIO(data),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{kind}-{stamp}.pdf",
    )


# ---------------------------------------------------------------------------
# هزینه‌ها
# ---------------------------------------------------------------------------


@admin_bp.get("/expenses")
@permission_required("expenses.manage")
def expenses():
    stmt = db.session.query(Expense).order_by(Expense.expense_date.desc(), Expense.id.desc())
    category = (request.args.get("category") or "").strip()
    if category:
        stmt = stmt.filter(Expense.category == category)
    page = paginate(stmt, per_page=30)
    categories = [
        row[0]
        for row in db.session.query(Expense.category).distinct().all()
        if row[0]
    ]
    total = sum(item.amount for item in page.items)
    return render_template(
        "admin/expenses/list.html",
        page=page,
        categories=categories,
        selected_category=category,
        page_total=total,
        today=utcnow().date().isoformat(),
    )


@admin_bp.route("/expenses/create", methods=["POST"])
@admin_bp.route("/expenses/<int:expense_id>/edit", methods=["POST"])
@permission_required("expenses.manage")
def expense_save(expense_id: int | None = None):
    expense = db.session.get(Expense, expense_id) if expense_id else None
    if expense_id and expense is None:
        abort(404)
    title = text_field("title", max_length=200)
    amount = int_field("amount")
    expense_date = parse_date(request.form.get("expense_date")) or utcnow().date()
    if len(title) < 2 or amount <= 0:
        flash("عنوان و مبلغ هزینه را درست وارد کنید.", "error")
        return redirect(url_for("admin.expenses"))
    if expense is None:
        expense = Expense(created_by=g.user.id, created_at=utcnow(), updated_at=utcnow())
        db.session.add(expense)
    expense.title = title
    expense.category = text_field("category", max_length=80) or None
    expense.amount = amount
    expense.description = text_field("description") or None
    expense.expense_date = expense_date
    expense.updated_at = utcnow()
    db.session.commit()
    log_admin("expense.save", "expense", expense.id, new={"title": expense.title, "amount": amount})
    flash("هزینه ذخیره شد.", "success")
    return redirect(url_for("admin.expenses"))


@admin_bp.post("/expenses/<int:expense_id>/delete")
@permission_required("expenses.manage")
def expense_delete(expense_id: int):
    expense = db.session.get(Expense, expense_id)
    if expense is None:
        abort(404)
    db.session.delete(expense)
    db.session.commit()
    log_admin("expense.delete", "expense", expense_id)
    flash("هزینه حذف شد.", "success")
    return redirect(url_for("admin.expenses"))
