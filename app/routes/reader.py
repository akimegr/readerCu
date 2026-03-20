import logging
import datetime as dt
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import Reader, ReaderHeartbeat, TelegramMessage, TelegramSource
from app.db.session import get_sessionmaker
from app.services.dedup_service import process_message_dedup
from app.settings import settings


router = APIRouter(prefix="/reader", tags=["reader"])
logger = logging.getLogger(__name__)


class HeartbeatPayload(BaseModel):
    reader_id: int
    status: str = "ok"  # ok/offline/banned


class NewMessagePayload(BaseModel):
    reader_id: int
    source_id: int
    telegram_chat_id: int
    telegram_message_id: int
    telegram_date: dt.datetime
    text: str


@router.post("/heartbeat")
async def heartbeat(payload: HeartbeatPayload) -> dict:
    logger.debug("Heartbeat from reader_id=%s status=%s", payload.reader_id, payload.status)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        reader = (await session.execute(select(Reader).where(Reader.id == payload.reader_id))).scalar_one_or_none()
        if reader is None:
            raise HTTPException(status_code=404, detail="Reader not found")

        hb = ReaderHeartbeat(
            reader_id=payload.reader_id,
            last_seen_at=dt.datetime.now(dt.timezone.utc),
            status=payload.status,
            updated_at=dt.datetime.now(dt.timezone.utc),
        )
        session.add(hb)
        await session.commit()

    return {"ok": True}


@router.get("/sources")
async def get_allocated_sources(reader_id: Optional[int] = None) -> dict:
    """
    Reader опрашивает backend и получает актуальный список источников, которые ему назначены.
    """
    rid = reader_id or settings.reader_id
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        sources = (await session.execute(
            select(TelegramSource).where(
                TelegramSource.allocated_reader_id == rid,
                TelegramSource.status == "active",
            )
        )).scalars().all()

        return {
            "reader_id": rid,
            "sources": [
                {
                    "source_id": s.id,
                    "telegram_chat_id": s.telegram_chat_id,
                    "source_ref": s.source_ref,
                    "source_type": s.source_type,
                }
                for s in sources
            ],
        }


@router.post("/new_message")
async def new_message(payload: NewMessagePayload) -> dict:
    logger.debug(
        "NewMessage: reader_id=%s source_id=%s chat_id=%s message_id=%s",
        payload.reader_id,
        payload.source_id,
        payload.telegram_chat_id,
        payload.telegram_message_id,
    )
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        # Ensure source exists and belongs to active allocations is a future optimization.
        msg = TelegramMessage(
            telegram_chat_id=payload.telegram_chat_id,
            telegram_message_id=payload.telegram_message_id,
            telegram_date=payload.telegram_date,
            text=payload.text,
            source_id=payload.source_id,
            status="new",
        )
        session.add(msg)
        try:
            await session.flush()

            dedup = await process_message_dedup(session, message=msg)
            await session.commit()
        except Exception:
            # Most likely unique constraint conflict; treat as idempotent.
            await session.rollback()
            # Even on duplicate insert, ensure source has chat id.
            source = (await session.execute(
                select(TelegramSource).where(TelegramSource.id == payload.source_id)
            )).scalar_one_or_none()
            if source is not None and source.telegram_chat_id is None:
                source.telegram_chat_id = payload.telegram_chat_id
                await session.commit()
            return {"ok": True, "duplicate": True}

        # Fill in telegram_chat_id on first received event (useful for sources added by @tag/link).
        source = (await session.execute(
            select(TelegramSource).where(TelegramSource.id == payload.source_id)
        )).scalar_one_or_none()
        if source is not None and source.telegram_chat_id is None:
            source.telegram_chat_id = payload.telegram_chat_id
            await session.commit()

    return {"ok": True, "dedup": dedup.reason, "cluster_id": dedup.cluster_id}

