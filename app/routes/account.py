"""پنل حساب مشتری: سفارش‌ها، دانلودها، آدرس‌ها، نظرات، ویدئوها و تیکت‌ها."""
from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from app.decorators import login_required
from app.extensions import db
from app.models import (
    CustomerVideo,
    Order,
    OrderItem,
    Product,
    Review,
    Address,
    Ticket,
    Notification,
)
from app.services import (
    auth_service,
    digital_delivery_service,
    media_service,
    order_service,
    product_service,
    settings_service,
    support_service,
)
from app.utils.dates import utcnow
from app.utils.files import FileValidationError
from app.utils.pagination import paginate
from app.utils.security import is_valid_mobile, normalize_mobile

account_bp = Blueprint("account", __name__, url_prefix="/account")


def _owned_order(order_number: str) -> Order:
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    if not order_service.can_access(order, g.user):
        abort(403)
    return order


@account_bp.get("/")
@login_required
def dashboard():
    orders_count = db.session.query(Order).filter(Order.user_id == g.user.id).count()
    paid_count = (
        db.session.query(Order)
        .filter(Order.user_id == g.user.id, Order.payment_status == "paid")
        .count()
    )
    recent_orders = (
        db.session.query(Order)
        .filter(Order.user_id == g.user.id)
        .order_by(Order.created_at.desc())
        .limit(5)
        .all()
    )
    notifications = (
        db.session.query(Notification)
        .filter(Notification.user_id == g.user.id)
        .order_by(Notification.created_at.desc())
        .limit(5)
        .all()
    )
    digital_items = (
        db.session.query(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            Order.user_id == g.user.id,
            Order.payment_status == "paid",
            OrderItem.delivery_type == "digital",
        )
        .count()
    )
    return render_template(
        "account/dashboard.html",
        orders_count=orders_count,
        paid_count=paid_count,
        recent_orders=recent_orders,
        notifications=notifications,
        digital_items=digital_items,
    )


@account_bp.get("/orders")
@login_required
def orders():
    query = (
        db.session.query(Order)
        .filter(Order.user_id == g.user.id)
        .order_by(Order.created_at.desc())
    )
    status = request.args.get("status")
    if status:
        query = query.filter(Order.status == status)
    page = paginate(query)
    return render_template("account/orders.html", page=page, status=status)


@account_bp.get("/orders/<order_number>")
@login_required
def order_detail(order_number: str):
    order = _owned_order(order_number)
    downloadable = {}
    if order.is_paid:
        digital_delivery_service.ensure_tokens(order)
        for item in order.items:
            if item.delivery_type == "digital":
                downloadable[item.id] = digital_delivery_service.item_state(item)
    return render_template(
        "account/order_detail.html",
        order=order,
        downloadable=downloadable,
        can_cancel=order.can_be_cancelled and not order.is_paid,
    )


@account_bp.post("/orders/<order_number>/cancel")
@login_required
def order_cancel(order_number: str):
    order = _owned_order(order_number)
    if order.is_paid:
        flash("سفارش پرداخت‌شده از طریق پشتیبانی قابل پیگیری است.", "warning")
        return redirect(url_for("account.order_detail", order_number=order.order_number))
    ok, message = order_service.cancel_order(order, reason="لغو توسط مشتری")
    flash(message, "success" if ok else "error")
    return redirect(url_for("account.order_detail", order_number=order.order_number))


@account_bp.get("/downloads")
@login_required
def downloads():
    items = (
        db.session.query(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            Order.user_id == g.user.id,
            Order.payment_status == "paid",
            OrderItem.delivery_type == "digital",
        )
        .order_by(Order.paid_at.desc())
        .all()
    )
    states = {}
    for item in items:
        states[item.id] = digital_delivery_service.item_state(item)
    return render_template("account/downloads.html", items=items, states=states)


@account_bp.get("/download/<int:item_id>")
@login_required
def download_item(item_id: int):
    item = db.session.get(OrderItem, item_id)
    if item is None:
        abort(404)
    order = item.order
    if order is None or order.user_id != g.user.id:
        abort(403)
    if not order.is_paid:
        flash("این فایل فقط پس از تأیید پرداخت قابل دانلود است.", "warning")
        return redirect(url_for("account.downloads"))
    signature = request.args.get("sig", "")
    if item.delivery_type != "digital" or not digital_delivery_service.verify_signature(item, signature):
        abort(403)

    tokens = digital_delivery_service.ensure_tokens(order)
    token = next((t for t in tokens if t.order_item_id == item.id), None)
    if token is None:
        flash("فایل این محصول در دسترس نیست.", "error")
        return redirect(url_for("account.downloads"))
    if token.is_expired:
        flash("مهلت دانلود این محصول به پایان رسیده است.", "error")
        return redirect(url_for("account.downloads"))
    if token.is_exhausted:
        flash("سقف تعداد دانلود این محصول تکمیل شده است.", "error")
        return redirect(url_for("account.downloads"))

    asset = digital_delivery_service.asset_for_item(item)
    if asset is None:
        abort(404)

    if asset.delivery_type == "link":
        if not asset.external_url:
            abort(404)
        digital_delivery_service.register_download(item, token)
        return redirect(asset.external_url)

    if asset.delivery_type == "license" and not asset.file_path:
        return render_template(
            "account/license.html",
            item=item,
            token=token,
            order=order,
        )

    path = media_service.private_file_path(asset.file_path)
    if path is None:
        flash("فایل این محصول یافت نشد. با پشتیبانی تماس بگیرید.", "error")
        return redirect(url_for("account.downloads"))
    digital_delivery_service.register_download(item, token)
    filename = f"{item.product_name_snapshot}-{item.sku_snapshot or item.id}{path.suffix}"
    return send_file(path, as_attachment=True, download_name=filename, conditional=True)


@account_bp.get("/addresses")
@login_required
def addresses():
    return render_template("account/addresses.html", addresses=g.user.addresses)


@account_bp.post("/addresses")
@login_required
def address_create():
    address, errors = order_service.save_address(
        g.user, request.form.to_dict(), make_default=bool(request.form.get("is_default"))
    )
    if address is None:
        for message in errors:
            flash(message, "error")
    else:
        flash("آدرس جدید ذخیره شد.", "success")
    return redirect(url_for("account.addresses"))


@account_bp.post("/addresses/<int:address_id>")
@login_required
def address_update(address_id: int):
    address = db.session.get(Address, address_id)
    if address is None or address.user_id != g.user.id:
        abort(404)
    data = order_service.build_address_dict(request.form.to_dict())
    if len(data["recipient_name"]) < 3 or not is_valid_mobile(data["mobile"]) or len(data["full_address"]) < 10:
        flash("اطلاعات آدرس کامل نیست.", "error")
        return redirect(url_for("account.addresses"))
    address.recipient_name = data["recipient_name"]
    address.mobile = data["mobile"]
    address.province = data["province"]
    address.city = data["city"]
    address.full_address = data["full_address"]
    address.postal_code = data["postal_code"] or None
    address.national_code = data["national_code"] or None
    address.updated_at = utcnow()
    if request.form.get("is_default"):
        for other in g.user.addresses:
            other.is_default = other.id == address.id
    db.session.commit()
    flash("آدرس به‌روزرسانی شد.", "success")
    return redirect(url_for("account.addresses"))


@account_bp.post("/addresses/<int:address_id>/delete")
@login_required
def address_delete(address_id: int):
    address = db.session.get(Address, address_id)
    if address is None or address.user_id != g.user.id:
        abort(404)
    db.session.delete(address)
    db.session.commit()
    flash("آدرس حذف شد.", "info")
    return redirect(url_for("account.addresses"))


@account_bp.post("/addresses/<int:address_id>/default")
@login_required
def address_default(address_id: int):
    address = db.session.get(Address, address_id)
    if address is None or address.user_id != g.user.id:
        abort(404)
    for other in g.user.addresses:
        other.is_default = other.id == address.id
    db.session.commit()
    flash("آدرس پیش‌فرض تغییر کرد.", "success")
    return redirect(url_for("account.addresses"))


@account_bp.get("/reviews")
@login_required
def reviews():
    items = (
        db.session.query(Review)
        .filter(Review.user_id == g.user.id)
        .order_by(Review.created_at.desc())
        .all()
    )
    return render_template("account/reviews.html", reviews=items)


@account_bp.post("/reviews")
@login_required
def review_create():
    product = db.session.get(Product, request.form.get("product_id", type=int) or 0)
    if product is None:
        abort(404)
    rating = request.form.get("rating", type=int) or 0
    body = (request.form.get("body") or "").strip()
    title = (request.form.get("title") or "").strip()
    target = url_for("storefront.product_detail", slug=product.slug) + "#reviews"

    if rating not in {1, 2, 3, 4, 5}:
        flash("امتیاز را انتخاب کنید (۱ تا ۵).", "error")
        return redirect(target)
    if len(body) < 10:
        flash("متن نظر باید حداقل ۱۰ کاراکتر باشد.", "error")
        return redirect(target)
    duplicate = (
        db.session.query(Review.id)
        .filter(Review.user_id == g.user.id, Review.product_id == product.id)
        .first()
    )
    if duplicate:
        flash("شما قبلاً برای این محصول نظر ثبت کرده‌اید.", "warning")
        return redirect(target)

    purchase = (
        db.session.query(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            Order.user_id == g.user.id,
            OrderItem.product_id == product.id,
            Order.payment_status == "paid",
            Order.status.notin_(["cancelled", "returned"]),
        )
        .first()
    )
    if settings_service.get_bool("review_require_purchase", False) and purchase is None:
        flash("ثبت نظر فقط برای خریداران این محصول مجاز است.", "error")
        return redirect(target)

    status = "approved" if settings_service.get_bool("review_auto_approve", False) else "pending"
    review = Review(
        product_id=product.id,
        user_id=g.user.id,
        order_item_id=purchase.id if purchase else None,
        rating=rating,
        title=title[:180] or None,
        body=body[:4000],
        is_verified_purchase=purchase is not None,
        status=status,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.session.add(review)
    db.session.commit()
    if status == "approved":
        product_service.recalc_rating(product.id)
        flash("نظر شما ثبت و منتشر شد.", "success")
    else:
        flash("نظر شما ثبت شد و پس از تأیید مدیر منتشر می‌شود.", "success")
    return redirect(target)


@account_bp.get("/videos")
@login_required
def videos():
    enabled = settings_service.get_bool("customer_video_enabled", True)
    items = (
        db.session.query(CustomerVideo)
        .filter(CustomerVideo.user_id == g.user.id)
        .order_by(CustomerVideo.created_at.desc())
        .all()
    )
    purchasable = (
        db.session.query(Product)
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.user_id == g.user.id, Order.payment_status == "paid")
        .distinct()
        .all()
    )
    return render_template(
        "account/videos.html", videos=items, products=purchasable, enabled=enabled
    )


@account_bp.post("/videos")
@login_required
def video_upload():
    if not settings_service.get_bool("customer_video_enabled", True):
        flash("ارسال ویدئو توسط مشتریان غیرفعال است.", "warning")
        return redirect(url_for("account.videos"))
    product = db.session.get(Product, request.form.get("product_id", type=int) or 0)
    if product is None:
        abort(404)

    purchase = (
        db.session.query(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            Order.user_id == g.user.id,
            OrderItem.product_id == product.id,
            Order.payment_status == "paid",
            Order.status.notin_(["cancelled", "returned"]),
        )
        .first()
    )
    if purchase is None:
        flash("ارسال ویدئو فقط برای خریداران تأییدشده همین محصول مجاز است.", "error")
        return redirect(url_for("account.videos"))

    file = request.files.get("video")
    if file is None or not file.filename:
        flash("فایل ویدئو را انتخاب کنید.", "error")
        return redirect(url_for("account.videos"))
    max_mb = max(1, settings_service.get_int("customer_video_max_mb", 60))
    try:
        saved = media_service.save_upload(file, "customer-videos", kind="video", max_mb=max_mb, private=True)
    except FileValidationError as exc:
        flash(exc.message, "error")
        return redirect(url_for("account.videos"))

    db.session.add(
        CustomerVideo(
            product_id=product.id,
            user_id=g.user.id,
            order_item_id=purchase.id,
            file_path=saved["path"],
            original_name=saved["name"],
            description=(request.form.get("description") or "").strip()[:2000] or None,
            status="pending",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db.session.commit()
    flash("ویدئوی شما ثبت شد و پس از تأیید مدیر در صفحه محصول نمایش داده می‌شود.", "success")
    return redirect(url_for("account.videos"))


@account_bp.post("/videos/<int:video_id>/delete")
@login_required
def video_delete(video_id: int):
    video = db.session.get(CustomerVideo, video_id)
    if video is None or video.user_id != g.user.id:
        abort(404)
    if video.status == "approved":
        flash("برای حذف ویدئوی منتشرشده با پشتیبانی تماس بگیرید.", "warning")
        return redirect(url_for("account.videos"))
    if video.file_path:
        media_service.delete_private(video.file_path)
    db.session.delete(video)
    db.session.commit()
    flash("ویدئو حذف شد.", "info")
    return redirect(url_for("account.videos"))


@account_bp.get("/tickets")
@login_required
def tickets():
    page = paginate(
        db.session.query(Ticket)
        .filter(Ticket.user_id == g.user.id)
        .order_by(Ticket.updated_at.desc())
    )
    return render_template("account/tickets.html", page=page)


@account_bp.post("/tickets")
@login_required
def ticket_create():
    ticket, errors = support_service.create_ticket(
        user=g.user,
        subject=request.form.get("subject", ""),
        message=request.form.get("message", ""),
        department=request.form.get("department", "support"),
        priority=request.form.get("priority", "normal"),
    )
    if ticket is None:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("account.tickets"))
    flash(f"تیکت {ticket.ticket_number} ثبت شد.", "success")
    return redirect(url_for("account.ticket_detail", ticket_id=ticket.id))


@account_bp.get("/tickets/<int:ticket_id>")
@login_required
def ticket_detail(ticket_id: int):
    ticket = db.session.get(Ticket, ticket_id)
    if ticket is None or not support_service.can_access(ticket, g.user):
        abort(404)
    return render_template(
        "account/ticket_detail.html",
        ticket=ticket,
        messages=support_service.ticket_messages(ticket),
    )


@account_bp.post("/tickets/<int:ticket_id>/reply")
@login_required
def ticket_reply(ticket_id: int):
    ticket = db.session.get(Ticket, ticket_id)
    if ticket is None or not support_service.can_access(ticket, g.user):
        abort(404)
    if ticket.status == "closed":
        flash("این تیکت بسته شده است.", "warning")
        return redirect(url_for("account.ticket_detail", ticket_id=ticket.id))

    attachment = None
    file = request.files.get("attachment")
    if file is not None and file.filename:
        try:
            saved = media_service.save_upload(
                file,
                "tickets",
                kind="document",
                max_mb=max(1, settings_service.get_int("ticket_attachment_max_mb", 5)),
                private=True,
            )
            attachment = {"path": saved["path"], "name": saved["name"]}
        except FileValidationError as exc:
            flash(exc.message, "error")
            return redirect(url_for("account.ticket_detail", ticket_id=ticket.id))

    ok, message = support_service.add_message(
        ticket, request.form.get("message", ""), g.user, is_admin_reply=False, attachment=attachment
    )
    flash(message, "success" if ok else "error")
    return redirect(url_for("account.ticket_detail", ticket_id=ticket.id))


@account_bp.post("/tickets/<int:ticket_id>/close")
@login_required
def ticket_close(ticket_id: int):
    ticket = db.session.get(Ticket, ticket_id)
    if ticket is None or not support_service.can_access(ticket, g.user):
        abort(404)
    ok, message = support_service.close_ticket(ticket, g.user)
    flash(message, "success" if ok else "error")
    return redirect(url_for("account.ticket_detail", ticket_id=ticket.id))


@account_bp.get("/tickets/<int:ticket_id>/attachment/<int:message_id>")
@login_required
def ticket_attachment(ticket_id: int, message_id: int):
    from app.models import TicketMessage

    ticket = db.session.get(Ticket, ticket_id)
    if ticket is None or not support_service.can_access(ticket, g.user):
        abort(404)
    message = db.session.get(TicketMessage, message_id)
    if message is None or message.ticket_id != ticket.id or not message.attachment_path:
        abort(404)
    path = media_service.private_file_path(message.attachment_path)
    if path is None:
        abort(404)
    return send_file(path, as_attachment=True, download_name=message.attachment_name or path.name)


@account_bp.get("/notifications")
@login_required
def notifications():
    items = (
        db.session.query(Notification)
        .filter(Notification.user_id == g.user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template("account/notifications.html", notifications=items)


@account_bp.post("/notifications/read")
@login_required
def notifications_read():
    query = db.session.query(Notification).filter(
        Notification.user_id == g.user.id, Notification.is_read.is_(False)
    )
    notification_id = request.form.get("id")
    if notification_id:
        query = query.filter(Notification.id == notification_id)
    query.update({Notification.is_read: True}, synchronize_session=False)
    db.session.commit()
    return redirect(request.form.get("next") or url_for("account.notifications"))


@account_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        if len(full_name) < 3:
            flash("نام و نام خانوادگی را کامل وارد کنید.", "error")
        else:
            g.user.full_name = full_name[:150]
            g.user.email = email[:190] or None
            g.user.updated_at = utcnow()
            db.session.commit()
            flash("اطلاعات حساب به‌روزرسانی شد.", "success")
            return redirect(url_for("account.profile"))
    return render_template("account/profile.html")


@account_bp.route("/password", methods=["GET", "POST"])
@login_required
def password():
    if request.method == "POST":
        errors = auth_service.change_password(
            g.user,
            request.form.get("current_password", ""),
            request.form.get("new_password", ""),
            request.form.get("password_confirm", ""),
        )
        if errors:
            for message in errors:
                flash(message, "error")
        else:
            flash("رمز عبور شما تغییر کرد.", "success")
            return redirect(url_for("account.profile"))
    return render_template("account/password.html", must_change=g.user.must_change_password)
