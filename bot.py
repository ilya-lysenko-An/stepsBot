import logging
import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import ReplyKeyboardMarkup
from telegram.ext import MessageHandler, filters
import database
import os 
from dotenv import load_dotenv  

load_dotenv("token.env")
TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["menu"] = "main"
    context.user_data["state"] = None

    await update.message.reply_text("Тут будет текст правил…")
    await update.message.reply_text("Нажми кнопку для участия", reply_markup=JOIN_KEYBOARD)



async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "УЧАСТВУЮ":
        user = update.effective_user
        database.add_user(
            tg_id=user.id,
            username=user.username,
            first_name=user.first_name,
            club=None
        )
        context.user_data["state"] = "awaiting_club"
        await update.message.reply_text("Напиши свой клуб, если клуба нет пиши 'нет'.")
        return

    state = context.user_data.get("state")

    if text == "Меню":
        context.user_data["menu"] = "menu"
        await update.message.reply_text("Меню:", reply_markup=MENU_KEYBOARD)
        return

    if text == "Назад":
        current = context.user_data.get("menu", "main")
        if current == "stats":
            context.user_data["menu"] = "menu"
            await update.message.reply_text("Меню:", reply_markup=MENU_KEYBOARD)
        else:
            context.user_data["menu"] = "main"
            await update.message.reply_text("Выбери действие", reply_markup=MAIN_KEYBOARD)
        return

    if text == "Уведомления":
        await update.message.reply_text("Раздел уведомлений в разработке.")
        return

    if text == "Статистика":
        context.user_data["menu"] = "stats"
        await update.message.reply_text("Статистика:", reply_markup=STATS_KEYBOARD)
        return

    if text == "Топ 30 all time":
        await update.message.reply_text("Раздел в разработке.")
        return

    if text == "Топ 10 вчера":
        await update.message.reply_text("Раздел в разработке.")
        return

    if text == "Моя активность":
        await update.message.reply_text("Раздел в разработке.")
        return

    if text == "Отставание от лидера":
        await update.message.reply_text("Раздел в разработке.")
        return

    if text == "Место в топе":
        await update.message.reply_text("Раздел в разработке.")
        return

    if state == "awaiting_steps_today":
        if not text.isdigit():
            await update.message.reply_text("Слова не понимаю, напиши число.")
            return
        
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
        logger.info(f"Шаги сохранены: user_id={user_id}, day={today}, steps={steps}")

        context.user_data["state"] = None
        await update.message.reply_text("Сохранено.", reply_markup=MAIN_KEYBOARD)
        return
    
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
            database.update_user_club(user_id=user_id, club=club)  # нужна функция

        context.user_data["state"] = None
        await update.message.reply_text("Готово! Теперь можно ввести шаги.", reply_markup=MAIN_KEYBOARD)
        return

    
    if state == "awaiting_edit_date":
        day_iso = parse_ddmm(text)
        if not day_iso:
            await update.message.reply_text("Неверный формат. Пример: 05.02")
            return
        
        context.user_data["edit_date"] = day_iso
        context.user_data["state"] = "awaiting_edit_steps"
        await update.message.reply_text("Теперь введи количество шагов за эту дату.")
        return
    
    if state == "awaiting_edit_steps":
        if not text.isdigit():
            await update.message.reply_text("Введи число.")
            return
        
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
        return

    if text == "Ввести шаги":
        context.user_data["state"] = "awaiting_steps_today"
        await update.message.reply_text("Введи количество шагов за сегодня.")
        return

    if text == "Редактировать шаги":
        context.user_data["state"] = "awaiting_edit_date"
        await update.message.reply_text("Введи дату в формате ДД.ММ.")
        return


def main():
    if not TOKEN:
        logger.error("BOT_TOKEN не найден")
        return
    database.init_db()
    logger.info("База данных инициализированна")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()