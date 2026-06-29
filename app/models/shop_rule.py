from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class ShopRule(Base):
    __tablename__ = "shop_rules"

    id = Column(Integer, primary_key=True)
    domain = Column(String(256), nullable=False, unique=True, index=True)
    price_selector = Column(String(256), nullable=True)
    title_selector = Column(String(256), nullable=True)
    availability_selector = Column(String(256), nullable=True)
    price_regex = Column(String(256), nullable=True)
    availability_regex = Column(String(256), nullable=True)
    currency = Column(String(8), nullable=False, default="EUR")
    test_url = Column(String(512), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
