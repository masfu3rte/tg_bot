from aiogram.fsm.state import StatesGroup, State


class ProfileEdit(StatesGroup):
    waiting_for_cdek_form = State()
    waiting_for_req_form = State()


class RequestCreate(StatesGroup):
    waiting_for_internal_title = State()
    waiting_for_item_name = State()
    waiting_for_description = State()
    waiting_for_photo = State()


class RequestEdit(StatesGroup):
    waiting_for_internal_title = State()
    waiting_for_item_name = State()
    waiting_for_description = State()


class OfferCreate(StatesGroup):
    waiting_for_price = State()
    waiting_for_days = State()
    waiting_for_condition = State()
    waiting_for_photo = State()


class DealTrack(StatesGroup):
    waiting_for_track = State()
