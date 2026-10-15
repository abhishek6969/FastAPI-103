from datetime import datetime
from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class UserPreference(Base):
    """
    SQLAlchemy ORM model for storing user preferences.
    Stores UI theme settings (dark/light) keyed by the user's Entra ID object_id.
    """
    __tablename__ = "user_preferences"

    # Primary key: matches the immutable Object ID (oid) from Entra ID
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)

    # UI theme: "dark" or "light" (defaults to "dark")
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="dark", server_default="dark")

    # Timestamps
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
