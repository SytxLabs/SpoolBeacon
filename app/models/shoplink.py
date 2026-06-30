from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class ShopLink(Base):
    __tablename__ = "shop_links"

    id = Column(Integer, primary_key=True)
    filament_product_id = Column(Integer, ForeignKey("filament_products.id"), nullable=False)
    shop_name = Column(String(128), nullable=False)
    url = Column(String(512), nullable=False)
    currency = Column(String(8), nullable=False, default="EUR")
    package_weight_g = Column(Integer, nullable=False)
    manual_price = Column(Float, nullable=False)
    shipping_price = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    target_price = Column(Float, nullable=True)
    target_price_per_kg = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    filament_product = relationship("FilamentProduct", back_populates="shop_links")
    snapshots = relationship("PriceSnapshot", back_populates="shop_link",
                             cascade="all, delete-orphan", order_by="PriceSnapshot.captured_at.desc()")
    alerts = relationship("PriceAlertEvent", back_populates="shop_link",
                          cascade="all, delete-orphan", order_by="PriceAlertEvent.created_at.desc()")

    @property
    def total_price(self) -> float:
        return self.manual_price + (self.shipping_price or 0.0)

    @property
    def price_per_kg(self) -> float:
        if not self.package_weight_g:
            return 0.0
        return round(self.total_price / self.package_weight_g * 1000, 2)

    def __repr__(self) -> str:
        return f"<ShopLink {self.shop_name} {self.manual_price} {self.currency}>"
