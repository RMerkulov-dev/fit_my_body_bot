import asyncio
import os
import psycopg2
import logging
import json
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

MEALS = {
    "breakfast": "🍳 Сніданок",
    "lunch": "🍲 Обід",
    "dinner": "🥗 Вечеря",
    "snack": "🍎 Перекус",
    "ai_food": "🤖 AI"
}

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

class CalorieState(StatesGroup):
    waiting_for_calories = State()

class AIState(StatesGroup):
    waiting_for_food_text = State()

# ==========================================
# 3. КЛАВІАТУРИ
# ==========================================
def get_main_keyboard():
    kb = [
        [KeyboardButton(text="🤖 AI")],
        [KeyboardButton(text="⚖️ Внести вагу"), KeyboardButton(text="🍔 Внести калорії")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="👤 Профіль")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)

def get_gender_keyboard(prefix="reg"):
    kb = [
        [InlineKeyboardButton(text="👨 Чоловіча", callback_data=f"{prefix}_gender_male"),
         InlineKeyboardButton(text="👩 Жіноча", callback_data=f"{prefix}_gender_female")]
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
    await state.clear() # Скидання завислих станів
    try:
        # Перевіряємо, чи є користувач у БД
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT name FROM users WHERE user_id = %s", (message.from_user.id,))
                user = cursor.fetchone()
                
        if user and user[0]:
            await message.answer(f"З поверненням, {user[0]}! 🏋️‍♂️", reply_markup=get_main_keyboard())
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
        await message.answer("✅ Доступ дозволено!\n\nПривіт! Давай налаштуємо твій профіль. Як тебе звати?")
        await state.set_state(RegState.name)
    else:
        await message.answer("❌ Невірний пароль. Спробуйте ще раз.")

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
        await message.answer("І останнє: яка у тебе мета по калоріях **на день**?\n*(Напиши число, наприклад 2000)*", parse_mode="Markdown")
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
                # Зберігаємо профіль
                cursor.execute("""
                    INSERT INTO users (user_id, name, gender, age, height, daily_goal)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET 
                    name=EXCLUDED.name, gender=EXCLUDED.gender, age=EXCLUDED.age, 
                    height=EXCLUDED.height, daily_goal=EXCLUDED.daily_goal
                """, (user_id, data['name'], data['gender'], data['age'], data['height'], daily_goal))
                
                # Зберігаємо першу вагу
                cursor.execute("INSERT INTO weight_log (user_id, weight, date) VALUES (%s, %s, %s)", 
                               (user_id, data['weight'], today))
            conn.commit()
            
        await message.answer("✅ Реєстрація завершена! Тепер ти можеш повноцінно користуватися ботом.", reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Помилка збереження БД при реєстрації: {e}")
        await message.answer("Сталася помилка бази даних. Спробуй натиснути /start і повторити.")
    await state.clear()

# ==========================================
# 5. ЗМІНА ЦІЛЕЙ ТА ПРОФІЛЬ
# ==========================================
@dp.message(F.text == "👤 Профіль")
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
        gender_str = "Чоловіча 👨" if u[1] == "male" else "Жіноча 👩"
        
        text = f"👤 **Профіль: {u[0]}**\n" \
               f"Стать: {gender_str}\nВік: {u[2]} років\nЗріст: {u[3]} см\nПоточна вага: {weight_str}\n" \
               f"🎯 **Мета на день:** {u[4]} ккал"
               
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 Змінити мету калорій", callback_data="change_goal")]])
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
    
    # Обчислюємо денну мету
    daily_goal = total_goal // days
    
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE users SET daily_goal = %s WHERE user_id = %s", (daily_goal, message.from_user.id))
            conn.commit()

        period_name = {1: "день", 7: "тиждень", 30: "місяць", 90: "3 місяці"}[days]
        await message.answer(f"✅ Мета на {period_name} ({total_goal} ккал) збережена!\nТвоя нова норма: **{daily_goal} ккал на день**.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Помилка збереження мети: {e}")
        await message.answer("Сталася помилка. Спробуй пізніше.")
    await state.clear()

# ==========================================
# 6. НЕЙРОМЕРЕЖА (AI)
# ==========================================
@dp.message(F.text == "🤖 AI")
async def ai_food_start(message: types.Message, state: FSMContext):
    if not ai_client:
        return await message.answer("Функція AI поки недоступна (не налаштовано ключ OPENAI_API_KEY).")
    
    # Жорсткий захист: подвійна перевірка реєстрації (захист API)
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT name FROM users WHERE user_id = %s", (message.from_user.id,))
                if not cursor.fetchone():
                    return await message.answer("⛔ Немає доступу. Введіть /start і авторизуйтесь.")
    except Exception as e:
        logger.error(f"Помилка перевірки доступу AI: {e}")
        return await message.answer("Помилка бази даних.")

    await message.answer("Опиши, що ти з'їв у вільній формі (наприклад: *я з'їв 3 яйця, 100г хліба і 30г сиру*):", 
                         reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(AIState.waiting_for_food_text)

@dp.message(AIState.waiting_for_food_text)
async def ai_food_process(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        return await message.answer("Скасовано.", reply_markup=get_main_keyboard())

    # Відновлюємо головне меню одразу під час аналізу
    await message.answer("⏳ Аналізую продукти... Зачекай пару секунд.", reply_markup=get_main_keyboard())
    
    prompt = f"""
    Ти професійний дієтолог. Користувач з'їв наступне: "{message.text}".
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
        
        result = json.loads(response.choices[0].message.content)
        breakdown = result.get("breakdown", "Немає опису")
        total_calories = int(result.get("total", 0))

        text = f"🤖 **Аналіз AI:**\n\n{breakdown}\n\n**Разом:** {total_calories} ккал"
        
        # Кнопка для збереження
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💾 Зберегти {total_calories} ккал", callback_data=f"aisave_{total_calories}")]
        ])
        
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"AI Error: {e}")
        await message.answer("Ой, не зміг розпізнати їжу. Спробуй написати трохи інакше.")
    
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
            
        await callback.message.edit_text(callback.message.text + "\n\n✅ *Успішно збережено в щоденник!*", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Помилка при збереженні AI калорій: {e}")
        await callback.message.answer("Не вдалося зберегти калорії в базу даних.")

# ==========================================
# 7. РУЧНЕ ВВЕДЕННЯ ВАГИ ТА КАЛОРІЙ
# ==========================================
@dp.message(F.text == "❌ Скасувати")
async def cancel_action(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Дію скасовано.", reply_markup=get_main_keyboard())

@dp.message(F.text == "⚖️ Внести вагу")
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
        await message.answer(f"✅ Вага {weight} кг записана.", reply_markup=get_main_keyboard())
        await state.clear()
    except Exception:
        await message.answer("Введіть коректне число.")

@dp.message(F.text == "🍔 Внести калорії")
async def btn_add_calories(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=MEALS["breakfast"], callback_data="meal_breakfast"),
         InlineKeyboardButton(text=MEALS["lunch"], callback_data="meal_lunch")],
        [InlineKeyboardButton(text=MEALS["dinner"], callback_data="meal_dinner"),
         InlineKeyboardButton(text=MEALS["snack"], callback_data="meal_snack")]
    ])
    await message.answer("Який це прийом їжі?", reply_markup=kb)

@dp.callback_query(F.data.startswith("meal_"))
async def callback_meal_chosen(callback: types.CallbackQuery, state: FSMContext):
    meal_type = callback.data.split("_")[1]
    await state.update_data(meal_type=meal_type)
    await callback.message.edit_text(f"Вибрано: {MEALS[meal_type]}\nСкільки калорій?")
    await state.set_state(CalorieState.waiting_for_calories)

@dp.message(CalorieState.waiting_for_calories)
async def process_calories(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введіть ціле число.")
    
    calories = int(message.text)
    data = await state.get_data()
    today = datetime.now().date().isoformat()
    
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO calorie_log (user_id, meal_type, calories, date) VALUES (%s, %s, %s, %s)", 
                               (message.from_user.id, data['meal_type'], calories, today))
            conn.commit()
            
        await message.answer("✅ Записано!", reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Помилка ручного додавання калорій: {e}")
        await message.answer("Не вдалося записати в базу даних.")
    await state.clear()

# ==========================================
# 8. СТАТИСТИКА
# ==========================================
@dp.message(F.text == "📊 Статистика")
async def btn_statistics(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Сьогодні (Зведення)", callback_data="stat_today")],
        [InlineKeyboardButton(text="🔥 За 7 днів", callback_data="stat_7days")],
        [InlineKeyboardButton(text="🗑 Скинути статистику", callback_data="reset_stats")]
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
        res = f"📊 **Зведення на сьогодні:**\n🍽 З'їдено: {e} ккал\n🎯 Твоя норма: {goal} ккал\n"
        res += f"📉 Залишилось: {goal - e} ккал" if goal >= e else f"⚠️ Перебір: {e - goal} ккал!"
        
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

@dp.callback_query(F.data == "reset_stats")
async def callback_reset_stats_ask(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Так, видалити все", callback_data="confirm_reset")],
        [InlineKeyboardButton(text="❌ Ні, скасувати", callback_data="cancel_reset")]
    ])
    await callback.message.edit_text(
        "⚠️ **УВАГА!**\nВи дійсно хочете видалити всю історію (з'їдені калорії та логи ваги)?\n\n*Ваш профіль (зріст, вік, добова мета) залишиться.*", 
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
        await callback.message.edit_text("✅ Вся ваша статистика була успішно очищена!")
    except Exception as e:
        logger.error(f"Помилка при скиданні статистики: {e}")
        await callback.message.edit_text("❌ Виникла помилка при видаленні даних.")

@dp.callback_query(F.data == "cancel_reset")
async def callback_cancel_reset(callback: types.CallbackQuery):
    await callback.message.edit_text("✅ Дію скасовано. Ваша статистика в безпеці!")

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