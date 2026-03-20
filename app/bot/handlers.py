import logging
import re
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.menus import channels_menu, main_menu, settings_menu, source_menu
from app.bot.states import AddFSM, KeywordFSM
from app.services.source_service import add_source_for_user, get_or_create_user, list_sources_for_user, remove_source_for_user, set_source_filters
from app.services.settings_service import (
    get_source_filter_settings,
    get_user_digest_settings,
    update_user_digest_settings,
)

from app.settings import settings


router = Router()
logger = logging.getLogger(__name__)

# To avoid "menu drift" (user pressing old buttons), we keep the last bot-menu message per user
# and delete it before sending a new menu message (only for send-message paths).
_last_menu_message: dict[int, tuple[int, int]] = {}  # user_id -> (chat_id, message_id)


async def _delete_previous_menu_if_any(user_id: int, bot: Bot) -> None:
    prev = _last_menu_message.get(user_id)
    if not prev:
        return
    chat_id, message_id = prev
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        # Safe to ignore (message may already be deleted / too old).
        pass
    _last_menu_message.pop(user_id, None)


def _remember_menu(user_id: int, chat_id: int, message_id: int) -> None:
    _last_menu_message[user_id] = (chat_id, message_id)


def _back_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)]])


def _parse_keywords(raw: str) -> list[str]:
    parts = re.split(r"[,\n;]+", raw)
    out: list[str] = []
    for p in parts:
        w = (p or "").strip().lower()
        if w:
            out.append(w)
    # de-dup preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for w in out:
        if w in seen:
            continue
        seen.add(w)
        unique.append(w)
    return unique


def _extract_username_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m1 = re.search(r"@([a-zA-Z0-9_]{3,})", text)
    if m1:
        return m1.group(0)  # includes "@"

    # link: https://t.me/<username> or https://t.me/<username>/<message_id>
    m2 = re.search(r"https?://t\.me/([a-zA-Z0-9_]{3,})", text)
    if m2:
        return f"@{m2.group(1)}"

    return None


async def _render_main(user_tg_id: int, bot: Bot, message: Message) -> None:
    await _delete_previous_menu_if_any(user_tg_id, bot)
    sent = await message.answer(
        "Главное меню. Выберите, что сделать дальше 👇",
        reply_markup=main_menu(),
    )
    _remember_menu(user_tg_id, sent.chat.id, sent.message_id)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    from_user = message.from_user
    if from_user is None:
        return
    await get_or_create_user(from_user.id, message.chat.id)
    logger.info("Bot start: telegram_user_id=%s chat_id=%s", from_user.id, message.chat.id)
    await _render_main(from_user.id, message.bot, message)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Доступные команды:\n"
        "• /add — добавить канал (можно переслать сообщение из канала или написать @username)\n"
        "• /channels — список ваших каналов\n"
        "• /settings — настройки дайджеста/сокращения/медиа + фильтры\n"
        "• /subscription — лимит каналов в прослушивании\n"
        "• /support — поддержка (навигация на админа)\n"
        "\nТакже можно пользоваться кнопками в меню."
    )


@router.message(Command("add"))
async def cmd_add(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    # Option A: user replies/forwards a message from the target chat
    if message.reply_to_message and message.reply_to_message.forward_from_chat:
        chat = message.reply_to_message.forward_from_chat
        source_type = str(chat.type or "channel")
        source_ref = f"@{chat.username}" if getattr(chat, "username", None) else str(chat.id)
        telegram_chat_id = chat.id
        res = await add_source_for_user(
            user_telegram_id=user.id,
            source_ref=source_ref,
            telegram_chat_id=telegram_chat_id,
            source_type=source_type,
        )
        logger.info("User %s adds source via forward: source_ref=%s chat_id=%s status=%s", user.id, source_ref, telegram_chat_id, res.status)
        status_text = "✅ Источник добавлен и подключается reader'ом" if res.status == "active" else "⏳ Источник добавлен, но ждёт лимитов reader'а"
        await message.answer(f"{status_text}\nОсталось мест в лимите: {res.remaining_limit}")
        await _render_main(user.id, message.bot, message)
        return

    # Option B: parse @username (or t.me link -> @username) from text
    source_ref = _extract_username_from_text(message.text or "")
    if source_ref:
        res = await add_source_for_user(
            user_telegram_id=user.id,
            source_ref=source_ref,
            telegram_chat_id=None,
            source_type="channel",
        )
        logger.info("User %s adds source via @username: %s status=%s", user.id, source_ref, res.status)
        status_text = "✅ Источник добавлен. При появлении сообщений reader заполнит chat_id" if res.status == "active" else "⏳ Источник добавлен, ждёт лимитов"
        await message.answer(f"{status_text}\nОсталось мест в лимите: {res.remaining_limit}")
        await _render_main(user.id, message.bot, message)
        return

    await _delete_previous_menu_if_any(user.id, message.bot)
    sent = await message.answer(
        "➕ Как добавить канал:\n"
        "1) Перешлите сообщение из канала в ответ на этот ввод\n"
        "2) или напишите `/add @username`.\n"
        "Главное меню — кнопкой снизу 👇",
        reply_markup=main_menu(),
    )
    _remember_menu(user.id, sent.chat.id, sent.message_id)


@router.message(AddFSM.waiting_for_source)
async def addfsm_waiting_for_source(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return

    data = await state.get_data()
    menu_chat_id = data.get("menu_chat_id")
    menu_message_id = data.get("menu_message_id")

    user_id = message.from_user.id

    # Option A: forwarded message as reply to the bot prompt
    src_chat = None
    if message.reply_to_message and message.reply_to_message.forward_from_chat:
        src_chat = message.reply_to_message.forward_from_chat
    elif message.forward_from_chat:
        src_chat = message.forward_from_chat

    if src_chat is not None:
        source_type = str(src_chat.type or "channel")
        source_ref = f"@{src_chat.username}" if getattr(src_chat, "username", None) else str(src_chat.id)
        telegram_chat_id = src_chat.id
    else:
        # Option B: typed @username or link
        source_ref = _extract_username_from_text(message.text or "")
        if not source_ref:
            if menu_chat_id is not None and menu_message_id is not None:
                await message.bot.edit_message_text(
                    "Не понял источник. Пожалуйста, сделайте одно:\n"
                    "1) Перешлите сообщение из канала (можно как ответ)\n"
                    "2) или отправьте `@username` / ссылку на канал 🔗",
                    chat_id=int(menu_chat_id),
                    message_id=int(menu_message_id),
                    reply_markup=_back_keyboard("add_back_main"),
                )
            return
        source_type = "channel"
        telegram_chat_id = None

    res = await add_source_for_user(
        user_telegram_id=user_id,
        source_ref=source_ref,
        telegram_chat_id=telegram_chat_id,
        source_type=source_type,
    )
    logger.info("AddFSM: user=%s source_ref=%s status=%s", user_id, source_ref, res.status)
    await state.clear()

    confirm_text = (
        f"✅ Источник добавлен и подключается reader'ом.\nОсталось мест в лимите: {res.remaining_limit}"
        if res.status == "active"
        else f"⏳ Источник добавлен, но ждёт лимитов reader'а.\nОсталось мест в лимите: {res.remaining_limit}"
    )

    if menu_chat_id is not None and menu_message_id is not None:
        await message.bot.edit_message_text(
            confirm_text,
            chat_id=int(menu_chat_id),
            message_id=int(menu_message_id),
            reply_markup=main_menu(),
        )
    else:
        await message.answer(confirm_text, reply_markup=main_menu())


@router.message(Command("channels"))
async def cmd_channels(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    await cmd_channels_render(user.id, message)


async def cmd_channels_render(telegram_user_id: int, message: Message, page: int = 0) -> None:
    sources = await list_sources_for_user(telegram_user_id)
    # Basic pagination
    page_size = 10
    start = page * page_size
    chunk = sources[start : start + page_size]
    has_next = len(sources) > start + page_size

    active_count = sum(1 for s in sources if s.status == "active")
    await _delete_previous_menu_if_any(telegram_user_id, message.bot)
    sent = await message.answer(
        f"📌 Ваши каналы (активно в прослушивании: {active_count}). Страница {page+1}.\n"
        f"Нажмите на канал, чтобы настроить фильтры 👇",
        reply_markup=channels_menu([s.id for s in chunk], page=page, has_next=has_next),
    )
    _remember_menu(telegram_user_id, sent.chat.id, sent.message_id)


async def edit_channels_menu(telegram_user_id: int, menu_message: Message, page: int = 0) -> None:
    """
    Render the channels menu by editing the existing bot message (no "new message" UX).
    """
    sources = await list_sources_for_user(telegram_user_id)
    page_size = 10
    start = page * page_size
    chunk = sources[start : start + page_size]
    has_next = len(sources) > start + page_size

    active_count = sum(1 for s in sources if s.status == "active")
    await menu_message.edit_text(
        f"📌 Ваши каналы (активно в прослушивании: {active_count}). Страница {page+1}.\n"
        f"Нажмите на канал, чтобы настроить фильтры 👇",
        reply_markup=channels_menu([s.id for s in chunk], page=page, has_next=has_next),
    )


@router.callback_query(F.data == "menu_main")
async def cb_menu_main(call: CallbackQuery) -> None:
    await call.message.edit_text("Главное меню. Выберите, что сделать дальше 👇", reply_markup=main_menu())
    await call.answer()


@router.callback_query(F.data == "menu_channels")
async def cb_menu_channels(call: CallbackQuery) -> None:
    await call.answer()
    # Telegram provides user id in callback.message.from_user
    if call.from_user is None:
        return
    await edit_channels_menu(call.from_user.id, call.message)


@router.callback_query(F.data.startswith("channels_page:"))
async def cb_channels_page(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user is None:
        return
    _, page_s = call.data.split(":", 1)
    page = int(page_s)
    await edit_channels_menu(call.from_user.id, call.message, page=page)


@router.callback_query(F.data.startswith("source_menu:"))
async def cb_source_menu(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user is None:
        return
    _, sid_s = call.data.split(":", 1)
    source_id = int(sid_s)

    s_filters = await get_source_filter_settings(call.from_user.id, source_id)
    include_enabled = bool(s_filters.include_enabled) if s_filters else False
    stop_enabled = bool(s_filters.stop_enabled) if s_filters else False
    include_keywords_count = len(s_filters.include_keywords) if s_filters and s_filters.include_keywords else 0
    stop_words_count = len(s_filters.stop_words) if s_filters and s_filters.stop_words else 0

    await call.message.edit_text(
        f"Канал #{source_id}. Настройка фильтров:\n\n"
        f"• 🔑 Include: {'ВКЛ' if include_enabled else 'ВЫКЛ'} ({include_keywords_count})\n"
        f"• 🚫 Stop: {'ВКЛ' if stop_enabled else 'ВЫКЛ'} ({stop_words_count})\n",
        reply_markup=source_menu(
            source_id,
            include_enabled=include_enabled,
            stop_enabled=stop_enabled,
            include_keywords_count=include_keywords_count,
            stop_words_count=stop_words_count,
        ),
    )


@router.callback_query(F.data.startswith("source_toggle_include:"))
async def cb_source_toggle_include(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user is None:
        return
    _, sid_s = call.data.split(":", 1)
    source_id = int(sid_s)
    cur = await get_source_filter_settings(call.from_user.id, source_id)
    new_val = not bool(cur.include_enabled) if cur else True
    await set_source_filters(
        call.from_user.id,
        source_id=source_id,
        include_enabled=new_val,
        include_keywords=None,
        stop_enabled=None,
        stop_words=None,
    )
    # re-render
    s_filters = await get_source_filter_settings(call.from_user.id, source_id)
    include_enabled = bool(s_filters.include_enabled) if s_filters else False
    stop_enabled = bool(s_filters.stop_enabled) if s_filters else False
    include_keywords_count = len(s_filters.include_keywords) if s_filters and s_filters.include_keywords else 0
    stop_words_count = len(s_filters.stop_words) if s_filters and s_filters.stop_words else 0
    await call.message.edit_text(
        f"Канал #{source_id}. Настройка фильтров:\n\n"
        f"• 🔑 Include: {'ВКЛ' if include_enabled else 'ВЫКЛ'} ({include_keywords_count})\n"
        f"• 🚫 Stop: {'ВКЛ' if stop_enabled else 'ВЫКЛ'} ({stop_words_count})\n",
        reply_markup=source_menu(
            source_id,
            include_enabled=include_enabled,
            stop_enabled=stop_enabled,
            include_keywords_count=include_keywords_count,
            stop_words_count=stop_words_count,
        ),
    )


@router.callback_query(F.data.startswith("source_toggle_stop:"))
async def cb_source_toggle_stop(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user is None:
        return
    _, sid_s = call.data.split(":", 1)
    source_id = int(sid_s)
    cur = await get_source_filter_settings(call.from_user.id, source_id)
    new_val = not bool(cur.stop_enabled) if cur else True
    await set_source_filters(
        call.from_user.id,
        source_id=source_id,
        include_enabled=None,
        include_keywords=None,
        stop_enabled=new_val,
        stop_words=None,
    )
    s_filters = await get_source_filter_settings(call.from_user.id, source_id)
    include_enabled = bool(s_filters.include_enabled) if s_filters else False
    stop_enabled = bool(s_filters.stop_enabled) if s_filters else False
    include_keywords_count = len(s_filters.include_keywords) if s_filters and s_filters.include_keywords else 0
    stop_words_count = len(s_filters.stop_words) if s_filters and s_filters.stop_words else 0
    await call.message.edit_text(
        f"Канал #{source_id}. Настройка фильтров:\n\n"
        f"• 🔑 Include: {'ВКЛ' if include_enabled else 'ВЫКЛ'} ({include_keywords_count})\n"
        f"• 🚫 Stop: {'ВКЛ' if stop_enabled else 'ВЫКЛ'} ({stop_words_count})\n",
        reply_markup=source_menu(
            source_id,
            include_enabled=include_enabled,
            stop_enabled=stop_enabled,
            include_keywords_count=include_keywords_count,
            stop_words_count=stop_words_count,
        ),
    )


@router.callback_query(F.data.startswith("source_clear_include:"))
async def cb_source_clear_include(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user is None:
        return
    _, sid_s = call.data.split(":", 1)
    source_id = int(sid_s)

    await set_source_filters(
        call.from_user.id,
        source_id=source_id,
        include_enabled=None,
        include_keywords=[],
        stop_enabled=None,
        stop_words=None,
    )

    s_filters = await get_source_filter_settings(call.from_user.id, source_id)
    include_enabled = bool(s_filters.include_enabled) if s_filters else False
    stop_enabled = bool(s_filters.stop_enabled) if s_filters else False
    include_keywords_count = 0
    stop_words_count = len(s_filters.stop_words) if s_filters and s_filters.stop_words else 0

    await call.message.edit_text(
        f"Канал #{source_id}. Настройка фильтров:\n\n"
        f"• 🔑 Include: {'ВКЛ' if include_enabled else 'ВЫКЛ'} ({include_keywords_count})\n"
        f"• 🚫 Stop: {'ВКЛ' if stop_enabled else 'ВЫКЛ'} ({stop_words_count})\n",
        reply_markup=source_menu(
            source_id,
            include_enabled=include_enabled,
            stop_enabled=stop_enabled,
            include_keywords_count=include_keywords_count,
            stop_words_count=stop_words_count,
        ),
    )


@router.callback_query(F.data.startswith("source_clear_stop:"))
async def cb_source_clear_stop(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user is None:
        return
    _, sid_s = call.data.split(":", 1)
    source_id = int(sid_s)

    await set_source_filters(
        call.from_user.id,
        source_id=source_id,
        include_enabled=None,
        include_keywords=None,
        stop_enabled=None,
        stop_words=[],
    )

    s_filters = await get_source_filter_settings(call.from_user.id, source_id)
    include_enabled = bool(s_filters.include_enabled) if s_filters else False
    stop_enabled = bool(s_filters.stop_enabled) if s_filters else False
    include_keywords_count = len(s_filters.include_keywords) if s_filters and s_filters.include_keywords else 0
    stop_words_count = 0

    await call.message.edit_text(
        f"Канал #{source_id}. Настройка фильтров:\n\n"
        f"• 🔑 Include: {'ВКЛ' if include_enabled else 'ВЫКЛ'} ({include_keywords_count})\n"
        f"• 🚫 Stop: {'ВКЛ' if stop_enabled else 'ВЫКЛ'} ({stop_words_count})\n",
        reply_markup=source_menu(
            source_id,
            include_enabled=include_enabled,
            stop_enabled=stop_enabled,
            include_keywords_count=include_keywords_count,
            stop_words_count=stop_words_count,
        ),
    )


@router.callback_query(F.data.startswith("source_set_include_keywords:"))
async def cb_source_set_include_keywords(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if call.from_user is None:
        return
    _, sid_s = call.data.split(":", 1)
    source_id = int(sid_s)
    await state.update_data(
        scope="source",
        source_id=source_id,
        menu_chat_id=call.message.chat.id,
        menu_message_id=call.message.message_id,
    )
    await state.set_state(KeywordFSM.setting_source_include_keywords)
    logger.info("User %s setting source include keywords (source_id=%s)", call.from_user.id, source_id)
    await call.message.edit_text(
        "🔑 Введите keywords через запятую или с новой строки.\n"
        "Например: новости, политика, экономика",
        reply_markup=_back_keyboard(f"source_back:{source_id}"),
    )


@router.callback_query(F.data.startswith("source_set_stopwords:"))
async def cb_source_set_stopwords(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if call.from_user is None:
        return
    _, sid_s = call.data.split(":", 1)
    source_id = int(sid_s)
    await state.update_data(
        scope="source",
        source_id=source_id,
        menu_chat_id=call.message.chat.id,
        menu_message_id=call.message.message_id,
    )
    await state.set_state(KeywordFSM.setting_source_stop_words)
    logger.info("User %s setting source stop-words (source_id=%s)", call.from_user.id, source_id)
    await call.message.edit_text(
        "🚫 Введите stop-words через запятую или с новой строки.\n"
        "Например: реклама, спам, мошенники",
        reply_markup=_back_keyboard(f"source_back:{source_id}"),
    )


@router.callback_query(F.data.startswith("source_back:"))
async def cb_source_back(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if call.from_user is None:
        return
    _, sid_s = call.data.split(":", 1)
    source_id = int(sid_s)
    await state.clear()

    s_filters = await get_source_filter_settings(call.from_user.id, source_id)
    include_enabled = bool(s_filters.include_enabled) if s_filters else False
    stop_enabled = bool(s_filters.stop_enabled) if s_filters else False
    include_keywords_count = len(s_filters.include_keywords) if s_filters and s_filters.include_keywords else 0
    stop_words_count = len(s_filters.stop_words) if s_filters and s_filters.stop_words else 0

    await call.message.edit_text(
        f"Канал #{source_id}. Настройка фильтров:\n\n"
        f"• 🔑 Include: {'ВКЛ' if include_enabled else 'ВЫКЛ'} ({include_keywords_count})\n"
        f"• 🚫 Stop: {'ВКЛ' if stop_enabled else 'ВЫКЛ'} ({stop_words_count})\n",
        reply_markup=source_menu(
            source_id,
            include_enabled=include_enabled,
            stop_enabled=stop_enabled,
            include_keywords_count=include_keywords_count,
            stop_words_count=stop_words_count,
        ),
    )


@router.callback_query(F.data == "menu_settings")
async def cb_menu_settings(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user is None:
        return
    ds = await get_user_digest_settings(call.from_user.id)
    digest_enabled = bool(ds.digest_enabled) if ds else True
    short_enabled = bool(ds.short_enabled) if ds else True
    media_enabled = bool(ds.media_enabled) if ds else False
    include_enabled = bool(ds.include_enabled) if ds else False
    include_keywords_count = len(ds.include_keywords) if ds and ds.include_keywords else 0
    stop_enabled = bool(ds.stop_enabled) if ds else False
    stop_words_count = len(ds.stop_words) if ds and ds.stop_words else 0
    await call.message.edit_text(
        "🎛️ Настройки дайджеста.\n"
        "Ниже — быстрые переключатели. Фильтры include/stop можно настроить отдельным меню.",
        reply_markup=settings_menu(
            digest_enabled=digest_enabled,
            short_enabled=short_enabled,
            media_enabled=media_enabled,
            include_enabled=include_enabled,
            include_keywords_count=include_keywords_count,
            stop_enabled=stop_enabled,
            stop_words_count=stop_words_count,
        ),
    )


@router.callback_query(F.data == "menu_subscription")
async def cb_menu_subscription(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user is None:
        return
    sources = await list_sources_for_user(call.from_user.id)
    active_count = sum(1 for s in sources if s.status == "active")
    limit = settings.max_sources_per_user_free  # paid later
    await call.message.edit_text(
        f"🧾 Подписка (пока без оплаты)\n"
        f"Лимит: {limit} каналов активного прослушивания\n"
        f"Сейчас активных: {active_count}\n",
        reply_markup=main_menu(),
    )


@router.message(Command("subscription"))
async def cmd_subscription(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    await _delete_previous_menu_if_any(user.id, message.bot)
    sources = await list_sources_for_user(user.id)
    active_count = sum(1 for s in sources if s.status == "active")
    limit = settings.max_sources_per_user_free  # paid later
    sent = await message.answer(
        f"🧾 Подписка (пока без оплаты)\n"
        f"Лимит: {limit} каналов активного прослушивания\n"
        f"Сейчас активных: {active_count}\n"
        f"Пока что обновление — не реализовано, но лимит уже учитывается.",
        reply_markup=main_menu(),
    )
    _remember_menu(user.id, sent.chat.id, sent.message_id)


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    await _delete_previous_menu_if_any(user.id, message.bot)
    ds = await get_user_digest_settings(user.id)
    digest_enabled = bool(ds.digest_enabled) if ds else True
    short_enabled = bool(ds.short_enabled) if ds else True
    media_enabled = bool(ds.media_enabled) if ds else False
    include_enabled = bool(ds.include_enabled) if ds else False
    include_keywords_count = len(ds.include_keywords) if ds and ds.include_keywords else 0
    stop_enabled = bool(ds.stop_enabled) if ds else False
    stop_words_count = len(ds.stop_words) if ds and ds.stop_words else 0
    sent = await message.answer(
        "🎛️ Настройки дайджеста.\n"
        "Быстрые переключатели — через кнопки ниже, а детальные настройки — через команды.",
        reply_markup=settings_menu(
            digest_enabled=digest_enabled,
            short_enabled=short_enabled,
            media_enabled=media_enabled,
            include_enabled=include_enabled,
            include_keywords_count=include_keywords_count,
            stop_enabled=stop_enabled,
            stop_words_count=stop_words_count,
        ),
    )
    _remember_menu(user.id, sent.chat.id, sent.message_id)


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    if settings.admin_support_username:
        link = f"https://t.me/{settings.admin_support_username.lstrip('@')}"
        await message.answer(f"💬 Поддержка: {link}")
    else:
        await message.answer("💬 Поддержка: админ не настроен (нет ADMIN_SUPPORT_USERNAME).")


@router.message(Command("remove"))
async def cmd_remove(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    text = message.text or ""
    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: `/remove <source_id>` (ID можно посмотреть в `/channels`).")
        return
    source_id = int(parts[1])
    ok = await remove_source_for_user(user.id, source_id)
    logger.info("User %s removes source_id=%s ok=%s", user.id, source_id, ok)
    if ok:
        await message.answer(f"🛑 Источник #{source_id} удалён.")
    else:
        await message.answer(f"Не удалось удалить источник #{source_id} (не найден или не принадлежит вам).")
    await cmd_channels_render(user.id, message)


@router.callback_query(F.data == "menu_add")
async def cb_menu_add(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if call.from_user is None:
        return
    await state.update_data(menu_chat_id=call.message.chat.id, menu_message_id=call.message.message_id)
    await state.set_state(AddFSM.waiting_for_source)
    logger.info("User %s started add flow", call.from_user.id)
    await call.message.edit_text(
        "➕ Как добавить канал\n\n"
        "1) Перешлите сообщение из канала в ответ на это сообщение ✅\n"
        "2) или просто отправьте `@username` / ссылку на канал 🔗\n\n"
        "После добавления вы увидите канал в `Мои каналы` и он подключится, если лимиты позволят.",
        reply_markup=_back_keyboard("add_back_main"),
    )


@router.callback_query(F.data == "add_back_main")
async def cb_add_back_main(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if call.from_user is None:
        return
    await state.clear()
    await call.message.edit_text("Главное меню. Выберите, что сделать дальше 👇", reply_markup=main_menu())


@router.callback_query(F.data == "menu_support")
async def cb_menu_support(call: CallbackQuery) -> None:
    await call.answer()
    if settings.admin_support_username:
        link = f"https://t.me/{settings.admin_support_username.lstrip('@')}"
        await call.message.edit_text(f"💬 Поддержка:\n{link}\n\nОткройте чат и напишите администратору.")
    else:
        await call.message.edit_text("💬 Поддержка: админ не настроен (нет ADMIN_SUPPORT_USERNAME).")


@router.callback_query(F.data == "toggle_digest")
async def cb_toggle_digest(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user is None:
        return
    ds = await get_user_digest_settings(call.from_user.id)
    cur = bool(ds.digest_enabled) if ds else True
    await update_user_digest_settings(call.from_user.id, digest_enabled=not cur)
    await cb_menu_settings(call)


@router.callback_query(F.data == "toggle_short")
async def cb_toggle_short(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user is None:
        return
    ds = await get_user_digest_settings(call.from_user.id)
    cur = bool(ds.short_enabled) if ds else True
    await update_user_digest_settings(call.from_user.id, short_enabled=not cur)
    await cb_menu_settings(call)


@router.callback_query(F.data == "toggle_media")
async def cb_toggle_media(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user is None:
        return
    ds = await get_user_digest_settings(call.from_user.id)
    cur = bool(ds.media_enabled) if ds else False
    await update_user_digest_settings(call.from_user.id, media_enabled=not cur)
    await cb_menu_settings(call)


@router.callback_query(F.data == "global_include_menu")
async def cb_global_include_menu(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if call.from_user is None:
        return
    await state.update_data(
        scope="global",
        kind="include",
        menu_chat_id=call.message.chat.id,
        menu_message_id=call.message.message_id,
    )
    await state.set_state(KeywordFSM.setting_global_include_keywords)
    logger.info("User %s setting global include keywords", call.from_user.id)
    await call.message.edit_text(
        "🔑 Введите include-keywords через запятую или с новой строки.\n"
        "Например: новости, политика",
        reply_markup=_back_keyboard("back_settings_menu"),
    )


@router.callback_query(F.data == "global_stop_menu")
async def cb_global_stop_menu(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if call.from_user is None:
        return
    await state.update_data(
        scope="global",
        kind="stop",
        menu_chat_id=call.message.chat.id,
        menu_message_id=call.message.message_id,
    )
    await state.set_state(KeywordFSM.setting_global_stop_words)
    logger.info("User %s setting global stop-words", call.from_user.id)
    await call.message.edit_text(
        "🚫 Введите stop-words через запятую или с новой строки.\n"
        "Например: спам, мошенники",
        reply_markup=_back_keyboard("back_settings_menu"),
    )


@router.callback_query(F.data == "back_settings_menu")
async def cb_back_settings_menu(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if call.from_user is None:
        return
    await state.clear()

    ds = await get_user_digest_settings(call.from_user.id)
    digest_enabled = bool(ds.digest_enabled) if ds else True
    short_enabled = bool(ds.short_enabled) if ds else True
    media_enabled = bool(ds.media_enabled) if ds else False
    include_enabled = bool(ds.include_enabled) if ds else False
    include_keywords_count = len(ds.include_keywords) if ds and ds.include_keywords else 0
    stop_enabled = bool(ds.stop_enabled) if ds else False
    stop_words_count = len(ds.stop_words) if ds and ds.stop_words else 0

    await call.message.edit_text(
        "🎛️ Настройки дайджеста.\n"
        "Ниже — быстрые переключатели. Фильтры include/stop можно настроить отдельным меню.",
        reply_markup=settings_menu(
            digest_enabled=digest_enabled,
            short_enabled=short_enabled,
            media_enabled=media_enabled,
            include_enabled=include_enabled,
            include_keywords_count=include_keywords_count,
            stop_enabled=stop_enabled,
            stop_words_count=stop_words_count,
        ),
    )


@router.message(KeywordFSM.setting_global_include_keywords)
async def fsm_global_include_keywords(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    menu_chat_id = int(data.get("menu_chat_id"))
    menu_message_id = int(data.get("menu_message_id"))
    keywords = _parse_keywords(message.text or "")
    await update_user_digest_settings(
        message.from_user.id,
        include_enabled=True,
        include_keywords=keywords,
    )
    await state.clear()

    ds = await get_user_digest_settings(message.from_user.id)
    digest_enabled = bool(ds.digest_enabled) if ds else True
    short_enabled = bool(ds.short_enabled) if ds else True
    media_enabled = bool(ds.media_enabled) if ds else False
    include_enabled = bool(ds.include_enabled) if ds else False
    include_keywords_count = len(ds.include_keywords) if ds and ds.include_keywords else 0
    stop_enabled = bool(ds.stop_enabled) if ds else False
    stop_words_count = len(ds.stop_words) if ds and ds.stop_words else 0

    await message.bot.edit_message_text(
        "🎛️ Настройки дайджеста.\n"
        "Ниже — быстрые переключатели. Фильтры include/stop можно настроить отдельным меню.",
        chat_id=menu_chat_id,
        message_id=menu_message_id,
        reply_markup=settings_menu(
            digest_enabled=digest_enabled,
            short_enabled=short_enabled,
            media_enabled=media_enabled,
            include_enabled=include_enabled,
            include_keywords_count=include_keywords_count,
            stop_enabled=stop_enabled,
            stop_words_count=stop_words_count,
        ),
    )


@router.message(KeywordFSM.setting_global_stop_words)
async def fsm_global_stop_words(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    menu_chat_id = int(data.get("menu_chat_id"))
    menu_message_id = int(data.get("menu_message_id"))
    words = _parse_keywords(message.text or "")
    await update_user_digest_settings(
        message.from_user.id,
        stop_enabled=True,
        stop_words=words,
    )
    await state.clear()

    ds = await get_user_digest_settings(message.from_user.id)
    digest_enabled = bool(ds.digest_enabled) if ds else True
    short_enabled = bool(ds.short_enabled) if ds else True
    media_enabled = bool(ds.media_enabled) if ds else False
    include_enabled = bool(ds.include_enabled) if ds else False
    include_keywords_count = len(ds.include_keywords) if ds and ds.include_keywords else 0
    stop_enabled = bool(ds.stop_enabled) if ds else False
    stop_words_count = len(ds.stop_words) if ds and ds.stop_words else 0

    await message.bot.edit_message_text(
        "🎛️ Настройки дайджеста.\n"
        "Ниже — быстрые переключатели. Фильтры include/stop можно настроить отдельным меню.",
        chat_id=menu_chat_id,
        message_id=menu_message_id,
        reply_markup=settings_menu(
            digest_enabled=digest_enabled,
            short_enabled=short_enabled,
            media_enabled=media_enabled,
            include_enabled=include_enabled,
            include_keywords_count=include_keywords_count,
            stop_enabled=stop_enabled,
            stop_words_count=stop_words_count,
        ),
    )


@router.message(Command("digest_on"))
async def cmd_digest_on(message: Message) -> None:
    if message.from_user is None:
        return
    await update_user_digest_settings(message.from_user.id, digest_enabled=True)
    await cmd_settings(message)


@router.message(Command("digest_off"))
async def cmd_digest_off(message: Message) -> None:
    if message.from_user is None:
        return
    await update_user_digest_settings(message.from_user.id, digest_enabled=False)
    await cmd_settings(message)


@router.message(Command("short_on"))
async def cmd_short_on(message: Message) -> None:
    if message.from_user is None:
        return
    await update_user_digest_settings(message.from_user.id, short_enabled=True)
    await cmd_settings(message)


@router.message(Command("short_off"))
async def cmd_short_off(message: Message) -> None:
    if message.from_user is None:
        return
    await update_user_digest_settings(message.from_user.id, short_enabled=False)
    await cmd_settings(message)


@router.message(Command("media_on"))
async def cmd_media_on(message: Message) -> None:
    if message.from_user is None:
        return
    await update_user_digest_settings(message.from_user.id, media_enabled=True)
    await cmd_settings(message)


@router.message(Command("media_off"))
async def cmd_media_off(message: Message) -> None:
    if message.from_user is None:
        return
    await update_user_digest_settings(message.from_user.id, media_enabled=False)
    await cmd_settings(message)


@router.message(Command("include_on"))
async def cmd_include_on(message: Message) -> None:
    if message.from_user is None:
        return
    await update_user_digest_settings(message.from_user.id, include_enabled=True)
    await cmd_settings(message)


@router.message(Command("include_off"))
async def cmd_include_off(message: Message) -> None:
    if message.from_user is None:
        return
    await update_user_digest_settings(message.from_user.id, include_enabled=False)
    await cmd_settings(message)


@router.message(Command("stop_on"))
async def cmd_stop_on(message: Message) -> None:
    if message.from_user is None:
        return
    await update_user_digest_settings(message.from_user.id, stop_enabled=True)
    await cmd_settings(message)


@router.message(Command("stop_off"))
async def cmd_stop_off(message: Message) -> None:
    if message.from_user is None:
        return
    await update_user_digest_settings(message.from_user.id, stop_enabled=False)
    await cmd_settings(message)


@router.message(Command("set_keywords"))
async def cmd_set_keywords(message: Message) -> None:
    if message.from_user is None:
        return
    text = message.text or ""
    parts = text.split(maxsplit=1)
    raw = parts[1] if len(parts) > 1 else ""
    keywords = _parse_keywords(raw)
    await update_user_digest_settings(message.from_user.id, include_enabled=True, include_keywords=keywords)
    await cmd_settings(message)


@router.message(Command("set_stopwords"))
async def cmd_set_stopwords(message: Message) -> None:
    if message.from_user is None:
        return
    text = message.text or ""
    parts = text.split(maxsplit=1)
    raw = parts[1] if len(parts) > 1 else ""
    words = _parse_keywords(raw)
    await update_user_digest_settings(message.from_user.id, stop_enabled=True, stop_words=words)
    await cmd_settings(message)


@router.message(Command("digest_time"))
async def cmd_digest_time(message: Message) -> None:
    if message.from_user is None:
        return
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: `/digest_time HH:MM` (например, `/digest_time 09:00`).")
        return
    hhmm = parts[1].strip()
    m = re.match(r"^(\d{2}):(\d{2})$", hhmm)
    if not m:
        await message.answer("Неверный формат. Пример: `/digest_time 09:00`.")
        return
    h = int(m.group(1))
    mi = int(m.group(2))
    await update_user_digest_settings(message.from_user.id, digest_time=__import__("datetime").time(h, mi))
    await cmd_settings(message)


@router.message(KeywordFSM.setting_source_include_keywords)
async def fsm_source_include_keywords(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    source_id = int(data.get("source_id"))
    menu_chat_id = int(data.get("menu_chat_id"))
    menu_message_id = int(data.get("menu_message_id"))
    keywords = _parse_keywords(message.text or "")
    await set_source_filters(
        message.from_user.id,
        source_id=source_id,
        include_enabled=True,
        include_keywords=keywords,
        stop_enabled=None,
        stop_words=None,
    )
    await state.clear()
    ds = await get_source_filter_settings(message.from_user.id, source_id)
    include_enabled = bool(ds.include_enabled) if ds else True
    stop_enabled = bool(ds.stop_enabled) if ds else False
    include_keywords_count = len(ds.include_keywords) if ds and ds.include_keywords else 0
    stop_words_count = len(ds.stop_words) if ds and ds.stop_words else 0
    await message.bot.edit_message_text(
        f"Канал #{source_id}. Настройка фильтров:\n\n"
        f"• 🔑 Include: {'ВКЛ' if include_enabled else 'ВЫКЛ'} ({include_keywords_count})\n"
        f"• 🚫 Stop: {'ВКЛ' if stop_enabled else 'ВЫКЛ'} ({stop_words_count})\n"
        f"\n✅ Include keywords обновлены.",
        chat_id=menu_chat_id,
        message_id=menu_message_id,
        reply_markup=source_menu(
            source_id,
            include_enabled=include_enabled,
            stop_enabled=stop_enabled,
            include_keywords_count=include_keywords_count,
            stop_words_count=stop_words_count,
        ),
    )


@router.message(KeywordFSM.setting_source_stop_words)
async def fsm_source_stop_keywords(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    source_id = int(data.get("source_id"))
    menu_chat_id = int(data.get("menu_chat_id"))
    menu_message_id = int(data.get("menu_message_id"))
    keywords = _parse_keywords(message.text or "")
    await set_source_filters(
        message.from_user.id,
        source_id=source_id,
        include_enabled=None,
        include_keywords=None,
        stop_enabled=True,
        stop_words=keywords,
    )
    await state.clear()
    ds = await get_source_filter_settings(message.from_user.id, source_id)
    include_enabled = bool(ds.include_enabled) if ds else False
    stop_enabled = bool(ds.stop_enabled) if ds else True
    include_keywords_count = len(ds.include_keywords) if ds and ds.include_keywords else 0
    stop_words_count = len(ds.stop_words) if ds and ds.stop_words else 0
    await message.bot.edit_message_text(
        f"Канал #{source_id}. Настройка фильтров:\n\n"
        f"• 🔑 Include: {'ВКЛ' if include_enabled else 'ВЫКЛ'} ({include_keywords_count})\n"
        f"• 🚫 Stop: {'ВКЛ' if stop_enabled else 'ВЫКЛ'} ({stop_words_count})\n"
        f"\n✅ Stop-words обновлены.",
        chat_id=menu_chat_id,
        message_id=menu_message_id,
        reply_markup=source_menu(
            source_id,
            include_enabled=include_enabled,
            stop_enabled=stop_enabled,
            include_keywords_count=include_keywords_count,
            stop_words_count=stop_words_count,
        ),
    )


def get_dispatcher() -> Dispatcher:
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    return dp

