"""مدیریت نظرات و ویدئوهای مشتریان."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import or_

from app.constants import REVIEW_STATUSES
from app.decorators import permission_required
from app.extensions import db
from app.models import CustomerVideo, Product, Review
from app.routes.admin import admin_bp
from app.routes.admin.helpers import log_admin, text_field
from app.services import media_service, product_service
from app.utils.dates import utcnow
from app.utils.pagination import paginate

# ---------------------------------------------------------------------------
# نظرات
# ---------------------------------------------------------------------------


@admin_bp.get("/reviews")
@permission_required("reviews.moderate")
def reviews():
    stmt = db.session.query(Review).order_by(Review.created_at.desc())
    status = request.args.get("status", "pending")
    if status in dict(REVIEW_STATUSES):
        stmt = stmt.filter(Review.status == status)
    q = (request.args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.filter(or_(Review.body.like(like), Review.title.like(like)))
    page = paginate(stmt, per_page=20)
    return render_template(
        "admin/reviews/list.html", page=page, statuses=REVIEW_STATUSES, status=status, q=q
    )


@admin_bp.post("/reviews/<int:review_id>/moderate")
@permission_required("reviews.moderate")
def review_moderate(review_id: int):
    review = db.session.get(Review, review_id)
    if review is None:
        abort(404)
    action = request.form.get("action")
    note = text_field("admin_note", max_length=300)
    if action not in {"approve", "reject"}:
        abort(400)
    review.status = "approved" if action == "approve" else "rejected"
    review.admin_note = note or None
    review.updated_at = utcnow()
    db.session.commit()
    product_service.recalc_rating(review.product_id)
    log_admin(f"review.{action}", "review", review.id, new={"status": review.status})
    flash("نظر تأیید شد." if action == "approve" else "نظر رد شد.", "success")
    return redirect(request.referrer or url_for("admin.reviews"))


@admin_bp.post("/reviews/<int:review_id>/delete")
@permission_required("reviews.moderate")
def review_delete(review_id: int):
    review = db.session.get(Review, review_id)
    if review is None:
        abort(404)
    product_id = review.product_id
    db.session.delete(review)
    db.session.commit()
    product_service.recalc_rating(product_id)
    log_admin("review.delete", "review", review_id)
    flash("نظر حذف شد.", "success")
    return redirect(request.referrer or url_for("admin.reviews"))


# ---------------------------------------------------------------------------
# ویدئوهای مشتریان
# ---------------------------------------------------------------------------


@admin_bp.get("/videos")
@permission_required("videos.moderate")
def videos():
    stmt = db.session.query(CustomerVideo).order_by(CustomerVideo.created_at.desc())
    status = request.args.get("status", "pending")
    if status in dict(REVIEW_STATUSES):
        stmt = stmt.filter(CustomerVideo.status == status)
    page = paginate(stmt, per_page=20)
    return render_template(
        "admin/videos/list.html", page=page, statuses=REVIEW_STATUSES, status=status
    )


@admin_bp.post("/videos/<int:video_id>/moderate")
@permission_required("videos.moderate")
def video_moderate(video_id: int):
    video = db.session.get(CustomerVideo, video_id)
    if video is None:
        abort(404)
    action = request.form.get("action")
    note = text_field("admin_note", max_length=300)
    if action not in {"approve", "reject"}:
        abort(400)
    video.status = "approved" if action == "approve" else "rejected"
    video.admin_note = note or None
    video.updated_at = utcnow()
    db.session.commit()
    log_admin(f"video.{action}", "customer_video", video.id, new={"status": video.status})
    flash("ویدئو تأیید و منتشر شد." if action == "approve" else "ویدئو رد شد.", "success")
    return redirect(request.referrer or url_for("admin.videos"))


@admin_bp.post("/videos/<int:video_id>/delete")
@permission_required("videos.moderate")
def video_delete(video_id: int):
    video = db.session.get(CustomerVideo, video_id)
    if video is None:
        abort(404)
    if video.file_path:
        media_service.delete_private(video.file_path)
    db.session.delete(video)
    db.session.commit()
    log_admin("video.delete", "customer_video", video_id)
    flash("ویدئو حذف شد.", "success")
    return redirect(url_for("admin.videos"))
