import asyncio
import logging

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.bot.handlers import get_dispatcher
from app.db.models import Base
from app.db.session import get_engine
from app.routes.reader import router as reader_router
from app.settings import settings

try:
    # aiogram is optional until env is set.
    from aiogram import Bot
except Exception:  # pragma: no cover
    Bot = None  # type: ignore


def create_app() -> FastAPI:
    app = FastAPI(title="News aggregator (RU UX)")
    app.include_router(reader_router)
    logger = logging.getLogger(__name__)

    bot_task: asyncio.Task | None = None

    async def _upgrade_telegram_ids_to_bigint(engine: AsyncEngine) -> None:
        """
        Dev-safe schema upgrade for existing INTEGER telegram id columns.
        Needed because Telegram IDs may exceed int32 range.
        """
        ddl_statements = [
            "ALTER TABLE IF EXISTS users ALTER COLUMN telegram_user_id TYPE BIGINT",
            "ALTER TABLE IF EXISTS users ALTER COLUMN telegram_chat_id TYPE BIGINT",
            "ALTER TABLE IF EXISTS telegram_sources ALTER COLUMN telegram_chat_id TYPE BIGINT",
            "ALTER TABLE IF EXISTS telegram_messages ALTER COLUMN telegram_chat_id TYPE BIGINT",
            "ALTER TABLE IF EXISTS telegram_messages ALTER COLUMN telegram_message_id TYPE BIGINT",
            "ALTER TABLE IF EXISTS digest_message_links ALTER COLUMN telegram_chat_id TYPE BIGINT",
            "ALTER TABLE IF EXISTS digest_message_links ALTER COLUMN telegram_message_id TYPE BIGINT",
        ]
        async with engine.begin() as conn:
            for ddl in ddl_statements:
                try:
                    await conn.execute(text(ddl))
                except Exception as exc:
                    logger.warning("Schema upgrade warning for '%s': %s", ddl, exc)

    @app.on_event("startup")
    async def startup() -> None:
        # Configure global logging once on startup.
        level_name = (settings.log_level or "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        logging.getLogger("aiogram").setLevel(level)

        # Create schema for early development.
        engine: AsyncEngine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _upgrade_telegram_ids_to_bigint(engine)

        # Start telegram bot polling
        if settings.bot_token:
            bot = Bot(token=settings.bot_token)  # type: ignore[misc]
            dp = get_dispatcher()
            nonlocal bot_task
            bot_task = asyncio.create_task(dp.start_polling(bot))

    @app.on_event("shutdown")
    async def shutdown() -> None:
        nonlocal bot_task
        if bot_task is not None:
            bot_task.cancel()
            try:
                await bot_task
            except Exception:
                pass

    return app


app = create_app()

