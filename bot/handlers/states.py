from aiogram.fsm.state import State, StatesGroup

class OnboardingStates(StatesGroup):
    waiting_for_ready = State()
    waiting_for_time = State()
    waiting_for_gender = State()
    waiting_for_age = State()
    waiting_for_height = State()
    waiting_for_weight = State()
    waiting_for_waist = State()
    waiting_for_target = State()

class MeasurementStates(StatesGroup):
    waiting_for_monthly_data = State()
