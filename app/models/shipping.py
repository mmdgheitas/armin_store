"""روش‌های ارسال."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class ShippingMethod(db.Model, TimestampMixin):
    __tablename__ = "shipping_methods"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(400), nullable=True)
    shipping_type = db.Column(db.String(30), default="flat", nullable=False)
    base_cost = db.Column(db.Integer, default=0, nullable=False)
    free_threshold = db.Column(db.Integer, default=0, nullable=False)
    configuration_json = db.Column(db.JSON, default=dict, nullable=False)
    estimated_days = db.Column(db.String(50), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    __table_args__ = (db.Index("ix_shipping_active_sort", "is_active", "sort_order"),)

    @property
    def type_label(self) -> str:
        return {
            "flat": "هزینه ثابت",
            "province": "بر اساس استان",
            "city": "بر اساس شهر",
            "free_threshold": "آزاد بالای مبلغ مشخص",
            "pickup": "تحویل حضوری",
        }.get(self.shipping_type, self.shipping_type)

    def cost_for(self, province: str | None, city: str | None, subtotal: int) -> int:
        """
        محاسبه هزینه ارسال این روش.
        پیکربندی: configuration_json = {"province_costs": {"تهران": 50000}, "city_costs": {"تهران|تهران": 30000}}
        """
        config = self.configuration_json or {}
        if self.shipping_type == "pickup":
            return 0
        if self.shipping_type == "free_threshold":
            if subtotal >= (self.free_threshold or 0) > 0:
                return 0
            return self._fallback_cost(province, city)
        if self.shipping_type == "province":
            costs = config.get("province_costs") or {}
            if province and province in costs:
                try:
                    return max(0, int(costs[province]))
                except (TypeError, ValueError):
                    pass
            return self._fallback_cost(province, city)
        if self.shipping_type == "city":
            city_costs = config.get("city_costs") or {}
            if province and city:
                key = f"{province}|{city}"
                if key in city_costs:
                    try:
                        return max(0, int(city_costs[key]))
                    except (TypeError, ValueError):
                        pass
            return self._fallback_cost(province, city)
        return max(0, int(self.base_cost or 0))

    def _fallback_cost(self, province: str | None, city: str | None) -> int:
        config = self.configuration_json or {}
        if config.get("free_over") and province:
            pass
        return max(0, int(self.base_cost or 0))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ShippingMethod {self.name}>"
