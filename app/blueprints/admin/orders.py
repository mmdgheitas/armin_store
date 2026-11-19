from __future__ import annotations

import csv
import io
import math
from typing import Any

from flask import (
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from app.blueprints.admin import admin_bp
from app.db import get_db
from app.repositories.audit import log_audit
from app.repositories.orders import (
    get_order_by_id,
    get_order_history,
    get_order_items,
    get_order_payments,
    list_orders_admin,
)
from app.repositories.users import (
    create_user,
    get_province_and_city_names,
    get_user_by_mobile,
    list_provinces,
)
from app.services.auth import admin_required
from app.services.bitpay import get_payment_gateway
from app.services.inventory import InsufficientStockError, expire_unpaid_orders
from app.services.orders import (
    ALLOWED_TRANSITIONS,
    LOCKED_FOR_EDIT_STATUSES,
    ORDER_STATUS_LABELS,
    PAYMENT_METHOD_LABELS,
    PAYMENT_STATUS_LABELS,
    OrderError,
    create_manual_order_admin,
    delete_order_super_admin,
    edit_order_admin,
    initiate_gateway_payment,
    process_payment_callback,
    transition_order_status,
)
from app.services.smsir import enqueue_sms
from app.utils.persian import (
    normalize_iran_mobile,
    now_utc_iso,
    to_english_digits,
    to_jalali,
)


def _to_int(val: Any, default: int = 0) -> int:
    try:
        s = to_english_digits(str(val or "")).replace(",", "").strip()
        return int(s) if s != "" else default
    except ValueError:
        return default


@admin_bp.route("/orders")
@admin_required("orders")
def orders_list():
    conn = get_db()
    expire_unpaid_orders(conn)

    q = (request.args.get("q") or "").strip()
    order_status = (request.args.get("order_status") or "").strip()
    payment_status = (request.args.get("payment_status") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    coupon_code = (request.args.get("coupon_code") or "").strip()
    customer_id = _to_int(request.args.get("customer_id"), 0) or None
    min_amount = _to_int(request.args.get("min_amount"), -1)
    max_amount = _to_int(request.args.get("max_amount"), -1)
    page = max(1, _to_int(request.args.get("page"), 1))
    per_page = 25

    items, total = list_orders_admin(
        conn,
        q=q,
        order_status=order_status,
        payment_status=payment_status,
        date_from=date_from,
        date_to=date_to,
        customer_id=customer_id,
        coupon_code=coupon_code,
        min_amount_toman=min_amount if min_amount >= 0 else None,
        max_amount_toman=max_amount if max_amount >= 0 else None,
        page=page,
        per_page=per_page,
    )
    total_pages = max(1, math.ceil(total / per_page))

    return render_template(
        "admin/orders_list.html",
        orders=items,
        total=total,
        page=page,
        total_pages=total_pages,
        status_labels=ORDER_STATUS_LABELS,
        payment_labels=PAYMENT_STATUS_LABELS,
        method_labels=PAYMENT_METHOD_LABELS,
        filters={
            "q": q,
            "order_status": order_status,
            "payment_status": payment_status,
            "date_from": date_from,
            "date_to": date_to,
            "coupon_code": coupon_code,
            "customer_id": customer_id or "",
            "min_amount": request.args.get("min_amount", ""),
            "max_amount": request.args.get("max_amount", ""),
        },
    )


@admin_bp.route("/orders/export.csv")
@admin_required("orders")
def orders_export_csv():
    conn = get_db()
    items, _ = list_orders_admin(
        conn,
        q=(request.args.get("q") or "").strip(),
        order_status=(request.args.get("order_status") or "").strip(),
        payment_status=(request.args.get("payment_status") or "").strip(),
        date_from=(request.args.get("date_from") or "").strip(),
        date_to=(request.args.get("date_to") or "").strip(),
        page=1,
        per_page=5000,
    )
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(
        ["شماره سفارش", "مشتری", "موبایل", "استان", "شهر", "جمع کل (تومان)", "هزینه ارسال (تومان)", "تخفیف (تومان)", "مبلغ نهایی (تومان)", "وضعیت پرداخت", "وضعیت سفارش", "کد رهگیری", "تاریخ ثبت"]
    )
    for o in items:
        w.writerow([
            o["order_number"],
            o["customer_name"],
            o["customer_mobile"],
            o["shipping_province"],
            o["shipping_city"],
            o["subtotal_rial"] // 10,
            o["shipping_fee_rial"] // 10,
            o["discount_rial"] // 10,
            o["total_rial"] // 10,
            PAYMENT_STATUS_LABELS.get(o["payment_status"], o["payment_status"]),
            ORDER_STATUS_LABELS.get(o["order_status"], o["order_status"]),
            o["tracking_code"],
            to_jalali(o["created_at"]),
        ])
    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="orders.csv"'},
    )


@admin_bp.route("/orders/bulk", methods=["POST"])
@admin_required("orders")
def orders_bulk():
    conn = get_db()
    action = request.form.get("bulk_action", "").strip()
    ids = [_to_int(x, 0) for x in request.form.getlist("selected_ids[]") if _to_int(x, 0) > 0]
    if not ids:
        flash("سفارشی انتخاب نشده است.", "warning")
        return redirect(url_for("admin.orders_list"))

    if action == "print_selected":
        ph = ",".join("?" for _ in ids)
        orders_data = []
        for row in conn.execute(f"SELECT * FROM orders WHERE id IN ({ph}) ORDER BY id DESC", ids).fetchall():
            d = dict(row)
            d["items"] = get_order_items(conn, int(row["id"]))
            orders_data.append(d)
        return render_template(
            "admin/orders_print.html",
            orders_data=orders_data,
            doc_type="invoice",
            status_labels=ORDER_STATUS_LABELS,
            method_labels=PAYMENT_METHOD_LABELS,
        )

    if action in ORDER_STATUS_LABELS:
        ok_cnt = 0
        fail_cnt = 0
        for oid in ids:
            ok, _ = transition_order_status(
                conn,
                order_id=oid,
                new_status=action,
                actor_user_id=int(g.current_user["id"]),
                note="تغییر وضعیت گروهی توسط مدیر",
            )
            if ok:
                ok_cnt += 1
            else:
                fail_cnt += 1
        log_audit(
            conn,
            admin_id=int(g.current_user["id"]),
            admin_name=g.current_user.get("username") or g.current_user["full_name"],
            action=f"bulk_status_{action}",
            entity_type="order",
            entity_id=",".join(map(str, ids)),
            after={"success": ok_cnt, "failed": fail_cnt},
            ip_address=request.remote_addr or "",
            user_agent=request.headers.get("User-Agent", ""),
        )
        flash(f"وضعیت {ok_cnt} سفارش تغییر یافت" + (f" ({fail_cnt} مورد مجاز نبود)." if fail_cnt else "."), "info")
    return redirect(url_for("admin.orders_list"))


@admin_bp.route("/orders/manual", methods=["GET", "POST"])
@admin_required("orders")
def order_create_manual():
    conn = get_db()
    provinces = list_provinces(conn)
    customers = conn.execute(
        "SELECT id, full_name, mobile, email FROM users WHERE role = 'customer' ORDER BY id DESC LIMIT 200"
    ).fetchall()
    variants = conn.execute(
        """
        SELECT v.id, v.label, v.sku, v.price_rial, v.stock_quantity, p.title AS product_title
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.is_active = 1 AND p.status != 'archived'
        ORDER BY p.title ASC, v.sort_order ASC
        """
    ).fetchall()

    if request.method == "POST":
        existing_uid = _to_int(request.form.get("customer_user_id"), 0) or None
        customer_name = (request.form.get("customer_name") or "").strip()
        customer_mobile = normalize_iran_mobile(request.form.get("customer_mobile"))
        customer_email = (request.form.get("customer_email") or "").strip()
        province_id = _to_int(request.form.get("province_id"), 0) or None
        city_id = _to_int(request.form.get("city_id"), 0) or None
        p_name, c_name = get_province_and_city_names(conn, province_id, city_id)
        if not p_name:
            p_name = (request.form.get("province_name_fallback") or "").strip()
        if not c_name:
            c_name = (request.form.get("city_name_fallback") or "").strip()
        shipping_address = (request.form.get("shipping_address") or "").strip()
        postal_code = (request.form.get("shipping_postal_code") or "").strip()
        shipping_fee_rial = max(0, _to_int(request.form.get("shipping_fee_toman"), 0)) * 10
        discount_rial = max(0, _to_int(request.form.get("discount_toman"), 0)) * 10
        payment_method = request.form.get("payment_method", "offline")
        send_pay_sms = request.form.get("send_pay_sms") == "1"
        admin_note = (request.form.get("admin_note") or "").strip()
        customer_note = (request.form.get("customer_note") or "").strip()

        vids = request.form.getlist("item_variant_id[]")
        qtys = request.form.getlist("item_qty[]")
        items_input: list[tuple[int, int]] = []
        for vid_s, qty_s in zip(vids, qtys):
            vid = _to_int(vid_s, 0)
            qty = _to_int(qty_s, 0)
            if vid > 0 and qty > 0:
                items_input.append((vid, qty))

        if not customer_name or not customer_mobile or not p_name or not c_name or not shipping_address:
            flash("نام، موبایل معتبر، استان، شهر و نشانی الزامی است.", "danger")
            return render_template(
                "admin/order_manual.html",
                provinces=provinces,
                customers=customers,
                variants=variants,
                form_data=request.form,
            )

        if not existing_uid:
            u = get_user_by_mobile(conn, customer_mobile)
            if u:
                existing_uid = int(u["id"])
            else:
                existing_uid = create_user(
                    conn,
                    mobile=customer_mobile,
                    email=customer_email or None,
                    full_name=customer_name,
                    role="customer",
                )

        try:
            order = create_manual_order_admin(
                conn,
                admin_id=int(g.current_user["id"]),
                user_id=existing_uid,
                customer_name=customer_name,
                customer_mobile=customer_mobile,
                customer_email=customer_email,
                shipping_province=p_name,
                shipping_city=c_name,
                shipping_address=shipping_address,
                shipping_postal_code=postal_code,
                items_input=items_input,
                shipping_fee_rial=shipping_fee_rial,
                discount_rial=discount_rial,
                payment_method=payment_method,
                admin_note=admin_note,
                customer_note=customer_note,
            )
            log_audit(
                conn,
                admin_id=int(g.current_user["id"]),
                admin_name=g.current_user.get("username") or g.current_user["full_name"],
                action="create_manual",
                entity_type="order",
                entity_id=order["id"],
                after={"order_number": order["order_number"], "total_rial": order["total_rial"]},
                ip_address=request.remote_addr or "",
                user_agent=request.headers.get("User-Agent", ""),
            )
        except (InsufficientStockError, OrderError) as exc:
            flash(str(exc), "danger")
            return render_template(
                "admin/order_manual.html",
                provinces=provinces,
                customers=customers,
                variants=variants,
                form_data=request.form,
            )

        if payment_method == "pay_link" and send_pay_sms:
            gateway = get_payment_gateway(conn, base_url=request.host_url.rstrip("/"))
            cb = url_for("payment.callback", _external=True)
            ok_g, pay_url, err_g = initiate_gateway_payment(
                conn, order_id=int(order["id"]), gateway=gateway, callback_url=cb
            )
            if ok_g:
                enqueue_sms(
                    conn,
                    recipient=customer_mobile,
                    event_type="manual_pay_link",
                    message_text=f"لینک پرداخت سفارش {order['order_number']}: {pay_url}",
                )
                flash(f"سفارش {order['order_number']} ثبت شد و لینک پرداخت پیامک گردید.", "success")
            else:
                flash(f"سفارش ثبت شد اما ایجاد لینک پرداخت با خطا مواجه شد: {err_g}", "warning")
        else:
            flash(f"سفارش دستی {order['order_number']} با موفقیت ثبت شد.", "success")

        return redirect(url_for("admin.order_detail", order_id=order["id"]))

    return render_template(
        "admin/order_manual.html",
        provinces=provinces,
        customers=customers,
        variants=variants,
        form_data={},
    )


@admin_bp.route("/orders/<int:order_id>")
@admin_required("orders")
def order_detail(order_id: int):
    conn = get_db()
    order = get_order_by_id(conn, order_id)
    if not order:
        abort(404)
    items = get_order_items(conn, order_id)
    history = get_order_history(conn, order_id)
    payments = get_order_payments(conn, order_id)
    allowed_next = sorted(ALLOWED_TRANSITIONS.get(order["order_status"], set()))
    variants = conn.execute(
        """
        SELECT v.id, v.label, v.sku, v.price_rial, v.stock_quantity, p.title AS product_title
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.is_active = 1 AND p.status != 'archived'
        ORDER BY p.title ASC, v.sort_order ASC
        """
    ).fetchall()

    return render_template(
        "admin/order_detail.html",
        order=dict(order),
        items=items,
        history=history,
        payments=payments,
        allowed_next=allowed_next,
        is_locked=(order["order_status"] in LOCKED_FOR_EDIT_STATUSES),
        variants=variants,
        status_labels=ORDER_STATUS_LABELS,
        payment_labels=PAYMENT_STATUS_LABELS,
        method_labels=PAYMENT_METHOD_LABELS,
    )


@admin_bp.route("/orders/<int:order_id>/status", methods=["POST"])
@admin_required("orders")
def order_update_status(order_id: int):
    conn = get_db()
    order = get_order_by_id(conn, order_id)
    if not order:
        abort(404)
    new_status = request.form.get("new_status", order["order_status"])
    note = request.form.get("note", "")
    tracking_code = request.form.get("tracking_code")
    carrier_name = request.form.get("carrier_name")
    restock_choice = request.form.get("restock_choice", "1") == "1"
    refund_toman = _to_int(request.form.get("refund_amount_toman"), 0)
    refund_reason = request.form.get("refund_reason", "")

    ok, msg = transition_order_status(
        conn,
        order_id=order_id,
        new_status=new_status,
        actor_user_id=int(g.current_user["id"]),
        note=note,
        tracking_code=tracking_code,
        carrier_name=carrier_name,
        restock_on_refund_or_return=restock_choice,
        refund_amount_rial=(refund_toman * 10) if refund_toman > 0 else None,
        refund_reason=refund_reason,
    )
    if ok:
        log_audit(
            conn,
            admin_id=int(g.current_user["id"]),
            admin_name=g.current_user.get("username") or g.current_user["full_name"],
            action="status_change",
            entity_type="order",
            entity_id=order_id,
            before={"order_status": order["order_status"]},
            after={"order_status": new_status, "tracking_code": tracking_code},
            ip_address=request.remote_addr or "",
            user_agent=request.headers.get("User-Agent", ""),
        )
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@admin_bp.route("/orders/<int:order_id>/notes", methods=["POST"])
@admin_required("orders")
def order_update_notes(order_id: int):
    conn = get_db()
    order = get_order_by_id(conn, order_id)
    if not order:
        abort(404)
    admin_note = (request.form.get("admin_note") or "").strip()
    customer_note = (request.form.get("customer_note") or "").strip()
    tracking_code = (request.form.get("tracking_code") or "").strip()
    carrier_name = (request.form.get("carrier_name") or "").strip()

    conn.execute(
        """
        UPDATE orders SET admin_note = ?, customer_note = ?, tracking_code = ?, carrier_name = ?, updated_at = ?
        WHERE id = ?
        """,
        (admin_note, customer_note, tracking_code, carrier_name, now_utc_iso(), order_id),
    )
    log_audit(
        conn,
        admin_id=int(g.current_user["id"]),
        admin_name=g.current_user.get("username") or g.current_user["full_name"],
        action="update_notes",
        entity_type="order",
        entity_id=order_id,
        ip_address=request.remote_addr or "",
        user_agent=request.headers.get("User-Agent", ""),
    )
    flash("یادداشت‌ها و اطلاعات ارسال ذخیره شد.", "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@admin_bp.route("/orders/<int:order_id>/edit", methods=["POST"])
@admin_required("orders")
def order_edit(order_id: int):
    conn = get_db()
    order = get_order_by_id(conn, order_id)
    if not order:
        abort(404)

    vids = request.form.getlist("edit_variant_id[]")
    qtys = request.form.getlist("edit_qty[]")
    new_items: list[tuple[int, int]] = []
    for vs, qs in zip(vids, qtys):
        vid = _to_int(vs, 0)
        qty = _to_int(qs, 0)
        if vid > 0 and qty > 0:
            new_items.append((vid, qty))

    try:
        updated = edit_order_admin(
            conn,
            order_id=order_id,
            admin_id=int(g.current_user["id"]),
            shipping_province=(request.form.get("shipping_province") or order["shipping_province"]),
            shipping_city=(request.form.get("shipping_city") or order["shipping_city"]),
            shipping_address=(request.form.get("shipping_address") or order["shipping_address"]),
            shipping_postal_code=(request.form.get("shipping_postal_code") or order["shipping_postal_code"]),
            shipping_fee_rial=max(0, _to_int(request.form.get("shipping_fee_toman"), 0)) * 10,
            discount_rial=max(0, _to_int(request.form.get("discount_toman"), 0)) * 10,
            new_items_input=new_items,
            customer_note=(request.form.get("customer_note") or order["customer_note"]),
            admin_note=(request.form.get("admin_note") or order["admin_note"]),
        )
        log_audit(
            conn,
            admin_id=int(g.current_user["id"]),
            admin_name=g.current_user.get("username") or g.current_user["full_name"],
            action="edit_order",
            entity_type="order",
            entity_id=order_id,
            before={"total_rial": order["total_rial"]},
            after={"total_rial": updated["total_rial"]},
            ip_address=request.remote_addr or "",
            user_agent=request.headers.get("User-Agent", ""),
        )
        flash("سفارش با موفقیت ویرایش و موجودی انبار به صورت خودکار تنظیم شد.", "success")
    except (InsufficientStockError, OrderError) as exc:
        flash(str(exc), "danger")

    return redirect(url_for("admin.order_detail", order_id=order_id))


@admin_bp.route("/orders/<int:order_id>/reverify", methods=["POST"])
@admin_required("orders")
def order_reverify_payment(order_id: int):
    conn = get_db()
    payment_id = _to_int(request.form.get("payment_id"), 0)
    pay = conn.execute(
        "SELECT * FROM payments WHERE id = ? AND order_id = ?", (payment_id, order_id)
    ).fetchone()
    if not pay or not pay["id_get"]:
        flash("تراکنش قابل استعلام یافت نشد.", "warning")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    trans_id = (request.form.get("trans_id") or pay["trans_id"] or "").strip()
    if not trans_id:
        flash("شناسه تراکنش (trans_id) برای استعلام لازم است.", "warning")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    gateway = get_payment_gateway(conn, base_url=request.host_url.rstrip("/"))
    ok, _, msg = process_payment_callback(
        conn, gateway=gateway, id_get=pay["id_get"], trans_id=trans_id
    )
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@admin_bp.route("/orders/<int:order_id>/print/<doc_type>")
@admin_required("orders")
def order_print(order_id: int, doc_type: str):
    if doc_type not in {"invoice", "packing"}:
        abort(404)
    conn = get_db()
    order = get_order_by_id(conn, order_id)
    if not order:
        abort(404)
    d = dict(order)
    d["items"] = get_order_items(conn, order_id)
    return render_template(
        "admin/orders_print.html",
        orders_data=[d],
        doc_type=doc_type,
        status_labels=ORDER_STATUS_LABELS,
        method_labels=PAYMENT_METHOD_LABELS,
    )


@admin_bp.route("/orders/<int:order_id>/delete", methods=["POST"])
@admin_required("orders_delete")
def order_delete(order_id: int):
    conn = get_db()
    order = get_order_by_id(conn, order_id)
    if not order:
        abort(404)
    num = order["order_number"]
    delete_order_super_admin(conn, order_id=order_id, admin_id=int(g.current_user["id"]))
    log_audit(
        conn,
        admin_id=int(g.current_user["id"]),
        admin_name=g.current_user.get("username") or g.current_user["full_name"],
        action="delete_test_order",
        entity_type="order",
        entity_id=order_id,
        before={"order_number": num},
        ip_address=request.remote_addr or "",
        user_agent=request.headers.get("User-Agent", ""),
    )
    flash(f"سفارش {num} حذف شد و موجودی آن (در صورت کسر) بازگردانده شد.", "success")
    return redirect(url_for("admin.orders_list"))
