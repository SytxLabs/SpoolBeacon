import enum
from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey, DateTime, Enum as SAEnum
from sqlalchemy.orm import relationship

from app.database import Base


class SpoolStatus(enum.Enum):
    new = "new"
    opened = "opened"
    almost_empty = "almost_empty"
    empty = "empty"
    archived = "archived"


class StorageStatus(enum.Enum):
    open = "open"
    sealed = "sealed"
    vacuum_sealed = "vacuum_sealed"
    drybox = "drybox"
    unknown = "unknown"


class Spool(Base):
    __tablename__ = "spools"

    id = Column(Integer, primary_key=True)
    filament_product_id = Column(Integer, ForeignKey("filament_products.id"), nullable=False)
    purchase_line_id = Column(Integer, ForeignKey("purchase_lines.id"), nullable=True)
    spool_code = Column(String(64), unique=True, nullable=False, index=True)
    status = Column(SAEnum(SpoolStatus), nullable=False, default=SpoolStatus.new)
    initial_weight_g = Column(Float, nullable=False)
    remaining_weight_g = Column(Float, nullable=False)
    storage_location = Column(String(128), nullable=True)
    storage_status = Column(SAEnum(StorageStatus), nullable=False, default=StorageStatus.unknown)
    opened_at = Column(DateTime, nullable=True)
    last_dried_at = Column(DateTime, nullable=True)
    last_weight_update_source = Column(String(64), nullable=True)
    last_weight_update_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    filament_product = relationship("FilamentProduct", back_populates="spools")
    purchase_line = relationship("PurchaseLine", back_populates="spools")

    @property
    def fill_percent(self) -> float:
        if not self.initial_weight_g:
            return 0.0
        return round(self.remaining_weight_g / self.initial_weight_g * 100, 1)

    @property
    def is_low(self) -> bool:
        return self.fill_percent < 20.0

    def __repr__(self) -> str:
        return f"<Spool {self.spool_code}>"
