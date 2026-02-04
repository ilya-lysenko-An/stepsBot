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

    if text == "Ввести шаги":
        await update.message.reply_text("Введи количество шагов за сегодня.")
    elif text == "Редактировать шаги":
        await update.message.reply_text("Напиши дату и шаги, которые хочешь исправить.")



def main():
    if not TOKEN:
        logger.error("BOT_TOKEN не найден")
        return
    database.init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    app.run_polling()

if __name__ == "__main__":
    main()