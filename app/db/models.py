from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, Text, Boolean, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    telegram_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    subscription_tier: Mapped[str] = mapped_column(String(16), default="free", index=True)
    language: Mapped[str] = mapped_column(String(8), default="ru")
    timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default="UTC")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.now(dt.timezone.utc))

    digest_settings: Mapped[Optional["UserDigestSettings"]] = relationship(back_populates="user", uselist=False)
    sources: Mapped[list["TelegramSource"]] = relationship(back_populates="user")


class UserDigestSettings(Base):
    __tablename__ = "user_digest_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    digest_time: Mapped[dt.time] = mapped_column(Time, default=dt.time(9, 0))

    short_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    media_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    include_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    include_keywords: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    stop_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_words: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # на старте фиксируем `both-and`
    filter_logic: Mapped[str] = mapped_column(String(32), default="both-and")

    user: Mapped["User"] = relationship(back_populates="digest_settings")


class Reader(Base):
    __tablename__ = "readers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    session_name: Mapped[str] = mapped_column(String(128), unique=True)

    joins_today_count: Mapped[int] = mapped_column(Integer, default=0)
    joins_today_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    banned: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.now(dt.timezone.utc))

    heartbeats: Mapped[list["ReaderHeartbeat"]] = relationship(back_populates="reader")


class ReaderHeartbeat(Base):
    __tablename__ = "reader_heartbeats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reader_id: Mapped[int] = mapped_column(ForeignKey("readers.id", ondelete="CASCADE"), index=True)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.now(dt.timezone.utc))
    status: Mapped[str] = mapped_column(String(32), default="ok", index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.now(dt.timezone.utc))

    reader: Mapped["Reader"] = relationship(back_populates="heartbeats")


class TelegramSource(Base):
    __tablename__ = "telegram_sources"
    __table_args__ = (
        UniqueConstraint("user_id", "source_ref", name="uq_user_source_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    source_type: Mapped[str] = mapped_column(String(16), default="channel")  # channel/group/chat
    source_ref: Mapped[str] = mapped_column(Text)  # username or string id

    telegram_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)  # filled on first received message
    status: Mapped[str] = mapped_column(String(24), default="pending")  # pending/active/inactive
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.now(dt.timezone.utc))

    allocated_reader_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("readers.id", ondelete="SET NULL"), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sources")

    filter_settings: Mapped[Optional["SourceFilterSettings"]] = relationship(back_populates="source", uselist=False)


class SourceFilterSettings(Base):
    __tablename__ = "source_filter_settings"

    source_id: Mapped[int] = mapped_column(ForeignKey("telegram_sources.id", ondelete="CASCADE"), primary_key=True)

    include_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    include_keywords: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    stop_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_words: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    filter_logic: Mapped[str] = mapped_column(String(32), default="both-and")

    source: Mapped["TelegramSource"] = relationship(back_populates="filter_settings")


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    __table_args__ = (
        UniqueConstraint("telegram_chat_id", "telegram_message_id", name="uq_chat_message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    telegram_date: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    text: Mapped[str] = mapped_column(Text)

    source_id: Mapped[int] = mapped_column(ForeignKey("telegram_sources.id", ondelete="CASCADE"), index=True)

    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.now(dt.timezone.utc))
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)  # new/clustered/ignored

    cluster_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("clusters.id", ondelete="SET NULL"), nullable=True, index=True)


class MessageFingerprint(Base):
    __tablename__ = "message_fingerprints"

    # 1:1 with TelegramMessage
    message_id: Mapped[int] = mapped_column(ForeignKey("telegram_messages.id", ondelete="CASCADE"), primary_key=True)

    exact_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256 hex
    simhash: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    embedding: Mapped[Optional[bytes]] = mapped_column(nullable=True)  # placeholder until pgvector


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.now(dt.timezone.utc))

    # placeholder until pgvector
    representative_embedding: Mapped[Optional[bytes]] = mapped_column(nullable=True)

    message_count: Mapped[int] = mapped_column(Integer, default=0)

    summary_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    summary_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary_generated_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    cluster_messages: Mapped[list["ClusterMessage"]] = relationship(back_populates="cluster")


class ClusterMessage(Base):
    __tablename__ = "cluster_messages"
    __table_args__ = (
        UniqueConstraint("cluster_id", "message_id", name="uq_cluster_message"),
    )

    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id", ondelete="CASCADE"), primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("telegram_messages.id", ondelete="CASCADE"), primary_key=True)
    assigned_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.now(dt.timezone.utc))

    cluster: Mapped["Cluster"] = relationship(back_populates="cluster_messages")


class DigestJob(Base):
    __tablename__ = "digest_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    digest_date: Mapped[dt.date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.now(dt.timezone.utc))


class DigestMessageLink(Base):
    __tablename__ = "digest_message_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    digest_job_id: Mapped[int] = mapped_column(Integer, index=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    link_url: Mapped[str] = mapped_column(Text)

