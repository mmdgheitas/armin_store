"""مدیریت محتوا: وبلاگ و صفحات ثابت."""
from __future__ import annotations

from flask import abort, flash, g, redirect, render_template, request, url_for

from app.decorators import permission_required
from app.extensions import db
from app.models import BlogCategory, BlogPost, Page
from app.routes.admin import admin_bp
from app.routes.admin.helpers import bool_field, int_field, log_admin, text_field
from app.services import media_service, product_service, settings_service
from app.utils.dates import parse_date, utcnow
from app.utils.files import FileValidationError
from app.utils.pagination import paginate
from app.utils.security import sanitize_html, slugify

# ---------------------------------------------------------------------------
# وبلاگ
# ---------------------------------------------------------------------------


@admin_bp.get("/blog")
@permission_required("blog.manage")
def blog_posts():
    stmt = db.session.query(BlogPost).order_by(BlogPost.created_at.desc())
    status = request.args.get("status")
    if status in {"published", "draft"}:
        stmt = stmt.filter(BlogPost.status == status)
    page = paginate(stmt, per_page=20)
    return render_template("admin/blog/list.html", page=page, status=status)


@admin_bp.route("/blog/create", methods=["GET", "POST"])
@admin_bp.route("/blog/<int:post_id>/edit", methods=["GET", "POST"])
@permission_required("blog.manage")
def blog_save(post_id: int | None = None):
    post = db.session.get(BlogPost, post_id) if post_id else None
    if post_id and post is None:
        abort(404)
    categories = db.session.query(BlogCategory).order_by(BlogCategory.name).all()

    if request.method == "POST":
        title = text_field("title", max_length=220)
        content = sanitize_html(request.form.get("content", ""))
        errors = []
        if len(title) < 3:
            errors.append("عنوان مقاله را وارد کنید.")
        if len(content) < 20:
            errors.append("متن مقاله باید حداقل ۲۰ کاراکتر باشد.")
        if errors:
            for message in errors:
                flash(message, "error")
            return render_template(
                "admin/blog/form.html", post=post, categories=categories, form=request.form
            )

        cover = post.cover_image if post else None
        file = request.files.get("cover")
        if file is not None and file.filename:
            try:
                saved = media_service.save_upload(
                    file, "blog", kind="image", max_mb=settings_service.get_int("image_max_mb", 5)
                )
                cover = saved["path"]
            except FileValidationError as exc:
                flash(exc.message, "error")
                return render_template(
                    "admin/blog/form.html", post=post, categories=categories, form=request.form
                )

        category_name = text_field("new_category", max_length=120)
        category_id = int_field("category_id") or None
        if category_name:
            category = BlogCategory(name=category_name, slug=slugify(category_name, 140), created_at=utcnow(), updated_at=utcnow())
            db.session.add(category)
            db.session.flush()
            category_id = category.id

        base_slug = text_field("slug") or slugify(title, 200)
        duplicate = (
            db.session.query(BlogPost.id)
            .filter(BlogPost.slug == base_slug, BlogPost.id != (post.id if post else 0))
            .first()
        )
        slug_value = base_slug if not duplicate else f"{base_slug}-{int(utcnow().timestamp())}"
        if post is None:
            post = BlogPost(created_at=utcnow(), updated_at=utcnow(), author_id=None)
            db.session.add(post)
        post.title = title
        post.slug = slug_value
        post.category_id = category_id
        post.excerpt = text_field("excerpt", max_length=400) or None
        post.content = content
        post.cover_image = cover
        post.status = "published" if request.form.get("status") == "published" else "draft"
        post.seo_title = text_field("seo_title", max_length=200) or None
        post.seo_description = text_field("seo_description", max_length=300) or None
        post.author_id = g.user.id
        published_at = parse_date(request.form.get("published_at"))
        post.published_at = utcnow() if post.status == "published" else None
        if published_at and post.status == "published":
            from datetime import datetime, timedelta

            post.published_at = datetime(
                published_at.year, published_at.month, published_at.day, 12
            ) - timedelta(hours=3, minutes=30)
        post.updated_at = utcnow()
        db.session.commit()
        log_admin("blog.save", "blog_post", post.id, new={"title": post.title, "status": post.status})
        flash("مقاله ذخیره شد.", "success")
        return redirect(url_for("admin.blog_posts"))

    return render_template("admin/blog/form.html", post=post, categories=categories, form=None)


@admin_bp.post("/blog/<int:post_id>/delete")
@permission_required("blog.manage")
def blog_delete(post_id: int):
    post = db.session.get(BlogPost, post_id)
    if post is None:
        abort(404)
    if post.cover_image:
        media_service.delete_public(post.cover_image)
    db.session.delete(post)
    db.session.commit()
    log_admin("blog.delete", "blog_post", post_id)
    flash("مقاله حذف شد.", "success")
    return redirect(url_for("admin.blog_posts"))


# ---------------------------------------------------------------------------
# صفحات ثابت
# ---------------------------------------------------------------------------


@admin_bp.get("/pages")
@permission_required("pages.manage")
def pages():
    items = db.session.query(Page).order_by(Page.sort_order, Page.title).all()
    return render_template("admin/pages/list.html", pages=items)


@admin_bp.route("/pages/create", methods=["GET", "POST"])
@admin_bp.route("/pages/<int:page_id>/edit", methods=["GET", "POST"])
@permission_required("pages.manage")
def page_save(page_id: int | None = None):
    page_obj = db.session.get(Page, page_id) if page_id else None
    if page_id and page_obj is None:
        abort(404)
    if request.method == "POST":
        title = text_field("title", max_length=200)
        content = sanitize_html(request.form.get("content", ""))
        if len(title) < 2 or len(content) < 10:
            flash("عنوان و محتوای صفحه را کامل وارد کنید.", "error")
            return render_template("admin/pages/form.html", page_item=page_obj, form=request.form)

        base_slug = text_field("slug") or slugify(title, 200)
        duplicate = (
            db.session.query(Page.id)
            .filter(Page.slug == base_slug, Page.id != (page_obj.id if page_obj else 0))
            .first()
        )
        slug_value = base_slug if not duplicate else f"{base_slug}-{int(utcnow().timestamp())}"
        if page_obj is None:
            page_obj = Page(created_at=utcnow(), updated_at=utcnow())
            db.session.add(page_obj)
        page_obj.title = title
        page_obj.slug = slug_value
        page_obj.content = content
        page_obj.seo_title = text_field("seo_title", max_length=200) or None
        page_obj.seo_description = text_field("seo_description", max_length=300) or None
        page_obj.is_published = "is_published" in request.form
        page_obj.show_in_footer = "show_in_footer" in request.form
        page_obj.sort_order = int_field("sort_order")
        page_obj.updated_at = utcnow()
        db.session.commit()
        log_admin("page.save", "page", page_obj.id, new={"slug": page_obj.slug})
        flash("صفحه ذخیره شد.", "success")
        return redirect(url_for("admin.pages"))

    return render_template("admin/pages/form.html", page_item=page_obj, form=None)


@admin_bp.post("/pages/<int:page_id>/toggle")
@permission_required("pages.manage")
def page_toggle(page_id: int):
    page_obj = db.session.get(Page, page_id)
    if page_obj is None:
        abort(404)
    page_obj.is_published = not page_obj.is_published
    page_obj.updated_at = utcnow()
    db.session.commit()
    log_admin("page.toggle", "page", page_id, new={"is_published": page_obj.is_published})
    flash("وضعیت صفحه تغییر کرد.", "success")
    return redirect(url_for("admin.pages"))


@admin_bp.post("/pages/<int:page_id>/delete")
@permission_required("pages.manage")
def page_delete(page_id: int):
    page_obj = db.session.get(Page, page_id)
    if page_obj is None:
        abort(404)
    db.session.delete(page_obj)
    db.session.commit()
    log_admin("page.delete", "page", page_id)
    flash("صفحه حذف شد.", "success")
    return redirect(url_for("admin.pages"))
