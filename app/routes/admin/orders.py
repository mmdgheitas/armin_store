"""مدیریت سفارش‌ها، پرداخت‌ها، فاکتور، مرجوعی و بازپرداخت و خروجی‌ها."""
from __future__ import annotations

import io
from datetime import datetime, timedelta

from flask import Response, abort, flash, g, redirect, render_template, request, send_file, url_for
from sqlalchemy import or_

from app.constants import (
    ORDER_STATUSES,
    PAYMENT_STATUSES,
    SHIPPING_STATUSES,
)
from app.decorators import permission_required
from app.extensions import db
from app.models import Order, OrderItem, OrderStatusHistory, Payment, Product, ProductVariant
from app.routes.admin import admin_bp
from app.routes.admin.helpers import bool_field, int_field, log_admin, text_field
from app.services import (
    digital_delivery_service,
    inventory_service,
    order_service,
    report_service,
    settings_service,
)
from app.utils.dates import parse_date, utcnow
from app.utils.http import client_ip
from app.utils.pagination import paginate
from app.utils.security import generate_order_number, is_valid_mobile, normalize_mobile

# ---------------------------------------------------------------------------
# فهرست و جزئیات سفارش
# ---------------------------------------------------------------------------


def _orders_query(args):
    stmt = db.session.query(Order)
    q = (args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.filter(
            or_(
                Order.order_number.like(like),
                Order.guest_mobile.like(like),
                Order.guest_name.like(like),
                Order.tracking_code.like(like),
            )
        )
    status = args.get("status")
    if status in dict(ORDER_STATUSES):
        stmt = stmt.filter(Order.status == status)
    payment_status = args.get("payment_status")
    if payment_status in dict(PAYMENT_STATUSES):
        stmt = stmt.filter(Order.payment_status == payment_status)
    shipping_status = args.get("shipping_status")
    if shipping_status in dict(SHIPPING_STATUSES):
        stmt = stmt.filter(Order.shipping_status == shipping_status)
    gateway = args.get("gateway")
    if gateway:
        stmt = stmt.filter(
            Order.id.in_(db.session.query(Payment.order_id).filter(Payment.gateway == gateway))
        )
    from_date = parse_date(args.get("from"))
    to_date = parse_date(args.get("to"))
    if from_date:
        stmt = stmt.filter(
            Order.created_at >= datetime(from_date.year, from_date.month, from_date.day)
            - timedelta(hours=3, minutes=30)
        )
    if to_date:
        stmt = stmt.filter(
            Order.created_at
            < datetime(to_date.year, to_date.month, to_date.day) + timedelta(days=1)
            - timedelta(hours=3, minutes=30)
        )
    sort = args.get("sort", "newest")
    if sort == "oldest":
        stmt = stmt.order_by(Order.created_at.asc())
    elif sort == "amount":
        stmt = stmt.order_by(Order.total_amount.desc())
    else:
        stmt = stmt.order_by(Order.created_at.desc())
    return stmt


@admin_bp.get("/orders")
@permission_required("orders.view")
def orders():
    page = paginate(_orders_query(request.args), per_page=20)
    return render_template(
        "admin/orders/list.html",
        page=page,
        args=request.args,
        statuses=ORDER_STATUSES,
        payment_statuses=PAYMENT_STATUSES,
        shipping_statuses=SHIPPING_STATUSES,
        summary=report_service.orders_export_rows(page.items),
    )


@admin_bp.get("/orders/<order_number>")
@permission_required("orders.view")
def order_detail(order_number: str):
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    if order.is_paid:
        digital_delivery_service.ensure_tokens(order)
    return render_template(
        "admin/orders/detail.html",
        order=order,
        statuses=ORDER_STATUSES,
        next_statuses=[s for s in ORDER_STATUSES if s[0] in order.allowed_next_statuses],
        payment_statuses=PAYMENT_STATUSES,
        shipping_statuses=SHIPPING_STATUSES,
        downloadable={
            item.id: digital_delivery_service.item_state(item)
            for item in order.items
            if item.delivery_type == "digital"
        },
    )


@admin_bp.post("/orders/<order_number>/status")
@permission_required("orders.manage")
def order_status(order_number: str):
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    new_status = request.form.get("status", "")
    note = text_field("note", max_length=400)
    tracking = text_field("tracking_code", max_length=80)
    ok, message = order_service.change_status(
        order,
        new_status,
        admin=g.user,
        note=note,
        tracking_code=tracking or None,
        restock=bool_field("restock"),
    )
    if ok:
        log_admin("order.status", "order", order.id, new={"status": new_status, "note": note})
    flash(message, "success" if ok else "error")
    return redirect(url_for("admin.order_detail", order_number=order.order_number))


@admin_bp.post("/orders/<order_number>/shipping")
@permission_required("orders.manage")
def order_shipping_status(order_number: str):
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    ok, message = order_service.set_shipping_status(
        order,
        request.form.get("shipping_status", "pending"),
        admin=g.user,
        tracking_code=text_field("tracking_code", max_length=80) or None,
    )
    if ok:
        log_admin("order.shipping", "order", order.id, new={"shipping_status": order.shipping_status})
    flash(message, "success" if ok else "error")
    return redirect(url_for("admin.order_detail", order_number=order.order_number))


@admin_bp.post("/orders/<order_number>/tracking")
@permission_required("orders.manage")
def order_tracking(order_number: str):
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    order.tracking_code = text_field("tracking_code", max_length=80) or None
    order.updated_at = utcnow()
    db.session.commit()
    log_admin("order.tracking", "order", order.id, new={"tracking": order.tracking_code})
    flash("کد رهگیری ثبت شد.", "success")
    return redirect(url_for("admin.order_detail", order_number=order.order_number))


@admin_bp.post("/orders/<order_number>/cancel")
@permission_required("orders.manage")
def order_cancel(order_number: str):
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    ok, message = order_service.cancel_order(
        order, admin=g.user, reason=text_field("reason"), restock=True
    )
    if ok:
        log_admin("order.cancel", "order", order.id, new={"status": "cancelled"})
    flash(message, "success" if ok else "error")
    return redirect(url_for("admin.order_detail", order_number=order.order_number))


@admin_bp.post("/orders/<order_number>/return")
@permission_required("orders.manage")
def order_return(order_number: str):
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    ok, message = order_service.register_return(
        order, admin=g.user, reason=text_field("reason"), restock=bool_field("restock")
    )
    if ok:
        log_admin("order.return", "order", order.id, new={"status": "returned"})
    flash(message, "success" if ok else "error")
    return redirect(url_for("admin.order_detail", order_number=order.order_number))


@admin_bp.post("/orders/<order_number>/refund")
@permission_required("orders.manage")
def order_refund(order_number: str):
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    ok, message = order_service.register_refund(
        order, int_field("amount"), admin=g.user, note=text_field("note")
    )
    if ok:
        log_admin("order.refund", "order", order.id, new={"amount": int_field("amount")})
    flash(message, "success" if ok else "error")
    return redirect(url_for("admin.order_detail", order_number=order.order_number))


@admin_bp.post("/orders/<order_number>/note")
@permission_required("orders.manage")
def order_note(order_number: str):
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    order.admin_note = text_field("admin_note", max_length=4000) or None
    order.updated_at = utcnow()
    db.session.commit()
    log_admin("order.note", "order", order.id)
    flash("یادداشت داخلی ذخیره شد.", "success")
    return redirect(url_for("admin.order_detail", order_number=order.order_number))


@admin_bp.get("/orders/<order_number>/invoice")
@permission_required("orders.view")
def order_invoice(order_number: str):
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    return render_template("admin/orders/invoice.html", order=order)


# --- خروجی‌های سفارش ---


CURRENT_EXPORTS = {"excel": "xlsx", "csv": "csv", "pdf": "pdf"}


@admin_bp.get("/orders/export/<fmt>")
@permission_required("orders.view")
def orders_export(fmt: str):
    if fmt not in CURRENT_EXPORTS:
        abort(404)
    orders_list = _orders_query(request.args).limit(5000).all()
    headers, rows = report_service.orders_export_rows(orders_list)
    meta = {
        "store": settings_service.get_str("store_name", "فروشگاه"),
        "subtitle": "گزارش سفارش‌ها",
        "period": f"{request.args.get('from') or '—'} تا {request.args.get('to') or '—'}",
    }
    stamp = utcnow().strftime("%Y%m%d")
    if fmt == "csv":
        content = report_service.build_csv(headers, rows)
        return Response(
            content,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=orders-{stamp}.csv"},
        )
    if fmt == "excel":
        data = report_service.build_excel("سفارش‌ها", headers, rows, meta)
        if data is None:
            flash("کتابخانه خروجی Excel نصب نیست.", "error")
            return redirect(url_for("admin.orders"))
        return send_file(
            io.BytesIO(data),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"orders-{stamp}.xlsx",
        )
    data = report_service.build_pdf("گزارش سفارش‌ها", headers, rows, meta)
    if data is None:
        flash("کتابخانه خروجی PDF نصب نیست.", "error")
        return redirect(url_for("admin.orders"))
    return send_file(
        io.BytesIO(data),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"orders-{stamp}.pdf",
    )


# ---------------------------------------------------------------------------
# ثبت سفارش دستی
# ---------------------------------------------------------------------------


@admin_bp.route("/orders/create", methods=["GET", "POST"])
@permission_required("orders.manage")
def order_create():
    variants = (
        db.session.query(ProductVariant, Product)
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(ProductVariant.is_active.is_(True), Product.deleted_at.is_(None))
        .order_by(Product.name)
        .limit(300)
        .all()
    )
    if request.method == "POST":
        variant_ids = request.form.getlist("variant_id[]")
        quantities = request.form.getlist("quantity[]")
        items = []
        for index, variant_id in enumerate(variant_ids):
            if not variant_id.isdigit():
                continue
            try:
                quantity = max(0, int(quantities[index] if index < len(quantities) else 0))
            except ValueError:
                quantity = 0
            if quantity <= 0:
                continue
            variant = db.session.get(ProductVariant, int(variant_id))
            if variant is None:
                continue
            items.append((variant, quantity))
        if not items:
            flash("حداقل یک کالا با تعداد معتبر انتخاب کنید.", "error")
            return render_template("admin/orders/create.html", variants=variants, form=request.form)

        name = text_field("customer_name", max_length=150)
        mobile = normalize_mobile(request.form.get("mobile", ""))
        if not is_valid_mobile(mobile):
            flash("شماره موبایل مشتری معتبر نیست.", "error")
            return render_template("admin/orders/create.html", variants=variants, form=request.form)

        payment_mode = request.form.get("payment_mode", "unpaid")
        address = {
            "recipient_name": name,
            "mobile": mobile,
            "province": text_field("province", max_length=80),
            "city": text_field("city", max_length=80),
            "full_address": text_field("address", max_length=1000) or "ثبت سفارش توسط پشتیبانی",
            "postal_code": text_field("postal_code", max_length=10),
            "national_code": text_field("national_code", max_length=10),
        }
        shipping_cost = int_field("shipping_cost")
        subtotal = sum(variant.effective_price * qty for variant, qty in items)
        total = subtotal + shipping_cost

        order = Order(
            order_number=generate_order_number(),
            guest_name=name,
            guest_mobile=mobile,
            guest_email=text_field("email", max_length=190) or None,
            subtotal=subtotal,
            discount_amount=0,
            shipping_cost=shipping_cost,
            total_amount=total,
            status="pending_payment",
            payment_status="unpaid",
            shipping_status="pending",
            shipping_address_json=address,
            customer_note=text_field("note", max_length=2000) or None,
            admin_note="سفارش دستی توسط پشتیبانی ثبت شد.",
            has_physical_items=any(not variant.product.is_digital for variant, _ in items),
            created_at=utcnow(),
            updated_at=utcnow(),
            currency_code=settings_service.get_str("currency_code", "IRT"),
            currency_label=settings_service.get_str("currency_label", "تومان"),
            currency_unit=settings_service.get_str("currency_unit", "toman"),
            currency_ratio=settings_service.get_int("currency_ratio", 1),
        )
        db.session.add(order)
        db.session.flush()
        for variant, qty in items:
            db.session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=variant.product_id,
                    variant_id=variant.id,
                    product_name_snapshot=variant.product.name,
                    product_slug_snapshot=variant.product.slug,
                    variant_snapshot=variant.label_with_names,
                    sku_snapshot=variant.sku,
                    unit_price=variant.effective_price,
                    unit_cost_snapshot=variant.cost_price or 0,
                    quantity=qty,
                    total_price=variant.effective_price * qty,
                    delivery_type="digital" if variant.product.is_digital else "physical",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
        db.session.flush()

        reservation_items = [
            {"variant_id": v.id, "quantity": q, "product_id": v.product_id}
            for v, q in items
            if not v.product.is_digital
        ]
        if reservation_items:
            try:
                inventory_service.reserve_stock(order, reservation_items)
            except inventory_service.StockError as exc:
                db.session.rollback()
                flash(exc.message, "error")
                return render_template("admin/orders/create.html", variants=variants, form=request.form)

        db.session.add(
            OrderStatusHistory(
                order_id=order.id,
                field="status",
                from_value=None,
                to_value="pending_payment",
                note="ثبت سفارش دستی",
                admin_id=g.user.id,
                created_at=utcnow(),
            )
        )
        db.session.commit()

        if payment_mode == "offline":
            payment = Payment(
                order_id=order.id,
                gateway="manual",
                amount=order.total_amount,
                currency=order.currency_code,
                status="successful",
                reference_code=text_field("reference", max_length=120) or None,
                response_data='{"note": "پرداخت آفلاین تأییدشده توسط مدیر"}',
                verified_at=utcnow(),
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.session.add(payment)
            db.session.commit()
            order_service.mark_paid(order, payment)
            order.admin_note = (
                (order.admin_note or "")
                + f"\n[مدیر] پرداخت آفلاین ثبت شد. مرجع: {text_field('reference', max_length=120) or '-'}"
            )
            db.session.commit()
            log_admin("order.create_offline_payment", "order", order.id, new={"amount": order.total_amount})
            flash("سفارش ثبت و پرداخت آفلاین تأیید شد.", "success")
        else:
            flash("سفارش دستی ثبت شد. لینک پرداخت برای مشتری قابل ارسال است.", "success")
        log_admin("order.create", "order", order.id, new={"number": order.order_number, "total": total})
        return redirect(url_for("admin.order_detail", order_number=order.order_number))

    return render_template("admin/orders/create.html", variants=variants, form={})


# ---------------------------------------------------------------------------
# پرداخت‌ها
# ---------------------------------------------------------------------------


@admin_bp.get("/payments")
@permission_required("payments.view")
def payments():
    stmt = db.session.query(Payment).order_by(Payment.created_at.desc())
    status = request.args.get("status")
    if status:
        stmt = stmt.filter(Payment.status == status)
    gateway = request.args.get("gateway")
    if gateway:
        stmt = stmt.filter(Payment.gateway == gateway)
    q = (request.args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.filter(
            or_(
                Payment.transaction_id.like(like),
                Payment.reference_code.like(like),
                Payment.authority_or_token.like(like),
                Payment.order_id.in_(db.session.query(Order.id).filter(Order.order_number.like(like))),
            )
        )
    page = paginate(stmt, per_page=25)
    return render_template(
        "admin/payments/list.html", page=page, args=request.args, payment_statuses=PAYMENT_STATUSES
    )


@admin_bp.get("/payments/<int:payment_id>")
@permission_required("payments.view")
def payment_detail(payment_id: int):
    payment = db.session.get(Payment, payment_id)
    if payment is None:
        abort(404)
    import json

    def _load(raw):
        try:
            return json.dumps(json.loads(raw or "{}"), ensure_ascii=False, indent=2)
        except Exception:
            return raw or ""

    return render_template(
        "admin/payments/detail.html",
        payment=payment,
        request_data=_load(payment.request_data),
        response_data=_load(payment.response_data),
        callback_data=_load(payment.callback_data),
    )
