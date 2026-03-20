import datetime as dt
from typing import Optional

from sqlalchemy import select

from app.db.models import SourceFilterSettings, TelegramSource, User, UserDigestSettings
from app.db.session import get_sessionmaker


async def get_user_digest_settings(telegram_user_id: int) -> Optional[UserDigestSettings]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))).scalar_one_or_none()
        if user is None:
            return None
        return (await session.execute(select(UserDigestSettings).where(UserDigestSettings.user_id == user.id))).scalar_one_or_none()


async def update_user_digest_settings(
    telegram_user_id: int,
    *,
    digest_enabled: Optional[bool] = None,
    digest_time: Optional[dt.time] = None,
    short_enabled: Optional[bool] = None,
    media_enabled: Optional[bool] = None,
    include_enabled: Optional[bool] = None,
    include_keywords: Optional[list[str]] = None,
    stop_enabled: Optional[bool] = None,
    stop_words: Optional[list[str]] = None,
    filter_logic: Optional[str] = None,
) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))).scalar_one_or_none()
        if user is None:
            return
        settings = (await session.execute(select(UserDigestSettings).where(UserDigestSettings.user_id == user.id))).scalar_one_or_none()
        if settings is None:
            return

        if digest_enabled is not None:
            settings.digest_enabled = digest_enabled
        if digest_time is not None:
            settings.digest_time = digest_time
        if short_enabled is not None:
            settings.short_enabled = short_enabled
        if media_enabled is not None:
            settings.media_enabled = media_enabled
        if include_enabled is not None:
            settings.include_enabled = include_enabled
        if include_keywords is not None:
            settings.include_keywords = include_keywords
        if stop_enabled is not None:
            settings.stop_enabled = stop_enabled
        if stop_words is not None:
            settings.stop_words = stop_words
        if filter_logic is not None:
            settings.filter_logic = filter_logic

        await session.commit()


async def get_source_filter_settings(telegram_user_id: int, source_id: int) -> Optional[SourceFilterSettings]:
    """
    Возвращаем настройки только если источник принадлежит пользователю.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))).scalar_one_or_none()
        if user is None:
            return None
        source = (await session.execute(
            select(TelegramSource).where(TelegramSource.id == source_id, TelegramSource.user_id == user.id)
        )).scalar_one_or_none()
        if source is None:
            return None
        return (await session.execute(select(SourceFilterSettings).where(SourceFilterSettings.source_id == source.id))).scalar_one_or_none()

