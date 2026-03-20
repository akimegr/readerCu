from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="📌 Мои каналы", callback_data="menu_channels")],
        [InlineKeyboardButton(text="🎛️ Настройки", callback_data="menu_settings")],
        [InlineKeyboardButton(text="🧾 Подписка", callback_data="menu_subscription")],
        [InlineKeyboardButton(text="➕ Добавить канал", callback_data="menu_add")],
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="menu_support")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def channels_menu(source_ids: list[int], page: int = 0, has_next: bool = False) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []
    for sid in source_ids:
        kb.append([InlineKeyboardButton(text=f"Канал #{sid}", callback_data=f"source_menu:{sid}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"channels_page:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️ Ещё", callback_data=f"channels_page:{page+1}"))

    if nav:
        kb.append(nav)

    kb.append([InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def source_menu(
    source_id: int,
    *,
    include_enabled: bool,
    stop_enabled: bool,
    include_keywords_count: int = 0,
    stop_words_count: int = 0,
) -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(
                text=(
                    f"🔑 Include: {'ВКЛ' if include_enabled else 'ВЫКЛ'}"
                    + (f" ({include_keywords_count})" if include_keywords_count else "")
                ),
                callback_data=f"source_toggle_include:{source_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=(
                    f"🚫 Stop: {'ВКЛ' if stop_enabled else 'ВЫКЛ'}"
                    + (f" ({stop_words_count})" if stop_words_count else "")
                ),
                callback_data=f"source_toggle_stop:{source_id}",
            )
        ],
        [
            InlineKeyboardButton(text="✍️ Задать keywords", callback_data=f"source_set_include_keywords:{source_id}"),
            InlineKeyboardButton(text="🧹 Задать stop-words", callback_data=f"source_set_stopwords:{source_id}"),
        ],
        [
            InlineKeyboardButton(text="🗑 Очистить keywords", callback_data=f"source_clear_include:{source_id}"),
            InlineKeyboardButton(text="🗑 Очистить stop-words", callback_data=f"source_clear_stop:{source_id}"),
        ],
        [InlineKeyboardButton(text="⬅️ К списку каналов", callback_data="menu_channels")],
        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def settings_menu(
    *,
    digest_enabled: bool,
    short_enabled: bool,
    media_enabled: bool,
    include_enabled: bool,
    include_keywords_count: int,
    stop_enabled: bool,
    stop_words_count: int,
) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text=f"📨 Дайджест: {'ON' if digest_enabled else 'OFF'}", callback_data="toggle_digest")],
        [InlineKeyboardButton(text=f"✂️ Сокращать: {'ON' if short_enabled else 'OFF'}", callback_data="toggle_short")],
        [InlineKeyboardButton(text=f"🖼️ Медиа: {'ON' if media_enabled else 'OFF'}", callback_data="toggle_media")],
        [
            InlineKeyboardButton(
                text=f"🔑 Include: {'ВКЛ' if include_enabled else 'ВЫКЛ'} ({include_keywords_count})",
                callback_data="global_include_menu",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🚫 Stop: {'ВКЛ' if stop_enabled else 'ВЫКЛ'} ({stop_words_count})",
                callback_data="global_stop_menu",
            )
        ],
        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

