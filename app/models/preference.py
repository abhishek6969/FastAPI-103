from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict


class ThemeEnum(str, Enum):
    DARK = "dark"
    LIGHT = "light"


class UserPreferenceResponse(BaseModel):
    """Schema returned when querying user preferences."""
    user_id: str
    theme: ThemeEnum
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserPreferenceUpdate(BaseModel):
    """Schema for updating user preferences."""
    theme: ThemeEnum
