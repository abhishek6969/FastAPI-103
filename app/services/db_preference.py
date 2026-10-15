from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.db_preference import UserPreference


async def get_user_preference(db: AsyncSession, user_id: str) -> UserPreference | None:
    """Query user preference from database by Entra ID object_id."""
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def upsert_user_preference(db: AsyncSession, user_id: str, theme: str) -> UserPreference:
    """Create or update user preference in PostgreSQL."""
    pref = await get_user_preference(db, user_id)
    if pref:
        pref.theme = theme
    else:
        pref = UserPreference(user_id=user_id, theme=theme)
        db.add(pref)
    await db.commit()
    await db.refresh(pref)
    return pref
