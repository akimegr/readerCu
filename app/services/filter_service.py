from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional


def _normalize_words(words: Iterable[str]) -> list[str]:
    out: list[str] = []
    for w in words:
        w2 = (w or "").strip().lower()
        if w2:
            out.append(w2)
    return out


def _contains_any(haystack: str, needles: list[str]) -> bool:
    h = haystack.lower()
    return any(n in h for n in needles)


@dataclass
class WordFilters:
    include_enabled: bool
    include_keywords: list[str]
    stop_enabled: bool
    stop_words: list[str]


def match_word_filters(text: str, global_filters: WordFilters, source_filters: Optional[WordFilters] = None) -> bool:
    """
    Логика `both-and` с приоритетом stop:
    - если включены stop-сообщения и хотя бы одно stop слово найдено -> False
    - если включены include-сообщения и ни одно include слово не найдено -> False
    - если include/stop выключены -> соответствующие проверки пропускаются

    Global применяются всегда, Source добавляются как дополнительные условия:
    - Source stop -> запретит
    - Source include -> потребует совпадение (если source include включён)
    """
    t = text or ""
    g_include = _normalize_words(global_filters.include_keywords)
    g_stop = _normalize_words(global_filters.stop_words)

    # stop has priority across global and source
    if global_filters.stop_enabled and g_stop and _contains_any(t, g_stop):
        return False

    if source_filters is not None:
        s_include = _normalize_words(source_filters.include_keywords)
        s_stop = _normalize_words(source_filters.stop_words)

        if source_filters.stop_enabled and s_stop and _contains_any(t, s_stop):
            return False

        if source_filters.include_enabled and s_include and not _contains_any(t, s_include):
            return False

    # global include check last
    if global_filters.include_enabled and g_include and not _contains_any(t, g_include):
        return False

    return True

