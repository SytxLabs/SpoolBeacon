from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id = Column(Integer, primary_key=True)
    shop_link_id = Column(Integer, ForeignKey("shop_links.id", ondelete="CASCADE"), nullable=False, index=True)
    price = Column(Float, nullable=False)
    shipping_price = Column(Float, nullable=True)
    currency = Column(String(8), nullable=False, default="EUR")
    availability = Column(String(64), nullable=True)
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    source = Column(String(16), nullable=False, default="manual")  # manual | html | error
    error_message = Column(Text, nullable=True)

    shop_link = relationship("ShopLink", back_populates="snapshots")

    @property
    def total_price(self) -> float:
        return self.price + (self.shipping_price or 0.0)
