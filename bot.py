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

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["Ввести шаги", "Редактировать шаги"]],
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
    user = update.effective_user
    database.add_user(
        tg_id=user.id,
        username=user.username,
        first_name=user.first_name,
        club = None
    )
    await update.message.reply_text("позже написать текст преветсвенного соо")
    await update.message.reply_text("Выбери действие", reply_markup=MAIN_KEYBOARD)

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    state = context.user_data.get("state")

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
                club = None
            )
            user_id = database.get_user_id(user.id)
        
        today = datetime.date.today().isoformat()
        database.upsert_steps(user_id= user_id, day= today, steps= steps)
        logger.info(f"Шаги сохранены: user_id={user_id}, day={today}, steps={steps}")


        context.user_data["state"] = None
        await update.message.reply_text("Сохранено.", reply_markup=MAIN_KEYBOARD)
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
        
        steps =int(text)
        day = context.user_data.get("edit_date")

        user = update.effective_user
        user_id = database.get_user_id(user.id)
        if user_id is None:
            database.add_user(
                tg_id=user.id,
                username= user.username,
                first_name=user.first_name,
                club = None
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
    elif text == "Редактировать шаги":
        context.user_data["state"] = "awaiting_edit_date"
        await update.message.reply_text("Введи дату в формате ДД.ММ.")



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