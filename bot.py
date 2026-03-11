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
                u = cursor.fetchone()
                cursor.execute("SELECT weight FROM weight_log WHERE user_id = %s ORDER BY date DESC LIMIT 1", (message.from_user.id,))
                w = cursor.fetchone()
                if not u or not w:
                    return await message.answer("❕ Щоб AI зміг розрахувати норму, спочатку заповни профіль та внеси вагу!")
    except Exception as e:
        return await message.answer("Помилка бази даних.")

    await message.answer("👟 Розкажіть про свою фізичну активність.\nСкільки разів на тиждень ви тренуєтесь і який це вид тренувань? (наприклад: _3 рази на тиждень, біг та йога_, або _майже не рухаюсь_)", 
                         reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(AIGoalState.workouts)

@dp.message(AIGoalState.workouts)
async def ai_calc_goal_workouts(message: types.Message, state: FSMContext):
    if message.text == "✖️ Скасувати":
        await state.clear()
        return await message.answer("Скасовано.", reply_markup=get_main_keyboard())
    
    await state.update_data(workouts=message.text)
    await message.answer("🎯 Яка ваша головна мета?\n(наприклад: _хочу схуднути на 5 кг_, _хочу набрати м'язову масу_, або _просто підтримувати форму_)", 
                         reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(AIGoalState.goal_type)

@dp.message(AIGoalState.goal_type)
async def ai_calc_goal_finish(message: types.Message, state: FSMContext):
    if message.text == "✖️ Скасувати":
        await state.clear()
        return await message.answer("Скасовано.", reply_markup=get_main_keyboard())

    data = await state.get_data()
    user_goal = message.text
    workouts = data.get('workouts', 'Не вказано')

    await message.answer("⏳ Аналізую ваші параметри та мету... Зачекай пару секунд.", reply_markup=get_main_keyboard())
    
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT gender, age, height FROM users WHERE user_id = %s", (message.from_user.id,))
                u = cursor.fetchone()
                cursor.execute("SELECT weight FROM weight_log WHERE user_id = %s ORDER BY date DESC LIMIT 1", (message.from_user.id,))
                w = cursor.fetchone()
                
        gender_str = "Чоловік" if u[0] == "male" else "Жінка"
        
        prompt = f"""
        Ти професійний фітнес-тренер та дієтолог.
        Параметри клієнта: {gender_str}, {u[1]} років, зріст {u[2]} см, вага {w[0]} кг.
        Фізична активність: "{workouts}".
        Мета клієнта: "{user_goal}".

        Завдання: розрахуй ідеальну добову норму калорій. Якщо мета передбачає схуднення, обов'язково врахуй безпечний дефіцит калорій. Якщо набір маси - профіцит.
        Відповідь має бути СУВОРО у форматі JSON:
        "explanation" - текстове пояснення твого розрахунку та поради (2-3 речення),
        "recommended_calories" - ціле число (рекомендована норма калорій на день).
        """

        response = await ai_client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={ "type": "json_object" },
            messages=[{"role": "system", "content": prompt}]
        )
        
        # Очищення відповіді від можливих маркдаун-тегів (часта помилка AI)
        content = response.choices[0].message.content
        if content.startswith('```json'):
            content = content.replace('```json', '').replace('```', '').strip()
        elif content.startswith('```'):
            content = content.replace('```', '').strip()
            
        result = json.loads(content)
        
        # Вилучаємо текст і повністю очищаємо його від небезпечних маркдаун-символів
        explanation = result.get("explanation", "Розрахунок завершено.")
        explanation = explanation.replace("*", "").replace("_", "").replace("`", "")
        
        # Безпечне вилучення цифр (якщо ШІ надіслав текст замість числа)
        rec_cal_raw = str(result.get("recommended_calories", 2000))
        digits = re.findall(r'\d+', rec_cal_raw)
        rec_cal = int(digits[0]) if digits else 2000

        text = f"✨ **Висновок AI-Дієтолога:**\n\n{explanation}\n\n🎯 **Рекомендована норма:** {rec_cal} ккал/день."
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✔️ Встановити {rec_cal} ккал як мету", callback_data=f"setaigoal_{rec_cal}")]
        ])
        
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
        await state.clear()
        
    except Exception as e:
        logger.error(f"AI Goal Calc Error: {e}")
        await message.answer(f"✖️ Помилка під час розрахунку. Спробуй написати коротше або інакше.\n\n_Деталі: {e}_", reply_markup=get_cancel_keyboard())

@dp.callback_query(F.data.startswith("setaigoal_"))
async def apply_ai_goal(callback: types.CallbackQuery):
    daily_goal = int(callback.data.split("_")[1])
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE users SET daily_goal = %s WHERE user_id = %s", (daily_goal, callback.from_user.id))
            conn.commit()
        await callback.message.edit_text(callback.message.text + "\n\n✔️ *Мета успішно оновлена!*", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Помилка застосування AI мети: {e}")
        await callback.message.answer("Не вдалося зберегти мету.")

# ==========================================
# 7. ДОДАВАННЯ ЇЖІ (ДИНАМІЧНО: ТЕКСТ АБО ЧИСЛО)
# ==========================================
@dp.message(F.text == "✖️ Скасувати")
async def cancel_action(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Дію скасовано.", reply_markup=get_main_keyboard())

@dp.message(F.text == "📉 Внести вагу")
async def btn_add_weight(message: types.Message, state: FSMContext):
    await message.answer("Введи поточну вагу (кг):", reply_markup=get_cancel_keyboard())
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
        await message.answer(f"✔️ Вага {weight} кг записана.", reply_markup=get_main_keyboard())
        await state.clear()
    except Exception:
        await message.answer("Введіть коректне число.")

@dp.message(F.text == "🥑 Внести Kcal AI")
async def ai_food_start(message: types.Message, state: FSMContext):
    if not ai_client:
        return await message.answer("Функція AI поки недоступна (не налаштовано ключ OPENAI_API_KEY).")
    
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT name FROM users WHERE user_id = %s", (message.from_user.id,))
                if not cursor.fetchone():
                    return await message.answer("❕ Немає доступу. Введіть /start і авторизуйтесь.")
    except Exception as e:
        logger.error(f"Помилка перевірки доступу AI: {e}")
        return await message.answer("Помилка бази даних.")

    await message.answer("Опиши, що ти з'їв (наприклад: _я з'їв 3 яйця і хліб_)\n\n⚡ **АБО** просто напиши число калорій, якщо знаєш його (наприклад: _450_ або _450 ккал_):", 
                         reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(AIState.waiting_for_food_text)

@dp.message(AIState.waiting_for_food_text)
async def ai_food_process(message: types.Message, state: FSMContext):
    if message.text == "✖️ Скасувати":
        await state.clear()
        return await message.answer("Скасовано.", reply_markup=get_main_keyboard())

    text_input = message.text.strip()
    
    # Регулярний вираз перевіряє, чи ввів користувач ТІЛЬКИ число (або число + слово "ккал"/"kcal")
    match_manual = re.fullmatch(r'(\d+)\s*(ккал|kcal|калорій|калорий)?', text_input.lower())
    
    is_manual = bool(match_manual)
    total_calories = 0
    breakdown = ""

    if is_manual:
        # Користувач ввів просто число
        total_calories = int(match_manual.group(1))
        # Одразу відновлюємо головну клавіатуру (інакше вона б пропала)
        await message.answer("⚡ Миттєва обробка...", reply_markup=get_main_keyboard())
    else:
        # Користувач ввів текст їжі - підключаємо OpenAI
        await message.answer("⏳ Аналізую продукти... Зачекай пару секунд.", reply_markup=get_main_keyboard())
        
        prompt = f"""
        Ти професійний дієтолог. Користувач з'їв наступне: "{text_input}".
        Оціни приблизну калорійність кожного продукту та порахуй загальну суму.
        Твоя відповідь має бути СУВОРО у форматі JSON з двома ключами:
        "breakdown" - рядковий опис розрахунку (кожен продукт з нового рядка).
        "total" - тільки ціле число (загальна сума калорій).
        """

        try:
            response = await ai_client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "system", "content": prompt}]
            )
            
            content = response.choices[0].message.content
            if content.startswith('```json'):
                content = content.replace('```json', '').replace('```', '').strip()
            elif content.startswith('```'):
                content = content.replace('```', '').strip()
                
            result = json.loads(content)
            breakdown = result.get("breakdown", "Немає опису")
            breakdown = breakdown.replace("*", "").replace("_", "").replace("`", "")
            
            total_raw = str(result.get("total", 0))
            digits = re.findall(r'\d+', total_raw)
            total_calories = int(digits[0]) if digits else 0
            
        except Exception as e:
            logger.error(f"AI Error: {e}")
            return await message.answer(f"✖️ Ой, не зміг розпізнати їжу. Спробуй написати трохи інакше:\n\n_Деталі: {e}_", reply_markup=get_cancel_keyboard())

    # Спільна логіка для лімітів та збереження (для обох варіантів)
    user_id = message.from_user.id
    today = datetime.now().date().isoformat()
    goal = 0
    eaten_today = 0
    
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT daily_goal FROM users WHERE user_id = %s", (user_id,))
            u = cursor.fetchone()
            if u and u[0]: goal = u[0]
            cursor.execute("SELECT SUM(calories) FROM calorie_log WHERE user_id = %s AND date = %s", (user_id, today))
            e = cursor.fetchone()
            if e and e[0]: eaten_today = e[0]

    status_text = ""
    if goal > 0:
        new_total = eaten_today + total_calories
        if new_total <= goal:
            joke = random.choice(SUCCESS_JOKES)
            status_text = f"\n\n📊 З цією їжею ви **вписуєтесь** у норму! Залишиться: {goal - new_total} ккал.\n_{joke}_"
        else:
            joke = random.choice(FAIL_JOKES)
            status_text = f"\n\n❕ **Овва, перебір!** Ви перевищите денну норму на {new_total - goal} ккал. (Разом за день буде {new_total} з {goal}).\n_{joke}_"

    # Формуємо текст залежно від того, яким шляхом ми сюди прийшли
    if is_manual:
        text = f"⚡ **Швидке введення:**\n\n**Разом:** {total_calories} ккал{status_text}"
    else:
        text = f"🥑 **Аналіз AI:**\n\n{breakdown}\n\n**Разом:** {total_calories} ккал{status_text}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📥 Зберегти {total_calories} ккал", callback_data=f"aisave_{total_calories}")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await state.clear()

@dp.callback_query(F.data.startswith("aisave_"))
async def save_ai_calories(callback: types.CallbackQuery):
    calories = int(callback.data.split("_")[1])
    today = datetime.now().date().isoformat()
    
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO calorie_log (user_id, meal_type, calories, date) VALUES (%s, %s, %s, %s)", 
                               (callback.from_user.id, "ai_food", calories, today))
            conn.commit()
            
        await callback.message.edit_text(callback.message.text + "\n\n✔️ *Успішно збережено в щоденник!*", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Помилка при збереженні AI калорій: {e}")
        await callback.message.answer("Не вдалося зберегти калорії в базу даних.")

# ==========================================
# 8. СТАТИСТИКА
# ==========================================
@dp.message(F.text == "📊 Статистика")
async def btn_statistics(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗓 Сьогодні (Зведення)", callback_data="stat_today")],
        [InlineKeyboardButton(text="🔥 За 7 днів", callback_data="stat_7days")],
        [InlineKeyboardButton(text="🚀 Загальний прогрес", callback_data="stat_overall")],
        [InlineKeyboardButton(text="🧨 Скинути статистику", callback_data="reset_stats")]
    ])
    await message.answer("Обери звіт:", reply_markup=kb)

@dp.callback_query(F.data == "stat_today")
async def callback_stat_today(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    today = datetime.now().date().isoformat()
    
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT daily_goal FROM users WHERE user_id = %s", (user_id,))
                u = cursor.fetchone()
                cursor.execute("SELECT SUM(calories) FROM calorie_log WHERE user_id = %s AND date = %s", (user_id, today))
                e = cursor.fetchone()[0] or 0

        if not u:
            return await callback.message.edit_text("Спочатку пройди реєстрацію в профілі!")

        goal = u[0]
        res = f"📋 **Зведення на сьогодні:**\n🍽 З'їдено: {e} ккал\n🎯 Твоя норма: {goal} ккал\n"
        res += f"🤍 Залишилось: {goal - e} ккал" if goal >= e else f"❕ Перебір: {e - goal} ккал!"
        
        await callback.message.edit_text(res, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Помилка статистики за сьогодні: {e}")
        await callback.message.answer("Помилка при розрахунку статистики.")

@dp.callback_query(F.data == "stat_7days")
async def callback_stat_7days(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    week_ago = (datetime.now() - timedelta(days=7)).date().isoformat()
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT SUM(calories) FROM calorie_log WHERE user_id = %s AND date >= %s", (user_id, week_ago))
                total = cursor.fetchone()[0] or 0
        await callback.message.edit_text(f"🔥 Калорії за останні 7 днів: **{total} ккал**", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Помилка статистики за 7 днів: {e}")
        await callback.message.answer("Помилка при розрахунку статистики.")

@dp.callback_query(F.data == "stat_overall")
async def callback_stat_overall(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT date, SUM(calories) FROM calorie_log WHERE user_id = %s GROUP BY date", (user_id,))
                daily_totals = cursor.fetchall()
                
                cursor.execute("SELECT weight FROM weight_log WHERE user_id = %s ORDER BY id ASC LIMIT 1", (user_id,))
                first_w = cursor.fetchone()
                cursor.execute("SELECT weight FROM weight_log WHERE user_id = %s ORDER BY id DESC LIMIT 1", (user_id,))
                last_w = cursor.fetchone()

        if not daily_totals:
            cal_text = "🍽 Немає записів калорій. Почніть додавати прийоми їжі!"
        else:
            max_day = max(daily_totals, key=lambda x: x[1])
            min_day = min(daily_totals, key=lambda x: x[1])
            
            cal_text = (
                f"🔥 **Рекорди калорій:**\n"
                f"📈 Найбільше за день: {max_day[1]} ккал ({max_day[0]})\n"
                f"📉 Найменше за день: {min_day[1]} ккал ({min_day[0]})"
            )

        weight_text = "📉 **Прогрес ваги:** Немає даних."
        if first_w and last_w:
            w_start = first_w[0]
            w_now = last_w[0]
            diff = w_now - w_start
            
            if diff > 0:
                w_status = f"🔺 Ви набрали: +{diff:.1f} кг"
            elif diff < 0:
                w_status = f"🔻 Ви скинули: {diff:.1f} кг 🎉"
            else:
                w_status = "🫧 Вага без змін."
            
            weight_text = (
                f"📉 **Прогрес ваги:**\n"
                f"Початкова вага: {w_start} кг\n"
                f"Поточна вага: {w_now} кг\n"
                f"**{w_status}**"
            )

        res = f"🚀 **Загальна статистика:**\n\n{cal_text}\n\n{weight_text}"
        await callback.message.edit_text(res, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Помилка загальної статистики: {e}")
        await callback.message.answer("Помилка при розрахунку загальної статистики.")

@dp.callback_query(F.data == "reset_stats")
async def callback_reset_stats_ask(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✔️ Так, видалити все", callback_data="confirm_reset")],
        [InlineKeyboardButton(text="✖️ Ні, скасувати", callback_data="cancel_reset")]
    ])
    await callback.message.edit_text(
        "❕ **УВАГА!**\nВи дійсно хочете видалити всю історію (з'їдені калорії та логи ваги)?\n\n*Ваш профіль (зріст, вік, добова мета) залишиться.*", 
        reply_markup=kb, parse_mode="Markdown"
    )

@dp.callback_query(F.data == "confirm_reset")
async def callback_confirm_reset(callback: types.CallbackQuery):
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM calorie_log WHERE user_id = %s", (callback.from_user.id,))
                cursor.execute("DELETE FROM weight_log WHERE user_id = %s", (callback.from_user.id,))
            conn.commit()
        await callback.message.edit_text("✔️ Вся ваша статистика була успішно очищена!")
    except Exception as e:
        logger.error(f"Помилка при скиданні статистики: {e}")
        await callback.message.edit_text("✖️ Виникла помилка при видаленні даних.")

@dp.callback_query(F.data == "cancel_reset")
async def callback_cancel_reset(callback: types.CallbackQuery):
    await callback.message.edit_text("✔️ Дію скасовано. Ваша статистика в безпеці!")

# ==========================================
# 9. ЗАПУСК БОТА
# ==========================================
async def main():
    init_db()
    logger.info("Бот запущений і готовий до роботи...")
    while True:
        try:
            await dp.start_polling(bot)
        except TelegramNetworkError as e:
            logger.error(f"Помилка мережі: {e}. Повтор через 5 секунд...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Критична помилка: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())