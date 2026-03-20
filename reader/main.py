import asyncio
import logging
import os
from typing import Any

import httpx
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, RPCError

from app.settings import settings

logger = logging.getLogger(__name__)


def _pick_source_id_from_event(source_ref_to_id: dict[str, int], event: Any) -> int | None:
    """
    Мы храним source_ref как:
    - '@username' (если он есть)
    - или numeric id строкой (fallback)

    Telethon в NewMessage дает event.chat_id и иногда event.chat.username.
    """
    key_num = str(event.chat_id) if getattr(event, "chat_id", None) is not None else None
    if key_num and key_num in source_ref_to_id:
        return source_ref_to_id[key_num]

    chat = getattr(event, "chat", None)
    username = getattr(chat, "username", None) if chat is not None else None
    if username:
        key = f"@{username}"
        return source_ref_to_id.get(key)

    return None


async def run_reader() -> None:
    level_name = (settings.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Reader starting (reader_id=%s)", settings.reader_id)
    # Ensure Telethon SQLite session path is writable inside container.
    session_dir = settings.tg_session_dir or "/tmp"
    os.makedirs(session_dir, exist_ok=True)
    session_name = settings.tg_session_name or "reader"
    session_path = os.path.join(session_dir, session_name)

    # Quick write access check before Telethon tries opening SQLite.
    test_file = os.path.join(session_dir, ".write_test")
    try:
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test_file)
    except OSError as exc:
        raise RuntimeError(f"Reader session directory is not writable: {session_dir}") from exc

    client = TelegramClient(session=session_path, api_id=int(settings.tg_api_id or 0), api_hash=settings.tg_api_hash)

    # Start (sign-in). For production you would provide a session string instead.
    await client.start(phone=settings.tg_phone_number)

    backend = settings.backend_base_url.rstrip("/")

    source_ref_to_id: dict[str, int] = {}
    subscribe_chats: list[str] = []
    handler = None

    async with httpx.AsyncClient(timeout=30) as http:
        async def fetch_sources() -> list[dict[str, Any]]:
            r = await http.get(f"{backend}/reader/sources", params={"reader_id": settings.reader_id})
            r.raise_for_status()
            data = r.json()
            return data.get("sources", [])

        def rebuild_handler(chats: list[str]) -> None:
            nonlocal handler
            if handler is not None:
                client.remove_event_handler(handler)
                handler = None

            if not chats:
                return

            @client.on(events.NewMessage(chats=chats))
            async def _on_new_message(event):  # type: ignore
                # Determine source_id by matching chat identity -> source_ref.
                source_id = _pick_source_id_from_event(source_ref_to_id, event)
                if source_id is None:
                    return

                payload = {
                    "reader_id": settings.reader_id,
                    "source_id": source_id,
                    "telegram_chat_id": int(event.chat_id),
                    "telegram_message_id": int(event.id),
                    "telegram_date": event.date.isoformat(),
                    "text": event.raw_text or "",
                }
                try:
                    await http.post(f"{backend}/reader/new_message", json=payload)
                except Exception:
                    logger.exception("Failed to send new_message to backend")

            handler = _on_new_message  # type: ignore

        # Initial fetch
        sources = await fetch_sources()
        source_ref_to_id = {}
        subscribe_chats = []
        for s in sources:
            source_ref_to_id[s["source_ref"]] = s["source_id"]
            if s.get("telegram_chat_id") is not None:
                source_ref_to_id[str(s["telegram_chat_id"])] = s["source_id"]
            subscribe_chats.append(s["source_ref"])

        rebuild_handler(subscribe_chats)

        async def heartbeat_loop() -> None:
            while True:
                try:
                    await http.post(
                        f"{backend}/reader/heartbeat",
                        json={"reader_id": settings.reader_id, "status": "ok"},
                    )
                except Exception:
                    logger.exception("Heartbeat failed")
                await asyncio.sleep(settings.reader_heartbeat_interval_sec)

        async def poll_loop() -> None:
            nonlocal source_ref_to_id, subscribe_chats
            while True:
                try:
                    sources2 = await fetch_sources()
                    new_map: dict[str, int] = {}
                    new_subscribe_chats: list[str] = []
                    for s in sources2:
                        new_map[s["source_ref"]] = s["source_id"]
                        if s.get("telegram_chat_id") is not None:
                            new_map[str(s["telegram_chat_id"])] = s["source_id"]
                        new_subscribe_chats.append(s["source_ref"])
                    # If mapping changed, rebuild subscriptions.
                    if new_map != source_ref_to_id or new_subscribe_chats != subscribe_chats:
                        source_ref_to_id = new_map
                        subscribe_chats = new_subscribe_chats
                        rebuild_handler(subscribe_chats)
                        logger.info("Updated reader subscriptions: %s", len(source_ref_to_id))
                except Exception:
                    logger.exception("poll_loop error")
                await asyncio.sleep(settings.reader_poll_interval_sec)

        # Run both loops
        await asyncio.gather(client.run_until_disconnected(), heartbeat_loop(), poll_loop())


if __name__ == "__main__":
    asyncio.run(run_reader())

