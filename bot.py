import asyncio
import os
import psycopg2
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.exceptions import TelegramNetworkError

# Включаем логирование, чтобы видеть ошибки в панели Hugging Face
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================================
# 1. НАСТРОЙКИ, БЕЗОПАСНОСТЬ И ИНИЦИАЛИЗАЦИЯ
# ==========================================
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not TOKEN or not DATABASE_URL:
    raise ValueError("❌ ОШИБКА: BOT_TOKEN или DATABASE_URL не найдены в Secrets!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

MEALS = {
    "breakfast": "🍳 Завтрак",
    "lunch": "🍲 Обед",
    "dinner": "🥗 Ужин",
    "snack": "🍎 Перекус"
}

# ==========================================
# 2. БАЗА ДАННЫХ (Облачная PostgreSQL)
# ==========================================
def init_db():
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS weight_log (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        weight REAL,
                        date TEXT
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS calorie_log (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        meal_type TEXT,
                        calories INTEGER,
                        date TEXT
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        gender TEXT,
                        age INTEGER,
                        height INTEGER
                    )
                ''')
            conn.commit()
            logger.info("База данных успешно инициализирована")
    except Exception as e:
        logger.error(f"Ошибка БД: {e}")

init_db()

# ==========================================
# 3. СОСТОЯНИЯ (FSM)
# ==========================================
class WeightState(StatesGroup):
    waiting_for_weight = State()

class CalorieState(StatesGroup):
    waiting_for_calories = State()

class ProfileState(StatesGroup):
    waiting_for_gender = State()
    waiting_for_age = State()
    waiting_for_height = State()

# ==========================================
# 4. КЛАВИАТУРЫ
# ==========================================
def get_main_keyboard():
    kb = [
        [KeyboardButton(text="⚖️ Внести вес"), KeyboardButton(text="🍔 Внести калории")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="👤 Профиль")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_keyboard():
    kb = [[KeyboardButton(text="❌ Отмена")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_gender_keyboard():
    kb = [
        [InlineKeyboardButton(text="👨 Мужской", callback_data="gender_male"),
         InlineKeyboardButton(text="👩 Женский", callback_data="gender_female")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_meals_keyboard():
    kb = [
        [InlineKeyboardButton(text=MEALS["breakfast"], callback_data="meal_breakfast"),
         InlineKeyboardButton(text=MEALS["lunch"], callback_data="meal_lunch")],
        [InlineKeyboardButton(text=MEALS["dinner"], callback_data="meal_dinner"),
         InlineKeyboardButton(text=MEALS["snack"], callback_data="meal_snack")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_stats_keyboard():
    kb = [
        [InlineKeyboardButton(text="📅 Сегодня (Сводка)", callback_data="stat_today")],
        [InlineKeyboardButton(text="🔥 Калории (за 7 дней)", callback_data="stat_calories")],
        [InlineKeyboardButton(text="⚖️ Вес (за месяц)", callback_data="stat_weight")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ==========================================
# 5. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def calculate_bmr(gender, weight, height, age):
    if gender == 'male':
        return (10 * weight) + (6.25 * height) - (5 * age) + 5
    else:
        return (10 * weight) + (6.25 * height) - (5 * age) - 161

# ==========================================
# 6. ОБРАБОТЧИКИ
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я твой фитнес-помощник. 🏋️‍♂️\n\n"
        "Рекомендую начать с заполнения <b>👤 Профиля</b>, чтобы я мог рассчитать твою суточную норму калорий!",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

@dp.message(F.text == "❌ Отмена")
async def cancel_action(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_keyboard())

@dp.message(F.text == "👤 Профиль")
async def btn_profile(message: types.Message, state: FSMContext):
    await message.answer("Укажи свой пол:", reply_markup=get_gender_keyboard())
    await state.set_state(ProfileState.waiting_for_gender)

@dp.callback_query(ProfileState.waiting_for_gender, F.data.startswith("gender_"))
async def process_gender(callback: types.CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]
    await state.update_data(gender=gender)
    await callback.message.delete()
    await callback.message.answer("Введи свой возраст (полных лет):", reply_markup=get_cancel_keyboard())
    await state.set_state(ProfileState.waiting_for_age)

@dp.message(ProfileState.waiting_for_age)
async def process_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Введи число:")
    await state.update_data(age=int(message.text))
    await message.answer("Введи свой рост в см:")
    await state.set_state(ProfileState.waiting_for_height)

@dp.message(ProfileState.waiting_for_height)
async def process_height(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Введи число:")
    
    height = int(message.text)
    data = await state.get_data()
    
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (user_id, gender, age, height) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET gender=EXCLUDED.gender, age=EXCLUDED.age, height=EXCLUDED.height",
                (message.from_user.id, data['gender'], data['age'], height)
            )
        conn.commit()

    await message.answer("✅ Профиль сохранен!", reply_markup=get_main_keyboard())
    await state.clear()

@dp.message(F.text == "⚖️ Внести вес")
async def btn_add_weight(message: types.Message, state: FSMContext):
    await message.answer("Введи вес (кг):", reply_markup=get_cancel_keyboard())
    await state.set_state(WeightState.waiting_for_weight)

@dp.message(WeightState.waiting_for_weight)
async def process_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(',', '.'))
        today = datetime.now().date().isoformat()
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO weight_log (user_id, weight, date) VALUES (%s, %s, %s)", (message.from_user.id, weight, today))
            conn.commit()
        await message.answer(f"✅ Вес {weight} кг записан.", reply_markup=get_main_keyboard())
        await state.clear()
    except:
        await message.answer("Введите число.")

@dp.message(F.text == "🍔 Внести калории")
async def btn_add_calories(message: types.Message):
    await message.answer("Что едим?", reply_markup=get_meals_keyboard())

@dp.callback_query(F.data.startswith("meal_"))
async def callback_meal_chosen(callback: types.CallbackQuery, state: FSMContext):
    meal_type = callback.data.split("_")[1]
    await state.update_data(meal_type=meal_type)
    await callback.message.edit_text(f"Выбрано: {MEALS[meal_type]}\nСколько калорий?")
    await state.set_state(CalorieState.waiting_for_calories)

@dp.message(CalorieState.waiting_for_calories)
async def process_calories(message: types.Message, state: FSMContext):
    try:
        calories = int(message.text)
        data = await state.get_data()
        today = datetime.now().date().isoformat()
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO calorie_log (user_id, meal_type, calories, date) VALUES (%s, %s, %s, %s)", (message.from_user.id, data['meal_type'], calories, today))
            conn.commit()
        await message.answer("✅ Записано!", reply_markup=get_main_keyboard())
        await state.clear()
    except:
        await message.answer("Введите целое число.")

@dp.message(F.text == "📊 Статистика")
async def btn_statistics(message: types.Message):
    await message.answer("Какой отчет?", reply_markup=get_stats_keyboard())

@dp.callback_query(F.data == "stat_today")
async def callback_stat_today(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    today = datetime.now().date().isoformat()
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT gender, age, height FROM users WHERE user_id = %s", (user_id,))
            u = cursor.fetchone()
            cursor.execute("SELECT weight FROM weight_log WHERE user_id = %s ORDER BY date DESC LIMIT 1", (user_id,))
            w = cursor.fetchone()
            cursor.execute("SELECT SUM(calories) FROM calorie_log WHERE user_id = %s AND date = %s", (user_id, today))
            e = cursor.fetchone()[0] or 0

    res = f"📊 Сводка на сегодня:\n🍽 Съедено: {e} ккал\n"
    if u and w:
        norm = int(calculate_bmr(u[0], w[0], u[2], u[1]))
        res += f"🎯 Норма: ~{norm} ккал\nОсталось: {norm - e} ккал"
    else:
        res += "\n<i>Заполни профиль и внеси вес!</i>"
    await callback.message.edit_text(res, parse_mode="HTML")

@dp.callback_query(F.data == "stat_calories")
async def callback_stat_calories(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    week_ago = (datetime.now() - timedelta(days=7)).date().isoformat()
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT SUM(calories) FROM calorie_log WHERE user_id = %s AND date >= %s", (user_id, week_ago))
            total = cursor.fetchone()[0] or 0
    await callback.message.edit_text(f"🔥 Калории за 7 дней: {total} ккал")

@dp.callback_query(F.data == "stat_weight")
async def callback_stat_weight(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT weight FROM weight_log WHERE user_id = %s ORDER BY date DESC LIMIT 1", (user_id,))
            w = cursor.fetchone()
    if w:
        await callback.message.edit_text(f"⚖️ Текущий вес: {w[0]} кг")
    else:
        await callback.message.edit_text("Данных нет.")

# ==========================================
# 7. ЗАПУСК БОТА (с обработкой сетевых ошибок)
# ==========================================
async def main():
    logger.info("Попытка запуска бота...")
    # Цикл для переподключения при сбоях сети
    while True:
        try:
            await dp.start_polling(bot)
        except TelegramNetworkError as e:
            logger.error(f"Ошибка сети: {e}. Повтор через 5 секунд...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())