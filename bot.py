import logging
import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import database
import os 
from dotenv import load_dotenv  

load_dotenv("token.env")
TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("")

def main():
    if not TOKEN:
        logger.error("BOT_TOKEN не найден")
        return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()

    if __name__ == "__main__":
        main()