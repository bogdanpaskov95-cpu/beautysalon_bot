import json
import os
import uuid
import re
import asyncio
from datetime import datetime, date, time, timedelta

# Спроба завантаження змінних оточення
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

# ---------------- CONFIGURATION ----------------
API_TOKEN = os.getenv("BOT_TOKEN", "8428204566:AAFyJbCJv5R8zUiyyEvWe0RWUtokPQBxwZg")
ADMIN_ID = int(os.getenv("ADMIN_ID", "834450069"))
DATA_FILE = "appointments.json"

# Робочий графік
WORK_SCHEDULE = {
    0: (10, 20),  # Пн
    1: (10, 20),  # Вт
    2: (10, 20),  # Ср
    3: (10, 20),  # Чт
    4: (10, 20),  # Пт
    5: (11, 17),  # Сб
    6: (11, 17),  # Нд
}
SLOT_MINUTES = 30

# Контакти
SALON_CONTACTS = {
    "tg": "@snchkss",
    "phone": "+380 67 388 07 81",
    "address": "м. Луцьк, вул. Львівська 75, Волинська область, гуртожиток біля ЛНТУ"
}

# База даних майстрів та послуг
MASTERS = {
    "manicure": {
        "title": "Галя",
        "services": {
            "manicure_basic": ("Манікюр стандартний", 350, 45),
            "manicure_gel": ("Манікюр гелевий", 650, 90),
            "pedicure": ("Педикюр", 700, 60),
        }
    },
    "hair": {
        "title": "Ібрагім",
        "services": {
            "hair_cut": ("Стрижка", 450, 45),
            "hair_styling": ("Укладання", 500, 60),
            "coloring": ("Фарбування", 1500, 150),
        }
    },
    "brows": {
        "title": "Ігнат",
        "services": {
            "brow_lamination": ("Ламінування брів", 500, 60),
            "lashes": ("Нарощування вій", 900, 120),
            "brow_tint": ("Фарбування брів", 220, 20),
        }
    }
}

# ---------------- FSM STATES ----------------
class BookingStates(StatesGroup):
    entering_name = State()
    entering_phone = State()
    choosing_master = State()
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()

class EditStates(StatesGroup):
    selecting_appointment = State()
    editing_date = State()
    choosing_time_for_edit = State()

class DeleteStates(StatesGroup):
    selecting_appointment = State()

# ---------------- INITIALIZATION ----------------
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

# ---------------- UTILS ----------------
def ensure_json_exists():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

def load_appointments():
    ensure_json_exists()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_appointments(appts):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(appts, f, ensure_ascii=False, indent=2)

def format_appointment(a, show_number=False, number=None):
    text = ""
    if show_number:
        text = f"📌 Запис #{number}\n"
    text += (
        f"👤 {a.get('user_name','Клієнт')} ({a.get('phone','')})\n"
        f"👩‍🔧 {a.get('master_title')}\n"
        f"✨ {a.get('service_title')} — {a.get('price')}₴ • {a.get('duration')} хв\n"
        f"📅 {a.get('date')} 🕒 {a.get('time')}"
    )
    return text

# Background task for reminders
async def send_reminders():
    while True:
        try:
            now = datetime.now()
            tomorrow = (now + timedelta(days=1)).date()
            appts = load_appointments()
            
            for appt in appts:
                if appt.get("reminder_sent"):
                    continue
                try:
                    appt_date = datetime.strptime(appt["date"], "%Y-%m-%d").date()
                except: 
                    continue
                
                if appt_date == tomorrow:
                    try:
                        reminder_text = (
                            "🔔 НАГАДУВАННЯ!\n\n"
                            f"Завтра у вас запис:\n\n"
                            f"{format_appointment(appt)}\n\n"
                            f"Чекаємо на вас! 💖"
                        )
                        await bot.send_message(appt["user_id"], reminder_text)
                        appt["reminder_sent"] = True
                        save_appointments(appts)
                        print(f"Reminder sent to: {appt['user_name']}")
                    except Exception as e:
                        print(f"Error sending reminder: {e}")
        except Exception as e:
            print(f"Error in background task: {e}")
        
        await asyncio.sleep(6 * 60 * 60)

def get_work_hours_for_date(d: date):
    return WORK_SCHEDULE.get(d.weekday(), None)

def generate_slots_for_date(d: date):
    hours = get_work_hours_for_date(d)
    if not hours:
        return []
    start_h, end_h = hours
    slots = []
    cur = datetime.combine(d, time(start_h, 0))
    end_dt = datetime.combine(d, time(end_h, 0))
    while cur + timedelta(minutes=SLOT_MINUTES) <= end_dt:
        slots.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=SLOT_MINUTES)
    return slots

def is_conflict(start_dt, dur_min, other_start, other_dur):
    a1 = start_dt
    a2 = start_dt + timedelta(minutes=dur_min)
    b1 = other_start
    b2 = other_start + timedelta(minutes=other_dur)
    return not (a2 <= b1 or b2 <= a1)

def get_available_slots(master_key, duration, d, exclude_appt_id=None):
    all_slots = generate_slots_for_date(d)
    appts = load_appointments()
    available = []
    for s in all_slots:
        try:
            start_dt = datetime.strptime(f"{d.isoformat()} {s}", "%Y-%m-%d %H:%M")
            conflict = False
            for ex in appts:
                if exclude_appt_id and ex.get("id") == exclude_appt_id:
                    continue
                if ex.get("master") != master_key:
                    continue
                try:
                    ex_dt = datetime.strptime(f"{ex['date']} {ex['time']}", "%Y-%m-%d %H:%M")
                    ex_dur = ex.get("duration", 0)
                    if is_conflict(start_dt, duration, ex_dt, ex_dur):
                        conflict = True
                        break
                except: continue
            if not conflict:
                available.append(s)
        except: continue
    return available

# ---------------- KEYBOARDS ----------------
def main_menu_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📅 Запис", "📋 Усі записи")
    kb.add("🧾 Мої записи", "📞 Контакти")
    return kb

def my_appointments_menu_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✏️ Редагувати запис", "❌ Видалити запис")
    kb.add("⬅️ Головне меню")
    return kb

def masters_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for key, info in MASTERS.items():
        kb.add(info["title"])
    kb.add("⬅️ Назад")
    return kb

def services_kb(master_key):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for sk, svc in MASTERS[master_key]["services"].items():
        name, price, dur = svc
        kb.add(f"{name} — {price}₴ — {dur} хв")
    kb.add("⬅️ Назад")
    return kb

def date_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    today = date.today()
    for i in range(14):
        d = today + timedelta(days=i)
        kb.add(types.KeyboardButton(d.strftime("%d.%m.%Y")))
    kb.add("⬅️ Назад")
    return kb

def slots_kb(slots):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    for s in slots:
        kb.add(types.KeyboardButton(s))
    kb.add("⬅️ Назад")
    return kb

def numbers_kb(count):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    for i in range(1, count + 1):
        kb.add(str(i))
    kb.add("⬅️ Назад")
    return kb

# ---------------- HANDLERS ----------------
@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    ensure_json_exists()
    await message.answer("Вітаю! Я бот запису в салон. Оберіть дію:", reply_markup=main_menu_kb())

@dp.message_handler(lambda m: m.text == "📞 Контакти", state='*')
async def show_contacts(message: types.Message, state: FSMContext):
    await state.finish()
    start_h, end_h = WORK_SCHEDULE[0]
    start_wk_h, end_wk_h = WORK_SCHEDULE[5]
    text = (
        f"📞 Контакти салону:\n"
        f"Telegram: {SALON_CONTACTS['tg']}\n"
        f"Телефон: {SALON_CONTACTS['phone']}\n"
        f"Адреса: {SALON_CONTACTS['address']}\n\n"
        f"Графік роботи:\nПн-Пт: {start_h:02d}:00 - {end_h:02d}:00\nСб-Нд: {start_wk_h:02d}:00 - {end_wk_h:02d}:00"
    )
    await message.answer(text, reply_markup=main_menu_kb())

@dp.message_handler(lambda m: m.text == "📋 Усі записи", state='*')
async def show_all_appointments(message: types.Message, state: FSMContext):
    await state.finish()
    appts = load_appointments()
    if not appts:
        await message.answer("Записів немає.", reply_markup=main_menu_kb())
        return
    text = "📋 Всі записи:\n\n"
    for i, a in enumerate(appts, 1):
        text += f"{i}. {format_appointment(a)}\n\n"
    await message.answer(text, reply_markup=main_menu_kb())

@dp.message_handler(lambda m: m.text in ["⬅️ Назад", "⬅️ Головне меню"], state='*')
async def show_main_menu(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Головне меню:", reply_markup=main_menu_kb())

@dp.message_handler(lambda m: m.text == "🧾 Мої записи", state='*')
async def show_my_appointments(message: types.Message, state: FSMContext):
    await state.finish()
    appts = load_appointments()
    user_appts = [a for a in appts if a.get("user_id") == message.from_user.id]
    
    if not user_appts:
        await message.answer("У вас немає активних записів.", reply_markup=main_menu_kb())
        return
    
    text = "🧾 Ваші записи:\n\n"
    for i, a in enumerate(user_appts, 1):
        text += format_appointment(a, show_number=True, number=i) + "\n\n"
    await message.answer(text, reply_markup=my_appointments_menu_kb())

# ---------- EDIT LOGIC ----------
@dp.message_handler(lambda m: m.text == "✏️ Редагувати запис", state='*')
async def start_edit(message: types.Message, state: FSMContext):
    appts = load_appointments()
    user_appts = [a for a in appts if a.get("user_id") == message.from_user.id]
    if not user_appts:
        await message.answer("У вас немає записів для редагування.", reply_markup=main_menu_kb())
        return
    text = "🧾 Ваші записи:\n\n"
    for i, a in enumerate(user_appts, 1):
        text += format_appointment(a, show_number=True, number=i) + "\n\n"
    text += "\nВведіть номер запису, який хочете редагувати:"
    await EditStates.selecting_appointment.set()
    await state.update_data(user_appointments=user_appts)
    await message.answer(text, reply_markup=numbers_kb(len(user_appts)))

@dp.message_handler(state=EditStates.selecting_appointment)
async def select_edit_appointment(message: types.Message, state: FSMContext):
    if message.text in ["⬅️ Назад", "⬅️ Головне меню"]:
        await state.finish()
        await message.answer("Редагування скасовано.", reply_markup=main_menu_kb())
        return
    try:
        num = int(message.text)
        data = await state.get_data()
        user_appts = data.get("user_appointments", [])
        if num < 1 or num > len(user_appts):
            await message.answer(f"Введіть число від 1 до {len(user_appts)}:", reply_markup=numbers_kb(len(user_appts)))
            return
        selected_appt = user_appts[num - 1]
        await state.update_data(
            edit_appt_id=selected_appt["id"],
            master=selected_appt["master"],
            duration=selected_appt["duration"]
        )
        await EditStates.editing_date.set()
        await message.answer(f"Редагуємо запис:\n{format_appointment(selected_appt)}\n\nОберіть нову дату:", reply_markup=date_kb())
    except ValueError:
        data = await state.get_data()
        user_appts = data.get("user_appointments", [])
        await message.answer(f"Введіть число від 1 до {len(user_appts)}:", reply_markup=numbers_kb(len(user_appts)))

@dp.message_handler(state=EditStates.editing_date)
async def edit_date(message: types.Message, state: FSMContext):
    if message.text in ["⬅️ Назад", "⬅️ Головне меню"]:
        await state.finish()
        await message.answer("Редагування скасовано.", reply_markup=main_menu_kb())
        return
    try:
        chosen_date = datetime.strptime(message.text, "%d.%m.%Y").date()
    except:
        await message.answer("Невірний формат дати. Оберіть зі списку:", reply_markup=date_kb())
        return
    if chosen_date < date.today():
        await message.answer("Не можна обрати минулу дату. Оберіть іншу:", reply_markup=date_kb())
        return
    data = await state.get_data()
    available = get_available_slots(
        data.get("master"), 
        data.get("duration"), 
        chosen_date,
        exclude_appt_id=data.get("edit_appt_id")
    )
    if not available:
        await message.answer("На цю дату немає вільних слотів. Оберіть іншу:", reply_markup=date_kb())
        return
    await state.update_data(new_date=chosen_date.strftime("%Y-%m-%d"))
    await EditStates.choosing_time_for_edit.set()
    await message.answer("Оберіть новий час:", reply_markup=slots_kb(available))

@dp.message_handler(state=EditStates.choosing_time_for_edit)
async def edit_time(message: types.Message, state: FSMContext):
    if message.text in ["⬅️ Назад", "⬅️ Головне меню"]:
        if message.text == "⬅️ Головне меню":
            await state.finish()
            await message.answer("Редагування скасовано.", reply_markup=main_menu_kb())
            return
        else:
            await EditStates.editing_date.set()
            await message.answer("Оберіть дату:", reply_markup=date_kb())
            return
    data = await state.get_data()
    chosen_date = datetime.strptime(data.get("new_date"), "%Y-%m-%d").date()
    available = get_available_slots(
        data.get("master"), 
        data.get("duration"), 
        chosen_date,
        exclude_appt_id=data.get("edit_appt_id")
    )
    if message.text not in available:
        await message.answer("Оберіть час зі списку:", reply_markup=slots_kb(available))
        return
    appts = load_appointments()
    for a in appts:
        if a["id"] == data.get("edit_appt_id"):
            a["date"] = data.get("new_date")
            a["time"] = message.text
            break
    save_appointments(appts)
    await state.finish()
    await message.answer("✅ Запис успішно оновлено!", reply_markup=main_menu_kb())

# ---------- DELETE LOGIC ----------
@dp.message_handler(lambda m: m.text == "❌ Видалити запис", state='*')
async def start_delete(message: types.Message, state: FSMContext):
    appts = load_appointments()
    user_appts = [a for a in appts if a.get("user_id") == message.from_user.id]
    if not user_appts:
        await message.answer("У вас немає записів для видалення.", reply_markup=main_menu_kb())
        return
    text = "🧾 Ваші записи:\n\n"
    for i, a in enumerate(user_appts, 1):
        text += format_appointment(a, show_number=True, number=i) + "\n\n"
    text += "\nВведіть номер запису, який хочете видалити:"
    await DeleteStates.selecting_appointment.set()
    await state.update_data(user_appointments=user_appts)
    await message.answer(text, reply_markup=numbers_kb(len(user_appts)))

@dp.message_handler(state=DeleteStates.selecting_appointment)
async def select_delete_appointment(message: types.Message, state: FSMContext):
    if message.text in ["⬅️ Назад", "⬅️ Головне меню"]:
        await state.finish()
        await message.answer("Видалення скасовано.", reply_markup=main_menu_kb())
        return
    try:
        num = int(message.text)
        data = await state.get_data()
        user_appts = data.get("user_appointments", [])
        if num < 1 or num > len(user_appts):
            await message.answer(f"Введіть число від 1 до {len(user_appts)}:", reply_markup=numbers_kb(len(user_appts)))
            return
        selected_appt = user_appts[num - 1]
        appts = load_appointments()
        new_appts = [a for a in appts if a["id"] != selected_appt["id"]]
        save_appointments(new_appts)
        await state.finish()
        await message.answer("✅ Запис успішно видалено!", reply_markup=main_menu_kb())
    except ValueError:
        data = await state.get_data()
        user_appts = data.get("user_appointments", [])
        await message.answer(f"Введіть число від 1 до {len(user_appts)}:", reply_markup=numbers_kb(len(user_appts)))

# ---------- BOOKING LOGIC ----------
@dp.message_handler(lambda m: m.text == "📅 Запис", state='*')
async def start_booking(message: types.Message, state: FSMContext):
    await state.finish()
    await BookingStates.entering_name.set()
    await message.answer("Введіть, будь ласка, ваше ім'я:", reply_markup=types.ReplyKeyboardRemove())

PHONE_RE = re.compile(r'^\+?\d{7,15}$')

@dp.message_handler(state=BookingStates.entering_name)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if name in ["⬅️ Назад", "⬅️ Головне меню"]:
        await state.finish()
        await message.answer("Запис скасовано.", reply_markup=main_menu_kb())
        return
    await state.update_data(user_name=name, user_id=message.from_user.id)
    await BookingStates.next()
    await message.answer("Введіть номер телефону (наприклад +380501234567):")

@dp.message_handler(state=BookingStates.entering_phone)
async def process_phone(message: types.Message, state: FSMContext):
    if message.text in ["⬅️ Назад", "⬅️ Головне меню"]:
        if message.text == "⬅️ Головне меню":
            await state.finish()
            await message.answer("Запис скасовано.", reply_markup=main_menu_kb())
            return
        else:
            await BookingStates.entering_name.set()
            await message.answer("Введіть ім'я:")
            return
    phone_raw = re.sub(r'[\s\-\(\)]', '', message.text.strip())
    if not PHONE_RE.match(phone_raw):
        await message.answer("Невірний формат номера. Формат: +380501234567 або 0501234567")
        return
    await state.update_data(phone=phone_raw)
    await BookingStates.next()
    await message.answer("Оберіть майстра:", reply_markup=masters_kb())

@dp.message_handler(state=BookingStates.choosing_master)
async def process_master(message: types.Message, state: FSMContext):
    if message.text in ["⬅️ Назад", "⬅️ Головне меню"]:
        await state.finish()
        await message.answer("Запис скасовано.", reply_markup=main_menu_kb())
        return
    master_key = None
    for key, info in MASTERS.items():
        if info["title"] == message.text:
            master_key = key
            break
    if not master_key:
        await message.answer("Оберіть майстра зі списку:", reply_markup=masters_kb())
        return
    await state.update_data(master=master_key, master_title=MASTERS[master_key]["title"])
    await BookingStates.next()
    await message.answer("Оберіть послугу:", reply_markup=services_kb(master_key))

@dp.message_handler(state=BookingStates.choosing_service)
async def process_service(message: types.Message, state: FSMContext):
    if message.text in ["⬅️ Назад", "⬅️ Головне меню"]:
        if message.text == "⬅️ Головне меню":
            await state.finish()
            await message.answer("Запис скасовано.", reply_markup=main_menu_kb())
            return
        else:
            await BookingStates.choosing_master.set()
            await message.answer("Оберіть майстра:", reply_markup=masters_kb())
            return
    data = await state.get_data()
    master_key = data.get("master")
    service_key = None
    for sk, svc in MASTERS[master_key]["services"].items():
        name, price, dur = svc
        if message.text.startswith(name):
            service_key = sk
            break
    if not service_key:
        await message.answer("Оберіть послугу зі списку:", reply_markup=services_kb(master_key))
        return
    name, price, dur = MASTERS[master_key]["services"][service_key]
    await state.update_data(service=service_key, service_title=name, price=price, duration=dur)
    await BookingStates.next()
    await message.answer("Оберіть дату:", reply_markup=date_kb())

@dp.message_handler(state=BookingStates.choosing_date)
async def process_date(message: types.Message, state: FSMContext):
    if message.text in ["⬅️ Назад", "⬅️ Головне меню"]:
        if message.text == "⬅️ Головне меню":
            await state.finish()
            await message.answer("Запис скасовано.", reply_markup=main_menu_kb())
            return
        else:
            data = await state.get_data()
            await BookingStates.choosing_service.set()
            await message.answer("Оберіть послугу:", reply_markup=services_kb(data.get("master")))
            return
    try:
        chosen_date = datetime.strptime(message.text, "%d.%m.%Y").date()
    except:
        await message.answer("Невірний формат дати. Оберіть зі списку:", reply_markup=date_kb())
        return
    if chosen_date < date.today():
        await message.answer("Не можна обрати минулу дату. Оберіть іншу:", reply_markup=date_kb())
        return
    data = await state.get_data()
    available = get_available_slots(data.get("master"), data.get("duration"), chosen_date)
    if not available:
        await message.answer("На цю дату немає вільних слотів. Оберіть іншу дату:", reply_markup=date_kb())
        return
    await state.update_data(date=chosen_date.strftime("%Y-%m-%d"))
    await BookingStates.next()
    await message.answer("Оберіть час:", reply_markup=slots_kb(available))

@dp.message_handler(state=BookingStates.choosing_time)
async def process_time_final(message: types.Message, state: FSMContext):
    # Navigation check
    if message.text in ["⬅️ Назад", "⬅️ Головне меню"]:
        if message.text == "⬅️ Головне меню":
            await state.finish()
            await message.answer("Запис скасовано.", reply_markup=main_menu_kb())
        else:
            await BookingStates.choosing_date.set()
            await message.answer("Оберіть дату:", reply_markup=date_kb())
        return

    # Validation
    data = await state.get_data()
    try:
        chosen_date = datetime.strptime(data.get("date"), "%Y-%m-%d").date()
    except Exception as e:
        await message.answer("Помилка дати, почніть спочатку.")
        await state.finish()
        return

    available = get_available_slots(data.get("master"), data.get("duration"), chosen_date)
    
    if message.text not in available:
        await message.answer("Цей час вже зайнятий або невірний. Оберіть зі списку:", reply_markup=slots_kb(available))
        return

    # Create record
    new_appt = {
        "id": str(uuid.uuid4()),
        "user_id": message.from_user.id,
        "user_name": data.get("user_name"),
        "phone": data.get("phone"),
        "master": data.get("master"),
        "master_title": data.get("master_title"),
        "service": data.get("service"),
        "service_title": data.get("service_title"),
        "price": data.get("price"),
        "duration": data.get("duration"),
        "date": data.get("date"),
        "time": message.text,
        "reminder_sent": False
    }

    appts = load_appointments()
    appts.append(new_appt)
    save_appointments(appts)
    
    await state.finish()
    
    await message.answer(
        f"✅ ЗАПИС УСПІШНО СТВОРЕНО!\n\n{format_appointment(new_appt)}\n\nЧекаємо на вас! ✨", 
        reply_markup=main_menu_kb()
    )
    
    try:
        await bot.send_message(ADMIN_ID, f"🔔 Новий запис!\n\n{format_appointment(new_appt)}")
    except:
        pass

# Main Loop
if __name__ == '__main__':
    ensure_json_exists()
    print("Bot started...")
    
    loop = asyncio.get_event_loop()
    loop.create_task(send_reminders())
    
    executor.start_polling(dp, skip_updates=True)