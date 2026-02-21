import logging
import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import ReplyKeyboardMarkup
from telegram.ext import MessageHandler, filters
import database
import os 
from dotenv import load_dotenv 
from zoneinfo import ZoneInfo
from datetime import timedelta
from telegram import ReplyKeyboardRemove
from datetime import time



load_dotenv("token.env")
TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")

CHALLENGE_START_DATE_MSK = datetime.date(2026, 2, 20)   # поправить
CHALLENGE_END_DATE_MSK = datetime.date(2026, 3, 20)     # поправить
DAILY_TARGET = 10_000
DAILY_PENALTY_RUB = 100


JOIN_KEYBOARD = ReplyKeyboardMarkup(
    [["УЧАСТВУЮ"]],
    resize_keyboard=True
)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["Ввести шаги", "Меню"]],
    resize_keyboard=True
)

MENU_KEYBOARD = ReplyKeyboardMarkup(
     [["Редактировать шаги", "Уведомления"],
      ["Статистика", "Назад"]],
      resize_keyboard=True
)

STATS_KEYBOARD = ReplyKeyboardMarkup(
    [["Топ 30 all time", "Топ 10 вчера"],
    ["Моя активность", "Отставание от лидера"],
    ["Место в топе", "Назад"]],
    resize_keyboard=True
)

NOTIFY_KEYBOARD = ReplyKeyboardMarkup(
    [["Выкл напоминание", "Выкл статистику"]],
    resize_keyboard=True
)


def parse_ddmm(text: str):
    parts = text.split(".")
    if len(parts) != 2:
        return None
    day_str, month_str = parts
    if not (day_str.isdigit() and month_str.isdigit()):
        return None
    
    day = int(day_str)
    month = int(month_str)
    year = datetime.date.today().year

    try:
        dt = datetime.date(year, month, day)
    except ValueError:
        return None
    
    return dt.isoformat()

def now_msk() -> datetime.datetime:
    return datetime.datetime.now(MSK)

def today_msk() -> datetime.date:
    return now_msk().date()

def is_within_challenge(day: datetime.date) -> bool:
    return CHALLENGE_START_DATE_MSK <= day <= CHALLENGE_END_DATE_MSK

def evaluate_result(submitted_on_time: int, steps_value: int) -> tuple[str, str]:
    if submitted_on_time == 0:
        return "-", "late"
    if steps_value < DAILY_TARGET:
        return "-", "lt_10k"
    return "+", "ok"



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["menu"] = "main"
    context.user_data["state"] = None

    with open("welcome.jpg", "rb") as photo:
        await update.message.reply_photo(
            photo = photo,
            caption = "Макс привет",
            reply_markup=JOIN_KEYBOARD
        )


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if await handle_join_flow(update, context, text):
        return
    if await handle_navigation(update, context, text):
        return
    if await handle_stats_menu(update, context, text):
        return
    if await handle_notifications(update, context, text):
        return
    if await handle_state(update, context, text):
        return
    if await handle_actions(update, context, text):
        return


def build_notify_keyboard(reminder_enabled: int, stats_enabled: int):
    reminder_btn = "Выкл напоминание" if reminder_enabled else "Вкл напоминание"
    stats_btn = "Выкл статистику" if stats_enabled else "Вкл статистику"
    return ReplyKeyboardMarkup(
        [[reminder_btn, stats_btn]],
        resize_keyboard=True
    )


async def handle_join_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if text == "УЧАСТВУЮ":
        user = update.effective_user
        database.add_user(
            tg_id=user.id,
            username=user.username,
            first_name=user.first_name,
            club=None
        )
        context.user_data["state"] = "awaiting_club"
        await update.message.reply_text(
            "Напиши свой клуб, если клуба нет пиши 'нет'.",
            reply_markup=ReplyKeyboardRemove()
            )
        return True

    state = context.user_data.get("state")
    if state == "awaiting_club":
        club_text = text.strip()
        club = None if club_text.lower() == "нет" else club_text

        user = update.effective_user
        user_id = database.get_user_id(user.id)
        if user_id is None:
            database.add_user(
                tg_id=user.id,
                username=user.username,
                first_name=user.first_name,
                club=club
            )
        else:
            database.update_user_club(user_id=user_id, club=club)

        context.user_data["state"] = None
        await update.message.reply_text("Готово! Теперь можно ввести шаги.", reply_markup=MAIN_KEYBOARD)
        return True

    return False


async def handle_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if text == "Меню":
        context.user_data["menu"] = "menu"
        await update.message.reply_text("Меню:", reply_markup=MENU_KEYBOARD)
        return True

    if text == "Назад":
        current = context.user_data.get("menu", "main")
        if current == "stats":
            context.user_data["menu"] = "menu"
            await update.message.reply_text("Меню:", reply_markup=MENU_KEYBOARD)
        else:
            context.user_data["menu"] = "main"
            await update.message.reply_text("Выбери действие", reply_markup=MAIN_KEYBOARD)
        return True

    if text == "Уведомления":
        user = update.effective_user
        settings = database.get_user_settings(user.id)
        if settings is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».", reply_markup=MENU_KEYBOARD)
            return True

        reminder_enabled, stats_enabled = settings
        kb = build_notify_keyboard(reminder_enabled, stats_enabled)
        await update.message.reply_text("Уведомления:", reply_markup=kb)
        return True


    if text == "Статистика":
        context.user_data["menu"] = "stats"
        await update.message.reply_text("Статистика:", reply_markup=STATS_KEYBOARD)
        return True

    return False

async def handle_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user = update.effective_user
    settings = database.get_user_settings(user.id)
    if settings is None:
        return False

    reminder_enabled, stats_enabled = settings

    if text == "Выкл напоминание":
        database.disable_reminder(database.get_user_id(user.id))
        await update.message.reply_text("Напоминания отключены.", reply_markup=MENU_KEYBOARD)
        return True

    if text == "Вкл напоминание":
        database.enable_reminder(database.get_user_id(user.id))
        await update.message.reply_text("Напоминания включены.", reply_markup=MENU_KEYBOARD)
        return True

    if text == "Выкл статистику":
        database.disable_stats(database.get_user_id(user.id))
        await update.message.reply_text("Статистика отключена.", reply_markup=MENU_KEYBOARD)
        return True

    if text == "Вкл статистику":
        database.enable_stats(database.get_user_id(user.id))
        await update.message.reply_text("Статистика включена.", reply_markup=MENU_KEYBOARD)
        return True

    return False



async def handle_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if text == "Топ 30 all time":
        await update.message.reply_text("Раздел в разработке.")
        return True

    if text == "Топ 10 вчера":
        await update.message.reply_text("Раздел в разработке.")
        return True

    if text == "Моя активность":
        await update.message.reply_text("Раздел в разработке.")
        return True

    if text == "Отставание от лидера":
        await update.message.reply_text("Раздел в разработке.")
        return True

    if text == "Место в топе":
        await update.message.reply_text("Раздел в разработке.")
        return True

    return False


async def handle_state(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    state = context.user_data.get("state")

    if state == "awaiting_steps_today":
        if not text.isdigit():
            await update.message.reply_text("Слова не понимаю, напиши число.")
            return True

        steps = int(text)
        user = update.effective_user
        user_id = database.get_user_id(user.id)
        if user_id is None:
            database.add_user(
                tg_id=user.id,
                username=user.username,
                first_name=user.first_name,
                club=None
            )
            user_id = database.get_user_id(user.id)

        today = datetime.date.today().isoformat()
        database.upsert_steps(user_id=user_id, day=today, steps=steps)
        database.reset_missed_streak(user_id=user_id)
        logger.info(f"Шаги сохранены: user_id={user_id}, day={today}, steps={steps}")

        context.user_data["state"] = None
        await update.message.reply_text("Сохранено.", reply_markup=MAIN_KEYBOARD)
        return True

    if state == "awaiting_edit_date":
        day_iso = parse_ddmm(text)
        if not day_iso:
            await update.message.reply_text("Неверный формат. Пример: 05.02")
            return True

        context.user_data["edit_date"] = day_iso
        context.user_data["state"] = "awaiting_edit_steps"
        await update.message.reply_text("Теперь введи количество шагов за эту дату.")
        return True

    if state == "awaiting_edit_steps":
        if not text.isdigit():
            await update.message.reply_text("Введи число.")
            return True

        steps = int(text)
        day = context.user_data.get("edit_date")

        user = update.effective_user
        user_id = database.get_user_id(user.id)
        if user_id is None:
            database.add_user(
                tg_id=user.id,
                username=user.username,
                first_name=user.first_name,
                club=None
            )
            user_id = database.get_user_id(user.id)

        database.upsert_steps(user_id=user_id, day=day, steps=steps)
        logger.info(f"Шаги сохранены (редакт): user_id={user_id}, day={day}, steps={steps}")

        context.user_data["state"] = None
        context.user_data["edit_date"] = None
        await update.message.reply_text("Сохранено.", reply_markup=MAIN_KEYBOARD)
        return True

    return False


async def handle_actions(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if text == "Ввести шаги":
        context.user_data["state"] = "awaiting_steps_today"
        await update.message.reply_text("Введи количество шагов за сегодня.")
        return True

    if text == "Редактировать шаги":
        context.user_data["state"] = "awaiting_edit_date"
        await update.message.reply_text("Введи дату в формате ДД.ММ.")
        return True

    return False

    
async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    tz = ZoneInfo("Europe/Moscow")
    today = datetime.datetime.now(tz).date().isoformat()

    missing_users = database.get_users_missing_day(today)

    for user_id, tg_id, missed_streak in missing_users:
        await context.bot.send_message(
            chat_id=tg_id,
            text="Соо для внесения шагов" #Добавить сообщение с напоминанием ввести шаги 
        )

        database.increment_missed_streak(user_id)

        if missed_streak + 1 >= 7:
            database.disable_reminder(user_id)


async def morning_top_job(context: ContextTypes.DEFAULT_TYPE):
    tz = ZoneInfo("Europe/Moscow")
    today = datetime.datetime.now(tz).date()
    yesterday = (today - timedelta(days=1)).isoformat()

    top10 = database.get_top10_for_day(yesterday)

    lines = ["Соо топ 10 вчера"] # Добавить сообщение топ 10 за вчера 
    for i, (first_name, username, steps) in enumerate(top10, start=1):
        name = f"@{username}" if username else first_name or "Без имени"
        lines.append(f"{i}. {name} — {steps}")

    message = "\n".join(lines)

    participants = database.get_participants()
    for _, tg_id, _, stats_enabled, _ in participants:
        if stats_enabled:
            await context.bot.send_message(chat_id=tg_id, text=message)


def main():
    if not TOKEN:
        logger.error("BOT_TOKEN не найден")
        return
    database.init_db()
    logger.info("База данных инициализированна")
    app = Application.builder().token(TOKEN).build()
    tz = ZoneInfo("Europe/Moscow")

    app.job_queue.run_daily(
        reminder_job,
        time=datetime.time(hour=23, minute=0, tzinfo=tz)
    )
    app.job_queue.run_daily(
        morning_top_job,
        time=datetime.time(hour=7, minute=0, tzinfo=tz)
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()