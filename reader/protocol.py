import datetime as dt
from dataclasses import dataclass
from typing import Any


@dataclass
class HeartbeatPayload:
    reader_id: int
    status: str = "ok"

    def to_json(self) -> dict[str, Any]:
        return {"reader_id": self.reader_id, "status": self.status}


@dataclass
class NewMessagePayload:
    reader_id: int
    source_id: int
    telegram_chat_id: int
    telegram_message_id: int
    telegram_date: dt.datetime
    text: str

    def to_json(self) -> dict[str, Any]:
        return {
            "reader_id": self.reader_id,
            "source_id": self.source_id,
            "telegram_chat_id": self.telegram_chat_id,
            "telegram_message_id": self.telegram_message_id,
            "telegram_date": self.telegram_date.isoformat(),
            "text": self.text,
        }

