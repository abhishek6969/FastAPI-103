from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import CurrentUser
from app.models.preference import UserPreferenceResponse, UserPreferenceUpdate, ThemeEnum
from app.database import get_db
from app.services.auth import get_current_user
from app.services.db_preference import get_user_preference, upsert_user_preference

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.get("", response_model=UserPreferenceResponse)
async def get_preferences(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve current user's UI preferences.
    If the user has not explicitly configured a preference yet,
    defaults gracefully to 'dark' theme.
    """
    pref = await get_user_preference(db, current_user.object_id)
    if not pref:
        return UserPreferenceResponse(
            user_id=current_user.object_id,
            theme=ThemeEnum.DARK,
            updated_at=datetime.now(timezone.utc),
        )
    return pref


@router.put("", response_model=UserPreferenceResponse)
async def update_preferences(
    body: UserPreferenceUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update or set current user's UI theme preference (dark / light).
    """
    pref = await upsert_user_preference(db, current_user.object_id, body.theme.value)
    return pref
