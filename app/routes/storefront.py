"""فروشگاه: خانه، فهرست، دسته‌بندی، جزئیات محصول، جستجو، مقایسه، وبلاگ و صفحات."""
from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func

from app.decorators import login_required
from app.extensions import db
from app.models import BlogPost, Category, CustomerVideo, Order, Page, Product, Review, Wishlist
from app.repositories import product_repository as repo
from app.services import (
    inventory_service,
    order_service,
    product_service,
    settings_service,
    support_service,
    media_service,
)
from app.utils.http import client_ip
from app.utils.security import is_valid_mobile, normalize_mobile, persian_search_normalize

storefront_bp = Blueprint("storefront", __name__)

COMPARE_SESSION_KEY = "compare"


@storefront_bp.get("/")
def home():
    limit = 8
    featured = repo.featured_products(limit)
    newest = repo.newest_products(limit)
    bestsellers = repo.bestseller_products(limit)
    discounted = repo.discounted_products(limit)
    categories = (
        db.session.query(Category)
        .filter(Category.is_active.is_(True), Category.parent_id.is_(None))
        .order_by(Category.sort_order, Category.id)
        .limit(8)
        .all()
    )
    posts = (
        db.session.query(BlogPost)
        .filter(BlogPost.status == "published")
        .order_by(BlogPost.published_at.desc())
        .limit(3)
        .all()
    )
    latest_videos = (
        db.session.query(CustomerVideo)
        .filter(CustomerVideo.status == "approved")
        .order_by(CustomerVideo.created_at.desc())
        .limit(4)
        .all()
    )
    stats = {
        "products": db.session.query(func.count(Product.id))
        .filter(Product.is_active.is_(True), Product.deleted_at.is_(None))
        .scalar()
        or 0,
        "categories": len(categories),
    }
    return render_template(
        "storefront/home.html",
        featured=featured,
        newest=newest,
        bestsellers=bestsellers,
        discounted=discounted,
        categories=categories,
        posts=posts,
        videos=latest_videos,
        stats=stats,
    )


@storefront_bp.get("/products")
def product_list():
    per_page = max(4, settings_service.get_int("products_per_page", 12))
    page, category = repo.search_page(request.args, per_page=per_page)
    min_price, max_price = repo.price_bounds()
    return render_template(
        "storefront/product_list.html",
        page=page,
        category=category,
        min_bound=min_price,
        max_bound=max_price,
        query=request.args.get("q", ""),
        args=request.args,
        sort_options=repo.SORT_OPTIONS,
        page_title="همه محصولات" if not category else category.name,
    )


@storefront_bp.get("/category/<slug>")
def category_page(slug: str):
    category = db.session.query(Category).filter(Category.slug == slug, Category.is_active.is_(True)).first()
    if category is None:
        abort(404)
    per_page = max(4, settings_service.get_int("products_per_page", 12))
    args = request.args.to_dict()
    args["category"] = slug
    page, _ = repo.search_page(args, per_page=per_page)
    min_price, max_price = repo.price_bounds()
    return render_template(
        "storefront/product_list.html",
        page=page,
        category=category,
        min_bound=min_price,
        max_bound=max_price,
        query=request.args.get("q", ""),
        args=request.args,
        sort_options=repo.SORT_OPTIONS,
        page_title=category.name,
    )


@storefront_bp.get("/search")
def search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return redirect(url_for("storefront.product_list"))
    per_page = max(4, settings_service.get_int("products_per_page", 12))
    page, _ = repo.search_page(request.args, per_page=per_page)
    min_price, max_price = repo.price_bounds()
    return render_template(
        "storefront/product_list.html",
        page=page,
        category=None,
        min_bound=min_price,
        max_bound=max_price,
        query=query,
        args=request.args,
        sort_options=repo.SORT_OPTIONS,
        page_title=f"نتایج جستجو برای «{query}»",
    )


@storefront_bp.get("/product/<slug>")
def product_detail(slug: str):
    product = repo.get_by_slug(slug)
    if product is None:
        abort(404)
    try:
        product.view_count = (product.view_count or 0) + 1
        db.session.commit()
    except Exception:  # pragma: no cover
        db.session.rollback()

    variants = sorted(
        product.active_variants,
        key=lambda variant: (not variant.is_default, variant.sort_order, variant.id),
    )
    selected = variants[0] if variants else product.default_variant
    stats = repo.review_stats(product.id)
    reviews = repo.approved_reviews(product.id, limit=30)
    videos = (
        db.session.query(CustomerVideo)
        .filter(CustomerVideo.product_id == product.id, CustomerVideo.status == "approved")
        .order_by(CustomerVideo.created_at.desc())
        .limit(12)
        .all()
    )
    related = repo.related_products(product, limit=max(2, settings_service.get_int("related_products_count", 4)))
    in_wishlist = False
    if getattr(g, "user", None) is not None:
        in_wishlist = (
            db.session.query(Wishlist.id)
            .filter(Wishlist.user_id == g.user.id, Wishlist.product_id == product.id)
            .first()
            is not None
        )
    can_review = False
    purchase_item = None
    if getattr(g, "user", None) is not None:
        from app.models import OrderItem

        purchase_item = (
            db.session.query(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .filter(
                Order.user_id == g.user.id,
                OrderItem.product_id == product.id,
                Order.payment_status == "paid",
            )
            .first()
        )
        can_review = purchase_item is not None or not settings_service.get_bool("review_require_purchase", False)
        already = (
            db.session.query(Review.id)
            .filter(Review.user_id == g.user.id, Review.product_id == product.id)
            .first()
        )
        if already:
            can_review = False

    return render_template(
        "storefront/product_detail.html",
        product=product,
        variants=variants,
        selected=selected,
        stats=stats,
        reviews=reviews,
        videos=videos,
        related=related,
        in_wishlist=in_wishlist,
        can_review=can_review,
        purchase_item=purchase_item,
        stock_class=product_service.stock_status_label(product)[0],
        stock_label=product_service.stock_status_label(product)[1],
    )


@storefront_bp.get("/compare")
def compare():
    ids = session.get(COMPARE_SESSION_KEY) or []
    products = repo.products_by_ids([int(i) for i in ids if str(i).isdigit()])
    spec_names: list[str] = []
    for product in products:
        for item in product.specifications_list:
            if item["name"] not in spec_names:
                spec_names.append(item["name"])
    return render_template(
        "storefront/compare.html",
        products=products,
        spec_names=spec_names,
        limit=settings_service.get_int("comparison_limit", 4),
    )


@storefront_bp.post("/compare/add/<int:product_id>")
def compare_add(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None or not product.is_active:
        abort(404)
    ids = [int(i) for i in (session.get(COMPARE_SESSION_KEY) or []) if str(i).isdigit()]
    limit = max(2, settings_service.get_int("comparison_limit", 4))
    if product_id not in ids:
        if len(ids) >= limit:
            flash(f"حداکثر {limit} محصول را می‌توانید مقایسه کنید.", "warning")
            return redirect(request.referrer or url_for("storefront.compare"))
        ids.append(product_id)
        session[COMPARE_SESSION_KEY] = ids
        session.modified = True
        flash("محصول به لیست مقایسه اضافه شد.", "success")
    return redirect(request.referrer or url_for("storefront.compare"))


@storefront_bp.post("/compare/remove/<int:product_id>")
def compare_remove(product_id: int):
    ids = [int(i) for i in (session.get(COMPARE_SESSION_KEY) or []) if str(i).isdigit()]
    session[COMPARE_SESSION_KEY] = [i for i in ids if i != product_id]
    session.modified = True
    flash("محصول از لیست مقایسه حذف شد.", "info")
    return redirect(request.referrer or url_for("storefront.compare"))


@storefront_bp.get("/wishlist")
@login_required
def wishlist():
    if not settings_service.get_bool("wishlist_enabled", True):
        abort(404)
    items = (
        db.session.query(Wishlist)
        .filter(Wishlist.user_id == g.user.id)
        .order_by(Wishlist.created_at.desc())
        .all()
    )
    products = [wishlist.product for wishlist in items if wishlist.product is not None]
    return render_template("storefront/wishlist.html", products=products)


@storefront_bp.get("/blog")
def blog_list():
    page_number = request.args.get("page", 1, type=int)
    per_page = 9
    query = db.session.query(BlogPost).filter(BlogPost.status == "published")
    total = query.count()
    posts = (
        query.order_by(BlogPost.published_at.desc())
        .limit(per_page)
        .offset((max(1, page_number) - 1) * per_page)
        .all()
    )
    pages = max(1, (total + per_page - 1) // per_page)
    return render_template(
        "storefront/blog_list.html",
        posts=posts,
        page=page_number,
        pages=pages,
        total=total,
    )


@storefront_bp.get("/blog/<slug>")
def blog_detail(slug: str):
    post = (
        db.session.query(BlogPost)
        .filter(BlogPost.slug == slug, BlogPost.status == "published")
        .first()
    )
    if post is None:
        abort(404)
    post.views = (post.views or 0) + 1
    db.session.commit()
    related = (
        db.session.query(BlogPost)
        .filter(BlogPost.status == "published", BlogPost.id != post.id)
        .order_by(BlogPost.published_at.desc())
        .limit(3)
        .all()
    )
    return render_template("storefront/blog_detail.html", post=post, related=related)


@storefront_bp.get("/page/<slug>")
@storefront_bp.get("/p/<slug>")
def static_page(slug: str):
    page = db.session.query(Page).filter(Page.slug == slug, Page.is_published.is_(True)).first()
    if page is None:
        abort(404)
    return render_template("storefront/page.html", page=page)


@storefront_bp.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        ticket, errors = support_service.create_ticket(
            user=getattr(g, "user", None),
            subject=request.form.get("subject", "تماس با ما"),
            message=request.form.get("message", ""),
            department="sales",
            priority="normal",
            guest_name=request.form.get("name", ""),
            guest_mobile=request.form.get("mobile", ""),
            guest_email=request.form.get("email", ""),
        )
        if ticket is not None:
            flash(f"پیام شما ثبت شد. شماره پیگیری: {ticket.ticket_number}", "success")
            return redirect(url_for("storefront.contact"))
        for error in errors:
            flash(error, "error")
    return render_template("storefront/contact.html")


@storefront_bp.route("/track-order", methods=["GET", "POST"])
def track_order():
    order_number = (request.args.get("order") or request.form.get("order_number") or "").strip()
    if request.method == "POST":
        mobile = normalize_mobile(request.form.get("mobile", ""))
        order = order_service.find_by_number(order_number)
        generic_error = "سفارشی با این مشخصات یافت نشد یا شماره موبایل مطابقت ندارد."
        if order is None or not is_valid_mobile(mobile):
            flash(generic_error, "error")
            return render_template("storefront/track_order.html", order_number=order_number)
        if order.customer_mobile and normalize_mobile(order.customer_mobile) != mobile:
            flash(generic_error, "error")
            return render_template("storefront/track_order.html", order_number=order_number)
        if settings_service.get_bool("otp_required_for_orders", False):
            from app.services import otp_service

            try:
                otp_service.send_code(mobile, "track_order", client_ip())
            except otp_service.OtpError as exc:
                flash(exc.message, "error")
                return render_template("storefront/track_order.html", order_number=order_number)
            session["track_order_context"] = {"order_number": order.order_number, "mobile": mobile}
            flash("کد تأیید به شماره ثبت‌شده در سفارش ارسال شد.", "success")
            return render_template("storefront/track_order.html", order_number=order_number, needs_code=True)
        order_service.grant_guest_access(order)
        return redirect(url_for("storefront.order_view", order_number=order.order_number))
    return render_template("storefront/track_order.html", order_number=order_number)


@storefront_bp.post("/track-order/verify")
def track_order_verify():
    context = session.get("track_order_context") or {}
    from app.services import otp_service

    if not context:
        flash("نشست پیگیری منقضی شده است.", "warning")
        return redirect(url_for("storefront.track_order"))
    ok, message = otp_service.verify_code(context.get("mobile", ""), request.form.get("code", ""), "track_order")
    if not ok:
        flash(message, "error")
        return render_template("storefront/track_order.html", order_number=context.get("order_number"), needs_code=True)
    session.pop("track_order_context", None)
    order = order_service.find_by_number(context["order_number"])
    if order is None:
        flash("سفارش یافت نشد.", "error")
        return redirect(url_for("storefront.track_order"))
    order_service.grant_guest_access(order)
    return redirect(url_for("storefront.order_view", order_number=order.order_number))


@storefront_bp.get("/order/<order_number>")
def order_view(order_number: str):
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    if not order_service.can_access(order, getattr(g, "user", None)):
        flash("برای مشاهده این سفارش، مالکیت خود را تأیید کنید.", "warning")
        return redirect(url_for("storefront.track_order", order=order_number))
    return render_template("storefront/order_view.html", order=order)


@storefront_bp.get("/video/<int:video_id>")
def customer_video(video_id: int):
    video = db.session.get(CustomerVideo, video_id)
    if video is None:
        abort(404)
    user = getattr(g, "user", None)
    is_owner = user is not None and video.user_id == user.id
    is_admin_user = user is not None and user.is_admin
    if video.status != "approved" and not (is_owner or is_admin_user):
        abort(404)
    if not video.file_path:
        abort(404)
    from flask import send_file

    from app.services.media_service import private_file_path

    path = private_file_path(video.file_path)
    if path is None:
        abort(404)
    return send_file(path, conditional=True, max_age=3600)
