import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String,
    Integer,
    Numeric,
    Float,
    Text,
    DateTime,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InvoiceRecord(Base):
    """
    SQLAlchemy ORM model for storing invoice extraction history.
    Stores task status, user ownership (Entra ID oid), file metadata (SHA-256 hash),
    and structured extraction results (JSONB + top-level queryable columns).
    """
    __tablename__ = "invoice_records"

    # Primary key: matches the task_id returned to the user
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # User identification from Microsoft Entra ID
    # user_id is the immutable Object ID (oid) — primary security key for ownership
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # user_upn is the email/UPN for UI display
    user_upn: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # File metadata
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # SHA-256 hex
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Processing status: "processing", "done", "failed"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing", index=True)

    # Full extraction JSON structure from Azure Document Intelligence
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Top-level extracted fields for fast indexing, sorting, and filtering
    vendor_name: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    invoice_total: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Error details if status == "failed"
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<InvoiceRecord task_id={self.task_id} status={self.status} user={self.user_upn}>"
