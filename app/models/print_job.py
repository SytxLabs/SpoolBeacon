import enum
from datetime import datetime

from sqlalchemy import Column, Integer, Float, String, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship

from app.database import Base


class PrintFileKind(enum.Enum):
    link = "link"
    upload = "upload"


class PrintJobStatus(enum.Enum):
    planned = "planned"
    printing = "printing"
    done = "done"


class PrintJob(Base):
    __tablename__ = "print_jobs"

    id = Column(Integer, primary_key=True)
    job_code = Column(String(64), unique=True, nullable=False, index=True)
    print_name = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(SAEnum(PrintJobStatus), nullable=False, default=PrintJobStatus.planned, index=True)
    printed_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    lines = relationship("PrintJobLine", back_populates="print_job", cascade="all, delete-orphan")
    files = relationship("PrintJobFile", back_populates="print_job", cascade="all, delete-orphan")

    @property
    def total_used_g(self) -> float:
        return sum(line.used_g for line in self.lines)

    def __repr__(self) -> str:
        return f"<PrintJob id={self.id} name={self.print_name!r}>"


class PrintJobLine(Base):
    __tablename__ = "print_job_lines"

    id = Column(Integer, primary_key=True)
    print_job_id = Column(Integer, ForeignKey("print_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    spool_id = Column(Integer, ForeignKey("spools.id", ondelete="SET NULL"), nullable=True, index=True)
    spool_code = Column(String(64), nullable=False)
    product_name = Column(String(200), nullable=False)
    used_g = Column(Float, nullable=False)

    print_job = relationship("PrintJob", back_populates="lines")
    spool = relationship("Spool")

    def __repr__(self) -> str:
        return f"<PrintJobLine spool={self.spool_code} used={self.used_g}g>"


class PrintJobFile(Base):
    __tablename__ = "print_job_files"

    id = Column(Integer, primary_key=True)
    print_job_id = Column(Integer, ForeignKey("print_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(SAEnum(PrintFileKind), nullable=False)
    provider = Column(String(50), nullable=True)
    url = Column(String(500), nullable=True)
    stored_filename = Column(String(255), nullable=True)
    original_filename = Column(String(255), nullable=True)
    file_ext = Column(String(10), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    print_job = relationship("PrintJob", back_populates="files")

    def __repr__(self) -> str:
        return f"<PrintJobFile kind={self.kind.value if self.kind else None} provider={self.provider!r}>"
