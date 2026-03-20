from aiogram.fsm.state import State, StatesGroup


class KeywordFSM(StatesGroup):
    setting_global_include_keywords = State()
    setting_global_stop_words = State()
    setting_source_include_keywords = State()
    setting_source_stop_words = State()


class AddFSM(StatesGroup):
    waiting_for_source = State()

