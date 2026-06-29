from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Purchase(Base):
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True)
    purchase_date = Column(Date, nullable=False, index=True)
    shop_name = Column(String(128), nullable=False)
    order_number = Column(String(64), nullable=True)
    shipping_price = Column(Float, nullable=True)
    total_price = Column(Float, nullable=True)
    currency = Column(String(8), nullable=False, default="EUR")
    notes = Column(Text, nullable=True)

    lines = relationship("PurchaseLine", back_populates="purchase", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Purchase {self.shop_name} {self.purchase_date}>"


class PurchaseLine(Base):
    __tablename__ = "purchase_lines"

    id = Column(Integer, primary_key=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    filament_product_id = Column(Integer, ForeignKey("filament_products.id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="EUR")
    spool_weight_g = Column(Integer, nullable=False, default=1000)
    lot_number = Column(String(64), nullable=True)
    notes = Column(Text, nullable=True)

    purchase = relationship("Purchase", back_populates="lines")
    filament_product = relationship("FilamentProduct", back_populates="purchase_lines")
    spools = relationship("Spool", back_populates="purchase_line")

    def __repr__(self) -> str:
        return f"<PurchaseLine purchase={self.purchase_id} product={self.filament_product_id}>"
