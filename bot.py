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
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramNetworkError

from openai import AsyncOpenAI

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TOKEN or not DATABASE_URL:
    logger.error("ОШИБКА: Проверьте переменные BOT_TOKEN и DATABASE_URL!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Инициализация OpenAI клиента
ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

MEALS = {
    "breakfast": "🍳 Завтрак",
    "lunch": "🍲 Обед",
    "dinner": "🥗 Ужин",
    "snack": "🍎 Перекус",
    "ai_food": "🤖 Нейросеть"
}

# ==========================================
# 1. БАЗА ДАННЫХ (с автоматической миграцией)
# ==========================================
def init_db():
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                # Базовые таблицы
                cursor.execute('CREATE TABLE IF NOT EXISTS weight_log (id SERIAL PRIMARY KEY, user_id BIGINT, weight REAL, date TEXT)')
                cursor.execute('CREATE TABLE IF NOT EXISTS calorie_log (id SERIAL PRIMARY KEY, user_id BIGINT, meal_type TEXT, calories INTEGER, date TEXT)')
                cursor.execute('CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, gender TEXT, age INTEGER, height INTEGER)')
                
                # Авто-обновление старой таблицы users (добавление имени и цели)
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='name';")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE users ADD COLUMN name TEXT;")
                    
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='daily_goal';")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE users ADD COLUMN daily_goal INTEGER;")
                    
            conn.commit()
            logger.info("БД подключена и обновлена успешно.")
    except Exception as e:
        logger.error(f"Ошибка БД: {e}")

# ==========================================
# 2. СОСТОЯНИЯ (FSM)
# ==========================================
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
# 3. КЛАВИАТУРЫ
# ==========================================
def get_main_keyboard():
    kb = [
        [KeyboardButton(text="🤖 Нейросеть (AI)")],
        [KeyboardButton(text="⚖️ Внести вес"), KeyboardButton(text="🍔 Внести калории")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="👤 Профиль")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)

def get_gender_keyboard(prefix="reg"):
    kb = [
        [InlineKeyboardButton(text="👨 Мужской", callback_data=f"{prefix}_gender_male"),
         InlineKeyboardButton(text="👩 Женский", callback_data=f"{prefix}_gender_female")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_goal_periods_keyboard():
    kb = [
        [InlineKeyboardButton(text="На 1 день", callback_data="setgoal_1"),
         InlineKeyboardButton(text="На 1 неделю", callback_data="setgoal_7")],
        [InlineKeyboardButton(text="На 1 месяц", callback_data="setgoal_30"),
         InlineKeyboardButton(text="На 3 месяца", callback_data="setgoal_90")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ==========================================
# 4. РЕГИСТРАЦИЯ
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    # Проверяем, есть ли пользователь в БД
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT name FROM users WHERE user_id = %s", (message.from_user.id,))
            user = cursor.fetchone()
            
    if user and user[0]:
        await message.answer(f"С возвращением, {user[0]}! 🏋️‍♂️", reply_markup=get_main_keyboard())
    else:
        await message.answer("Привет! Давай настроим твой профиль. Как тебя зовут?", reply_markup=ReplyKeyboardRemove())
        await state.set_state(RegState.name)

@dp.message(RegState.name)
async def reg_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(f"Приятно познакомиться, {message.text}! Укажи свой пол:", reply_markup=get_gender_keyboard())
    await state.set_state(RegState.gender)

@dp.callback_query(RegState.gender, F.data.startswith("reg_gender_"))
async def reg_gender(callback: types.CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[-1]
    await state.update_data(gender=gender)
    await callback.message.edit_text("Сколько тебе полных лет?")
    await state.set_state(RegState.age)

@dp.message(RegState.age)
async def reg_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введи число:")
    await state.update_data(age=int(message.text))
    await message.answer("Твой рост (в см)?")
    await state.set_state(RegState.height)

@dp.message(RegState.height)
async def reg_height(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введи число:")
    await state.update_data(height=int(message.text))
    await message.answer("Твой текущий вес (в кг)?")
    await state.set_state(RegState.weight)

@dp.message(RegState.weight)
async def reg_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(',', '.'))
        await state.update_data(weight=weight)
        await message.answer("И последнее: какая у тебя цель по калориям **на день**?\n*(Напиши число, например 2000)*", parse_mode="Markdown")
        await state.set_state(RegState.goal)
    except ValueError:
        await message.answer("Введи корректное число (например, 70.5):")

@dp.message(RegState.goal)
async def reg_goal(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введи число:")
    data = await state.get_data()
    daily_goal = int(message.text)
    user_id = message.from_user.id
    today = datetime.now().date().isoformat()
    
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            # Сохраняем профиль
            cursor.execute("""
                INSERT INTO users (user_id, name, gender, age, height, daily_goal)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET 
                name=EXCLUDED.name, gender=EXCLUDED.gender, age=EXCLUDED.age, 
                height=EXCLUDED.height, daily_goal=EXCLUDED.daily_goal
            """, (user_id, data['name'], data['gender'], data['age'], data['height'], daily_goal))
            
            # Сохраняем первый вес
            cursor.execute("INSERT INTO weight_log (user_id, weight, date) VALUES (%s, %s, %s)", 
                           (user_id, data['weight'], today))
        conn.commit()
        
    await message.answer("✅ Регистрация завершена! Теперь ты можешь полноценно пользоваться ботом.", reply_markup=get_main_keyboard())
    await state.clear()

# ==========================================
# 5. ИЗМЕНЕНИЕ ЦЕЛЕЙ И ПРОФИЛЬ
# ==========================================
@dp.message(F.text == "👤 Профиль")
async def show_profile(message: types.Message):
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT name, gender, age, height, daily_goal FROM users WHERE user_id = %s", (message.from_user.id,))
            u = cursor.fetchone()
            cursor.execute("SELECT weight FROM weight_log WHERE user_id = %s ORDER BY date DESC LIMIT 1", (message.from_user.id,))
            w = cursor.fetchone()

    if not u:
        return await message.answer("Сначала нажми /start и пройди регистрацию!")

    weight_str = f"{w[0]} кг" if w else "Нет данных"
    gender_str = "Мужской 👨" if u[1] == "male" else "Женский 👩"
    
    text = f"👤 **Профиль: {u[0]}**\n" \
           f"Пол: {gender_str}\nВозраст: {u[2]} лет\nРост: {u[3]} см\nТекущий вес: {weight_str}\n" \
           f"🎯 **Цель на день:** {u[4]} ккал"
           
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 Изменить цель калорий", callback_data="change_goal")]])
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "change_goal")
async def change_goal_start(callback: types.CallbackQuery):
    await callback.message.edit_text("На какой период ты хочешь задать новую цель?", reply_markup=get_goal_periods_keyboard())

@dp.callback_query(F.data.startswith("setgoal_"))
async def process_goal_period(callback: types.CallbackQuery, state: FSMContext):
    days = int(callback.data.split("_")[1])
    await state.update_data(goal_days=days)
    
    period_name = {1: "день", 7: "неделю", 30: "месяц", 90: "3 месяца"}[days]
    await callback.message.edit_text(f"Введи желаемое количество калорий на **{period_name}**:\n*(Я сам рассчитаю из этого твою суточную норму)*", parse_mode="Markdown")
    await state.set_state(GoalState.waiting_for_goal)

@dp.message(GoalState.waiting_for_goal)
async def save_new_goal(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Пожалуйста, введи целое число.")
    
    data = await state.get_data()
    days = data.get('goal_days', 1)
    total_goal = int(message.text)
    
    # Вычисляем дневную цель
    daily_goal = total_goal // days
    
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE users SET daily_goal = %s WHERE user_id = %s", (daily_goal, message.from_user.id))
        conn.commit()

    period_name = {1: "день", 7: "неделю", 30: "месяц", 90: "3 месяца"}[days]
    await message.answer(f"✅ Цель на {period_name} ({total_goal} ккал) сохранена!\nТвоя новая норма: **{daily_goal} ккал в день**.", parse_mode="Markdown")
    await state.clear()

# ==========================================
# 6. НЕЙРОСЕТЬ (AI)
# ==========================================
@dp.message(F.text == "🤖 Нейросеть (AI)")
async def ai_food_start(message: types.Message, state: FSMContext):
    if not ai_client:
        return await message.answer("Функция AI пока недоступна (не настроен ключ OPENAI_API_KEY).")
    
    await message.answer("Опиши, что ты съел в свободной форме (например: *я съел 3 яйца, 100г хлеба и 30г сыра*):", 
                         reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(AIState.waiting_for_food_text)

@dp.message(AIState.waiting_for_food_text)
async def ai_food_process(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("Отменено.", reply_markup=get_main_keyboard())

    await message.answer("⏳ Анализирую продукты... Подожди пару секунд.")
    
    prompt = f"""
    Ты профессиональный диетолог. Пользователь съел следующее: "{message.text}".
    Оцени примерную калорийность каждого продукта и посчитай общую сумму.
    Твой ответ должен быть СТРОГО в формате JSON с двумя ключами:
    "breakdown" - строковое описание расчета (каждый продукт с новой строки).
    "total" - только целое число (общая сумма калорий).
    """

    try:
        response = await ai_client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={ "type": "json_object" },
            messages=[{"role": "system", "content": prompt}]
        )
        
        result = json.loads(response.choices[0].message.content)
        breakdown = result.get("breakdown", "Нет описания")
        total_calories = int(result.get("total", 0))

        text = f"🤖 **Анализ нейросети:**\n\n{breakdown}\n\n**Итого:** {total_calories} ккал"
        
        # Кнопка для сохранения (передаем калории прямо в callback_data)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💾 Сохранить {total_calories} ккал", callback_data=f"aisave_{total_calories}")]
        ])
        
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"AI Error: {e}")
        await message.answer("Упс, не смог распознать еду. Попробуй написать чуть иначе.", reply_markup=get_main_keyboard())
    
    await state.clear()

@dp.callback_query(F.data.startswith("aisave_"))
async def save_ai_calories(callback: types.CallbackQuery):
    calories = int(callback.data.split("_")[1])
    today = datetime.now().date().isoformat()
    
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO calorie_log (user_id, meal_type, calories, date) VALUES (%s, %s, %s, %s)", 
                           (callback.from_user.id, "ai_food", calories, today))
        conn.commit()
        
    await callback.message.edit_text(callback.message.text + "\n\n✅ *Успешно сохранено в дневник!*", parse_mode="Markdown")

# ==========================================
# 7. РУЧНОЙ ВВОД ВЕСА И КАЛОРИЙ
# ==========================================
@dp.message(F.text == "❌ Отмена")
async def cancel_action(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_keyboard())

@dp.message(F.text == "⚖️ Внести вес")
async def btn_add_weight(message: types.Message, state: FSMContext):
    await message.answer("Введи текущий вес (кг):", reply_markup=get_cancel_keyboard())
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=MEALS["breakfast"], callback_data="meal_breakfast"),
         InlineKeyboardButton(text=MEALS["lunch"], callback_data="meal_lunch")],
        [InlineKeyboardButton(text=MEALS["dinner"], callback_data="meal_dinner"),
         InlineKeyboardButton(text=MEALS["snack"], callback_data="meal_snack")]
    ])
    await message.answer("Какой это прием пищи?", reply_markup=kb)

@dp.callback_query(F.data.startswith("meal_"))
async def callback_meal_chosen(callback: types.CallbackQuery, state: FSMContext):
    meal_type = callback.data.split("_")[1]
    await state.update_data(meal_type=meal_type)
    await callback.message.edit_text(f"Выбрано: {MEALS[meal_type]}\nСколько калорий?")
    await state.set_state(CalorieState.waiting_for_calories)

@dp.message(CalorieState.waiting_for_calories)
async def process_calories(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введите целое число.")
    
    calories = int(message.text)
    data = await state.get_data()
    today = datetime.now().date().isoformat()
    
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO calorie_log (user_id, meal_type, calories, date) VALUES (%s, %s, %s, %s)", 
                           (message.from_user.id, data['meal_type'], calories, today))
        conn.commit()
        
    await message.answer("✅ Записано!", reply_markup=get_main_keyboard())
    await state.clear()

# ==========================================
# 8. СТАТИСТИКА
# ==========================================
@dp.message(F.text == "📊 Статистика")
async def btn_statistics(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Сегодня (Сводка)", callback_data="stat_today")],
        [InlineKeyboardButton(text="🔥 За 7 дней", callback_data="stat_7days")]
    ])
    await message.answer("Выбери отчет:", reply_markup=kb)

@dp.callback_query(F.data == "stat_today")
async def callback_stat_today(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    today = datetime.now().date().isoformat()
    
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT daily_goal FROM users WHERE user_id = %s", (user_id,))
            u = cursor.fetchone()
            cursor.execute("SELECT SUM(calories) FROM calorie_log WHERE user_id = %s AND date = %s", (user_id, today))
            e = cursor.fetchone()[0] or 0

    if not u:
        return await callback.message.edit_text("Сначала пройди регистрацию в профиле!")

    goal = u[0]
    res = f"📊 **Сводка на сегодня:**\n🍽 Съедено: {e} ккал\n🎯 Твоя норма: {goal} ккал\n"
    res += f"📉 Осталось: {goal - e} ккал" if goal >= e else f"⚠️ Перебор: {e - goal} ккал!"
    
    await callback.message.edit_text(res, parse_mode="Markdown")

@dp.callback_query(F.data == "stat_7days")
async def callback_stat_7days(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    week_ago = (datetime.now() - timedelta(days=7)).date().isoformat()
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT SUM(calories) FROM calorie_log WHERE user_id = %s AND date >= %s", (user_id, week_ago))
            total = cursor.fetchone()[0] or 0
    await callback.message.edit_text(f"🔥 Калории за последние 7 дней: **{total} ккал**", parse_mode="Markdown")

# ==========================================
# 9. ЗАПУСК БОТА
# ==========================================
async def main():
    init_db()
    logger.info("Бот запущен и готов к работе...")
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