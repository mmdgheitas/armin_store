"""کاربران، نقش‌ها، مجوزها و آدرس‌ها."""
from __future__ import annotations

from datetime import datetime

from app.extensions import db
from app.models.base import SoftDeleteMixin, TimestampMixin

user_roles = db.Table(
    "user_roles",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    db.Column(
        "permission_id", db.Integer, db.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    ),
)


class Permission(db.Model):
    __tablename__ = "permissions"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False, index=True)
    title = db.Column(db.String(120), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Permission {self.key}>"


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False, index=True)
    title = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, nullable=True)

    permissions = db.relationship(
        "Permission", secondary=role_permissions, lazy="selectin", backref="roles"
    )

    @property
    def permission_keys(self) -> set[str]:
        return {p.key for p in self.permissions}

    def has_permission(self, key: str) -> bool:
        return self.name == "super_admin" or key in self.permission_keys

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Role {self.name}>"


class User(db.Model, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    mobile = db.Column(db.String(11), unique=True, nullable=False, index=True)
    email = db.Column(db.String(190), nullable=True, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    failed_login_count = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_login_ip = db.Column(db.String(64), nullable=True)

    roles = db.relationship("Role", secondary=user_roles, lazy="selectin", backref="users")

    addresses = db.relationship(
        "Address",
        backref="user",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="Address.is_default.desc(), Address.id.desc()",
    )

    # --- کمکی‌ها ---
    @property
    def is_admin(self) -> bool:
        return bool(self.roles) and self.is_active and not self.is_deleted

    @property
    def role_names(self) -> list[str]:
        return [role.name for role in self.roles]

    @property
    def role_titles(self) -> str:
        return "، ".join(role.title for role in self.roles) or "کاربر"

    def has_permission(self, key: str) -> bool:
        if not self.is_active or self.is_deleted:
            return False
        return any(role.has_permission(key) for role in self.roles)

    @property
    def permission_keys(self) -> set[str]:
        keys: set[str] = set()
        for role in self.roles:
            if role.name == "super_admin":
                return {"*"}
            keys |= role.permission_keys
        return keys

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > datetime.utcnow())

    @property
    def default_address(self):
        for address in self.addresses:
            if address.is_default:
                return address
        return self.addresses[0] if self.addresses else None

    @property
    def display_name(self) -> str:
        return self.full_name or self.mobile

    @property
    def masked_mobile(self) -> str:
        if len(self.mobile) == 11:
            return f"{self.mobile[:4]}***{self.mobile[-4:]}"
        return self.mobile

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.mobile}>"


class Address(db.Model, TimestampMixin):
    __tablename__ = "addresses"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_name = db.Column(db.String(150), nullable=False)
    mobile = db.Column(db.String(11), nullable=False)
    province = db.Column(db.String(80), nullable=False)
    city = db.Column(db.String(80), nullable=False)
    full_address = db.Column(db.Text, nullable=False)
    postal_code = db.Column(db.String(10), nullable=True)
    national_code = db.Column(db.String(10), nullable=True)
    is_default = db.Column(db.Boolean, default=False, nullable=False)

    __table_args__ = (db.Index("ix_addresses_user_default", "user_id", "is_default"),)

    def to_dict(self) -> dict:
        return {
            "recipient_name": self.recipient_name,
            "mobile": self.mobile,
            "province": self.province,
            "city": self.city,
            "full_address": self.full_address,
            "postal_code": self.postal_code or "",
            "national_code": self.national_code or "",
        }

    @property
    def summary(self) -> str:
        return f"{self.province}، {self.city}، {self.full_address}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Address {self.city} {self.id}>"
