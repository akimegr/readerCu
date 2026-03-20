from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Cluster, ClusterMessage, MessageFingerprint, TelegramMessage


logger = logging.getLogger(__name__)


def normalize_text(text: str) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t


def exact_hash_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def simhash64(text: str) -> int:
    """
    Simple 64-bit SimHash over tokens.
    """
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        return 0

    v = [0] * 64
    for token in tokens:
        h = hashlib.md5(token.encode("utf-8")).digest()  # 128-bit stable hash
        # use first 64 bits
        n = int.from_bytes(h[:8], byteorder="big", signed=False)
        for i in range(64):
            bit = (n >> i) & 1
            v[i] += 1 if bit else -1

    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming_distance_64(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@dataclass
class DedupDecision:
    status: str  # new/ignored
    reason: str  # unique/exact_duplicate/near_duplicate
    cluster_id: Optional[int]


async def _ensure_cluster_for_message(session: AsyncSession, message: TelegramMessage) -> int:
    if message.cluster_id is not None:
        return message.cluster_id

    cluster = Cluster(
        message_count=1,
        summary_status="pending",
    )
    session.add(cluster)
    await session.flush()

    message.cluster_id = cluster.id
    message.status = "clustered"
    session.add(ClusterMessage(cluster_id=cluster.id, message_id=message.id))
    return cluster.id


async def process_message_dedup(
    session: AsyncSession,
    *,
    message: TelegramMessage,
    simhash_threshold: int = 3,
) -> DedupDecision:
    text_norm = normalize_text(message.text)
    message.text = text_norm

    exact_hash = exact_hash_sha256(text_norm)
    sh = simhash64(text_norm)

    # Step 1: exact duplicate
    existing_exact = (
        await session.execute(select(MessageFingerprint).where(MessageFingerprint.exact_hash == exact_hash))
    ).scalar_one_or_none()
    if existing_exact is not None:
        logger.debug("Dedup exact duplicate: message_id=%s exact_hash=%s", message.id, exact_hash)
        # Attach to existing cluster if possible
        existing_msg = (
            await session.execute(select(TelegramMessage).where(TelegramMessage.id == existing_exact.message_id))
        ).scalar_one_or_none()
        if existing_msg and existing_msg.cluster_id:
            message.cluster_id = existing_msg.cluster_id
        message.status = "ignored"
        session.add(
            MessageFingerprint(
                message_id=message.id,
                exact_hash=exact_hash,
                simhash=sh,
            )
        )
        return DedupDecision(status="ignored", reason="exact_duplicate", cluster_id=message.cluster_id)

    # Step 2: near duplicate by SimHash against a bounded recent window
    recent_candidates = (
        await session.execute(
            select(MessageFingerprint).order_by(MessageFingerprint.message_id.desc()).limit(1000)
        )
    ).scalars().all()

    near_candidate: MessageFingerprint | None = None
    for cand in recent_candidates:
        if cand.simhash is None:
            continue
        if hamming_distance_64(int(cand.simhash), sh) <= simhash_threshold:
            near_candidate = cand
            break

    if near_candidate is not None:
        logger.debug(
            "Dedup near duplicate: message_id=%s near_message_id=%s threshold=%s",
            message.id,
            near_candidate.message_id,
            simhash_threshold,
        )
        existing_msg = (
            await session.execute(select(TelegramMessage).where(TelegramMessage.id == near_candidate.message_id))
        ).scalar_one_or_none()

        if existing_msg and existing_msg.cluster_id:
            message.cluster_id = existing_msg.cluster_id
        message.status = "ignored"
        session.add(
            MessageFingerprint(
                message_id=message.id,
                exact_hash=exact_hash,
                simhash=sh,
            )
        )
        return DedupDecision(status="ignored", reason="near_duplicate", cluster_id=message.cluster_id)

    # Unique -> create new cluster now (semantic step will refine later)
    cluster_id = await _ensure_cluster_for_message(session, message)
    logger.info("Dedup new cluster created: message_id=%s cluster_id=%s", message.id, cluster_id)
    session.add(
        MessageFingerprint(
            message_id=message.id,
            exact_hash=exact_hash,
            simhash=sh,
        )
    )
    return DedupDecision(status="new", reason="unique", cluster_id=cluster_id)

