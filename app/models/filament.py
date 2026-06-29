from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class Manufacturer(Base):
    __tablename__ = "manufacturers"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    website = Column(String(256), nullable=True)
    notes = Column(Text, nullable=True)

    products = relationship("FilamentProduct", back_populates="manufacturer")

    def __repr__(self) -> str:
        return f"<Manufacturer {self.name}>"


class FilamentProduct(Base):
    __tablename__ = "filament_products"

    id = Column(Integer, primary_key=True)
    manufacturer_id = Column(Integer, ForeignKey("manufacturers.id"), nullable=False)
    name = Column(String(128), nullable=False)
    material = Column(String(64), nullable=False, index=True)
    color_name = Column(String(64), nullable=False)
    color_hex = Column(String(7), nullable=True)
    diameter_mm = Column(Float, nullable=False, default=1.75)
    nominal_weight_g = Column(Integer, nullable=False, default=1000)
    notes = Column(Text, nullable=True)

    manufacturer = relationship("Manufacturer", back_populates="products")
    purchase_lines = relationship("PurchaseLine", back_populates="filament_product")
    spools = relationship("Spool", back_populates="filament_product")
    shop_links = relationship("ShopLink", back_populates="filament_product")

    def __repr__(self) -> str:
        return f"<FilamentProduct {self.name}>"
