from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class PriceAlertEvent(Base):
    __tablename__ = "price_alert_events"

    id = Column(Integer, primary_key=True)
    shop_link_id = Column(Integer, ForeignKey("shop_links.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    price_snapshot_id = Column(Integer, ForeignKey("price_snapshots.id", ondelete="SET NULL"),
                               nullable=True)
    alert_type = Column(String(32), nullable=False)   # target_price | target_price_per_kg
    message = Column(String(512), nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    shop_link = relationship("ShopLink", back_populates="alerts")
    snapshot = relationship("PriceSnapshot")

    @property
    def is_active(self) -> bool:
        return self.resolved_at is None
