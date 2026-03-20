import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.db.models import Reader, ReaderHeartbeat, SourceFilterSettings, TelegramSource, User, UserDigestSettings
from app.db.session import get_sessionmaker
from app.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class AddSourceResult:
    source_id: int
    status: str
    allocated_reader_id: Optional[int]
    remaining_limit: int


async def get_or_create_user(telegram_user_id: int, telegram_chat_id: Optional[int]) -> User:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))).scalar_one_or_none()
        if user is None:
            logger.info("New user registered telegram_user_id=%s", telegram_user_id)
            user = User(
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                subscription_tier="free",
                language="ru",
                timezone="UTC",
            )
            session.add(user)
            await session.flush()

            session.add(UserDigestSettings(user_id=user.id))
            # SourceFilterSettings and others created on demand.

            await session.commit()
        return user


async def ensure_reader_row() -> Reader:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        reader = (await session.execute(select(Reader).where(Reader.id == settings.reader_id))).scalar_one_or_none()
        if reader is None:
            reader = Reader(id=settings.reader_id, session_name=settings.tg_session_name)
            session.add(reader)
            await session.commit()
        return reader


async def add_source_for_user(
    user_telegram_id: int,
    source_ref: str,
    telegram_chat_id: int | None = None,
    source_type: str = "channel",
) -> AddSourceResult:
    await ensure_reader_row()
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.telegram_user_id == user_telegram_id))).scalar_one_or_none()
        if user is None:
            # Should not happen if bot handler uses get_or_create_user first.
            raise RuntimeError("User not found")

        # enforce max sources per user (count only actively listening sources)
        limit = settings.max_sources_per_user_free if user.subscription_tier == "free" else settings.max_sources_per_user_paid

        active_count = (
            await session.execute(
                select(TelegramSource).where(
                    TelegramSource.user_id == user.id,
                    TelegramSource.status == "active",
                )
            )
        ).scalars().count()
        remaining_limit = max(0, limit - active_count)

        reader = (await session.execute(select(Reader).where(Reader.id == settings.reader_id))).scalar_one()

        today = dt.date.today()
        if reader.joins_today_date != today:
            reader.joins_today_date = today
            reader.joins_today_count = 0

        can_allocate = remaining_limit > 0 and (reader.joins_today_count < settings.tg_joins_daily_limit_per_reader) and (not reader.banned)

        status = "active" if can_allocate else "pending"
        allocated_reader_id = settings.reader_id if can_allocate else None

        # Upsert-like behavior: if source exists, return it.
        existing = (await session.execute(
            select(TelegramSource).where(
                TelegramSource.user_id == user.id,
                TelegramSource.source_ref == source_ref,
            )
        )).scalar_one_or_none()

        if existing is not None:
            return AddSourceResult(
                source_id=existing.id,
                status=existing.status,
                allocated_reader_id=existing.allocated_reader_id,
                remaining_limit=remaining_limit,
            )

        source = TelegramSource(
            user_id=user.id,
            source_type=source_type,
            source_ref=source_ref,
            telegram_chat_id=telegram_chat_id,
            status=status,
            allocated_reader_id=allocated_reader_id,
        )
        session.add(source)
        await session.flush()

        session.add(SourceFilterSettings(source_id=source.id))

        if can_allocate:
            reader.joins_today_count += 1

        await session.commit()

        logger.info(
            "Source added/registered: user_telegram_id=%s source_ref=%s status=%s allocated_reader_id=%s remaining_limit=%s",
            user_telegram_id,
            source_ref,
            status,
            allocated_reader_id,
            remaining_limit,
        )

        return AddSourceResult(
            source_id=source.id,
            status=status,
            allocated_reader_id=allocated_reader_id,
            remaining_limit=remaining_limit,
        )


async def list_sources_for_user(telegram_user_id: int) -> list[TelegramSource]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))).scalar_one_or_none()
        if user is None:
            return []
        result = await session.execute(select(TelegramSource).where(TelegramSource.user_id == user.id).order_by(TelegramSource.created_at.desc()))
        return list(result.scalars().all())


async def remove_source_for_user(telegram_user_id: int, source_id: int) -> bool:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))).scalar_one_or_none()
        if user is None:
            return False
        source = (await session.execute(
            select(TelegramSource).where(
                TelegramSource.id == source_id,
                TelegramSource.user_id == user.id,
            )
        )).scalar_one_or_none()
        if source is None:
            return False
        await session.delete(source)
        await session.commit()
        logger.info("Source removed: user_telegram_id=%s source_id=%s", telegram_user_id, source_id)
        return True


async def set_source_filters(
    telegram_user_id: int,
    source_id: int,
    include_enabled: Optional[bool],
    include_keywords: Optional[list[str]],
    stop_enabled: Optional[bool],
    stop_words: Optional[list[str]],
) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))).scalar_one_or_none()
        if user is None:
            return

        source = (await session.execute(
            select(TelegramSource).where(
                TelegramSource.id == source_id,
                TelegramSource.user_id == user.id,
            )
        )).scalar_one_or_none()
        if source is None:
            return

        filters = (await session.execute(select(SourceFilterSettings).where(SourceFilterSettings.source_id == source.id))).scalar_one_or_none()
        if filters is None:
            filters = SourceFilterSettings(source_id=source.id)
            session.add(filters)

        if include_enabled is not None:
            filters.include_enabled = include_enabled
        if include_keywords is not None:
            filters.include_keywords = include_keywords

        if stop_enabled is not None:
            filters.stop_enabled = stop_enabled
        if stop_words is not None:
            filters.stop_words = stop_words

        await session.commit()

        logger.debug(
            "Updated filters: user_telegram_id=%s source_id=%s include_enabled=%s include_keywords_len=%s stop_enabled=%s stop_words_len=%s",
            telegram_user_id,
            source_id,
            include_enabled,
            None if include_keywords is None else len(include_keywords),
            stop_enabled,
            None if stop_words is None else len(stop_words),
        )

