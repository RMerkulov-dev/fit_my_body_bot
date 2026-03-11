import asyncio
import os
import psycopg2
import logging
import json
import random
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.exceptions import TelegramNetworkError

from openai import AsyncOpenAI

# Логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BOT_PASSWORD = os.getenv("BOT_PASSWORD") # Секретний пароль для доступу

if not TOKEN or not DATABASE_URL:
    logger.error("ПОМИЛКА: Перевірте змінні BOT_TOKEN та DATABASE_URL!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Ініціалізація клієнта OpenAI
ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Жарти для різноманітності
SUCCESS_JOKES = [
    "Ваш прес передає вам подяку! 🕶",
    "Можна ще з'їсти щось смачненьке, але без фанатизму! 🧁",
    "Ідеально! Ви сьогодні просто фітнес-гуру. 🧘‍♀️",
    "Совість чиста, калорії в нормі! ✨",
    "Так тримати! Термінатор би вами пишався. 🦾"
]

FAIL_JOKES = [
    "Ваші джинси дивляться на вас із засудженням... 🫣",
    "Будемо відпрацьовувати в залі, чи просто сховаємо ваги? 👟",
    "Хтось вночі крав їжу з холодильника? 🐾",
    "Ех, а так добре день починався... Ну нічого, завтра на дієту! 🫠",
    "Ваша фігура каже 'Ой-йой', а шлунок каже 'Дякую, бос!' 🍟"
]

# ==========================================
# 1. БАЗА ДАНИХ (з автоматичною міграцією)
# ==========================================
def init_db():
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        with conn.cursor() as cursor:
            # Базові таблиці
            cursor.execute('CREATE TABLE IF NOT EXISTS weight_log (id SERIAL PRIMARY KEY, user_id BIGINT, weight REAL, date TEXT)')
            cursor.execute('CREATE TABLE IF NOT EXISTS calorie_log (id SERIAL PRIMARY KEY, user_id BIGINT, meal_type TEXT, calories INTEGER, date TEXT)')
            cursor.execute('CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, gender TEXT, age INTEGER, height INTEGER)')
            
            # Авто-оновлення старої таблиці users
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='name';")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE users ADD COLUMN name TEXT;")
                
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='daily_goal';")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE users ADD COLUMN daily_goal INTEGER;")
                
        logger.info("БД підключена і оновлена успішно.")
    except Exception as e:
        logger.error(f"Помилка БД: {e}")
    finally:
        if conn:
            conn.close()

# ==========================================
# 2. СТАНИ (FSM)
# ==========================================
class AuthState(StatesGroup):
    waiting_for_password = State()

class RegState(StatesGroup):
    name = State()
    gender = State()
    age = State()
    height = State()
    weight = State()
    goal = State()

class GoalState(StatesGroup):
    waiting_for_goal = State()

class WeightState(StatesGroup):
    waiting_for_weight = State()

class AIState(StatesGroup):
    waiting_for_food_text = State()

class AIGoalState(StatesGroup):
    workouts = State()
    goal_type = State()

# ==========================================
# 3. КЛАВІАТУРИ
# ==========================================
def get_main_keyboard():
    kb = [
        [KeyboardButton(text="🥑 Внести Kcal AI"), KeyboardButton(text="✨ Мета Kcal AI")],
        [KeyboardButton(text="📉 Внести вагу"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🪪 Профіль")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✖️ Скасувати")]], resize_keyboard=True)

def get_gender_keyboard(prefix="reg"):
    kb = [
        [InlineKeyboardButton(text="🧍‍♂️ Чоловіча", callback_data=f"{prefix}_gender_male"),
         InlineKeyboardButton(text="🧍‍♀️ Жіноча", callback_data=f"{prefix}_gender_female")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_goal_periods_keyboard():
    kb = [
        [InlineKeyboardButton(text="На 1 день", callback_data="setgoal_1"),
         InlineKeyboardButton(text="На 1 тиждень", callback_data="setgoal_7")],
        [InlineKeyboardButton(text="На 1 місяць", callback_data="setgoal_30"),
         InlineKeyboardButton(text="На 3 місяці", callback_data="setgoal_90")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ==========================================
# 4. РЕЄСТРАЦІЯ
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT name FROM users WHERE user_id = %s", (message.from_user.id,))
                user = cursor.fetchone()
                
        if user and user[0]:
            await message.answer(f"З поверненням, {user[0]}! 🦾", reply_markup=get_main_keyboard())
        else:
            if BOT_PASSWORD:
                await message.answer("🔒 Бот приватний. Будь ласка, введіть пароль для доступу:", reply_markup=ReplyKeyboardRemove())
                await state.set_state(AuthState.waiting_for_password)
            else:
                await message.answer("Привіт! Давай налаштуємо твій профіль. Як тебе звати?", reply_markup=ReplyKeyboardRemove())
                await state.set_state(RegState.name)
    except Exception as e:
        logger.error(f"Помилка при завантаженні профілю (cmd_start): {e}")
        await message.answer("⏳ Оновлюю базу даних... Натисни /start ще раз через 5 секунд!")

@dp.message(AuthState.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    if message.text == BOT_PASSWORD:
        await message.answer("✔️ Доступ дозволено!\n\nПривіт! Давай налаштуємо твій профіль. Як тебе звати?")
        await state.set_state(RegState.name)
    else:
        await message.answer("✖️ Невірний пароль. Спробуйте ще раз.")

@dp.message(RegState.name)
async def reg_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(f"Приємно познайомитися, {message.text}! Вкажи свою стать:", reply_markup=get_gender_keyboard())
    await state.set_state(RegState.gender)

@dp.callback_query(RegState.gender, F.data.startswith("reg_gender_"))
async def reg_gender(callback: types.CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[-1]
    await state.update_data(gender=gender)
    await callback.message.edit_text("Скільки тобі повних років?")
    await state.set_state(RegState.age)

@dp.message(RegState.age)
async def reg_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введи число:")
    await state.update_data(age=int(message.text))
    await message.answer("Твій зріст (у см)?")
    await state.set_state(RegState.height)

@dp.message(RegState.height)
async def reg_height(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введи число:")
    await state.update_data(height=int(message.text))
    await message.answer("Твоя поточна вага (у кг)?")
    await state.set_state(RegState.weight)

@dp.message(RegState.weight)
async def reg_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(',', '.'))
        await state.update_data(weight=weight)
        await message.answer("І останнє: яка у тебе мета по калоріях **на день**?\n*(Напиши число, наприклад 2000, або напиши '0', а потім розрахуєш через AI)*", parse_mode="Markdown")
        await state.set_state(RegState.goal)
    except ValueError:
        await message.answer("Введи коректне число (наприклад, 70.5):")

@dp.message(RegState.goal)
async def reg_goal(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введи число:")
    data = await state.get_data()
    daily_goal = int(message.text)
    user_id = message.from_user.id
    today = datetime.now().date().isoformat()
    
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO users (user_id, name, gender, age, height, daily_goal)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET 
                    name=EXCLUDED.name, gender=EXCLUDED.gender, age=EXCLUDED.age, 
                    height=EXCLUDED.height, daily_goal=EXCLUDED.daily_goal
                """, (user_id, data['name'], data['gender'], data['age'], data['height'], daily_goal))
                
                cursor.execute("INSERT INTO weight_log (user_id, weight, date) VALUES (%s, %s, %s)", 
                               (user_id, data['weight'], today))
            conn.commit()
            
        await message.answer("✔️ Реєстрація завершена! Тепер ти можеш повноцінно користуватися ботом.", reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Помилка збереження БД при реєстрації: {e}")
        await message.answer("Сталася помилка бази даних. Спробуй натиснути /start і повторити.")
    await state.clear()

# ==========================================
# 5. ПРОФІЛЬ ТА РУЧНА ЗМІНА ЦІЛІ
# ==========================================
@dp.message(F.text == "🪪 Профіль")
async def show_profile(message: types.Message):
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT name, gender, age, height, daily_goal FROM users WHERE user_id = %s", (message.from_user.id,))
                u = cursor.fetchone()
                cursor.execute("SELECT weight FROM weight_log WHERE user_id = %s ORDER BY date DESC LIMIT 1", (message.from_user.id,))
                w = cursor.fetchone()

        if not u:
            return await message.answer("Спочатку натисни /start і пройди реєстрацію!")

        weight_str = f"{w[0]} кг" if w else "Немає даних"
        gender_str = "Чоловіча 🧍‍♂️" if u[1] == "male" else "Жіноча 🧍‍♀️"
        
        text = f"🪪 **Профіль: {u[0]}**\n" \
               f"Стать: {gender_str}\nВік: {u[2]} років\nЗріст: {u[3]} см\nПоточна вага: {weight_str}\n" \
               f"🎯 **Мета на день:** {u[4]} ккал"
               
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ Змінити мету вручну", callback_data="change_goal")]])
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Помилка профілю: {e}")
        await message.answer("Не вдалося завантажити дані. Повтори спробу пізніше.")

@dp.callback_query(F.data == "change_goal")
async def change_goal_start(callback: types.CallbackQuery):
    await callback.message.edit_text("На який період ти хочеш задати нову мету?", reply_markup=get_goal_periods_keyboard())

@dp.callback_query(F.data.startswith("setgoal_"))
async def process_goal_period(callback: types.CallbackQuery, state: FSMContext):
    days = int(callback.data.split("_")[1])
    await state.update_data(goal_days=days)
    
    period_name = {1: "день", 7: "тиждень", 30: "місяць", 90: "3 місяці"}[days]
    await callback.message.edit_text(f"Введи бажану кількість калорій на **{period_name}**:\n*(Я сам розрахую з цього твою добову норму)*", parse_mode="Markdown")
    await state.set_state(GoalState.waiting_for_goal)

@dp.message(GoalState.waiting_for_goal)
async def save_new_goal(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Будь ласка, введи ціле число.")
    
    data = await state.get_data()
    days = data.get('goal_days', 1)
    total_goal = int(message.text)
    daily_goal = total_goal // days
    
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE users SET daily_goal = %s WHERE user_id = %s", (daily_goal, message.from_user.id))
            conn.commit()

        period_name = {1: "день", 7: "тиждень", 30: "місяць", 90: "3 місяці"}[days]
        await message.answer(f"✔️ Мета на {period_name} ({total_goal} ккал) збережена!\nТвоя нова норма: **{daily_goal} ккал на день**.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Помилка збереження мети: {e}")
        await message.answer("Сталася помилка. Спробуй пізніше.")
    await state.clear()

# ==========================================
# 6. РОЗУМНИЙ РОЗРАХУНОК ЦІЛІ З AI (НОВЕ)
# ==========================================
@dp.message(F.text == "✨ Мета Kcal AI")
async def ai_calc_goal_start(message: types.Message, state: FSMContext):
    if not ai_client:
        return await message.answer("Функція AI поки недоступна (не налаштовано ключ OPENAI_API_KEY).")
    
    # Перевіряємо чи є у користувача профіль
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT gender, age, height FROM users WHERE user_id = %s", (message.from_user.id,))