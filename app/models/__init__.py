"""ثبت تمام مدل‌ها برای ساخت خودکار جداول."""
from app.models.catalog import Category, DigitalAsset, Product, ProductMedia, ProductVariant
from app.models.content import (
    AuditLog,
    BackupLog,
    BlogCategory,
    BlogPost,
    Expense,
    Notification,
    OtpCode,
    Page,
    RateLimitEntry,
    Setting,
    SmsLog,
)
from app.models.delivery import DownloadToken
from app.models.inventory import InventoryMovement, InventoryReservation
from app.models.marketing import Advertisement, Coupon, CouponRedemption, Wishlist
from app.models.order import Order, OrderItem, OrderStatusHistory, Payment
from app.models.review import CustomerVideo, Review
from app.models.shipping import ShippingMethod
from app.models.support import Ticket, TicketMessage
from app.models.user import Address, Permission, Role, User, role_permissions, user_roles

__all__ = [
    "Address",
    "Advertisement",
    "AuditLog",
    "BackupLog",
    "BlogCategory",
    "BlogPost",
    "Category",
    "Coupon",
    "CouponRedemption",
    "CustomerVideo",
    "DigitalAsset",
    "DownloadToken",
    "Expense",
    "InventoryMovement",
    "InventoryReservation",
    "Notification",
    "Order",
    "OrderItem",
    "OrderStatusHistory",
    "OtpCode",
    "Page",
    "Payment",
    "Permission",
    "Product",
    "ProductMedia",
    "ProductVariant",
    "RateLimitEntry",
    "Review",
    "Role",
    "Setting",
    "ShippingMethod",
    "SmsLog",
    "Ticket",
    "TicketMessage",
    "User",
    "Wishlist",
    "role_permissions",
    "user_roles",
]
