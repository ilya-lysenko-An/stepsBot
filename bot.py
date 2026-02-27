import logging
import datetime
from datetime import time

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.error import TimedOut, NetworkError
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
import os

import database


load_dotenv("token.env")
TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    level = logging.ERROR,
    format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("telegram").setLevel(logging.ERROR)
logging.getLogger("apscheduler").setLevel(logging.ERROR)

MSK = ZoneInfo("Europe/Moscow")

CHALLENGE_START_DATE_MSK = datetime.date(2026, 2, 23)   # поправить при необходимости
CHALLENGE_END_DATE_MSK = datetime.date(2026, 2, 28)     # поправить при необходимости
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


def parse_ddmm(text: str):
    parts = text.split(".")
    if len(parts) != 2:
        return None

    day_str, month_str = parts
    if not (day_str.isdigit() and month_str.isdigit()):
        return None

    day = int(day_str)
    month = int(month_str)
    year = today_msk().year

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


def build_notify_keyboard(notifications_enabled: int):
    btn = "Выкл уведомления" if notifications_enabled else "Вкл уведомления"
    return ReplyKeyboardMarkup([[btn]], resize_keyboard=True)


async def safe_send_message(bot, chat_id: int, text: str):
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except (TimedOut, NetworkError) as e:
        logger.warning("Send message timeout/network error chat_id=%s err=%s", chat_id, e)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["menu"] = "main"
    context.user_data["state"] = None

    try:
        with open("welcome.jpg", "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=('''Добро пожаловать в Bigagram Steps Challenge.
                            Вот всё, что нужно знать о том, куда ТЫЫЫЫ попал(а).
                            Располагайся и слушай.

                           Это челлендж для людей, которые:
                           — много ходят
                           — иногда бегают
                           — и считают шаги важнее смысла жизни

                           Неважно, Garmin у тебя, Apple Watch, Amazfit или телефон из «М.Видео».
                           Здесь шаги равны. Все равны. Почти.

                           Суть простая:
                           — каждый день вносишь шаги
                           — цель — 10 000 шагов в день
                           — не добрал или забил — штраф 100 ₽

                           Жми «УЧАСТВУЮ».
                           Назад дороги уже почти нет. '''
                ),
                reply_markup=JOIN_KEYBOARD
            )
    except FileNotFoundError:
        await update.message.reply_text(
            "Всем ку! Бот почти готов. Жми «УЧАСТВУЮ».",
            reply_markup=JOIN_KEYBOARD
        )
    except (TimedOut, NetworkError) as e:
        logger.warning("start reply timeout/network error: %s", e)


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    try:
        if await handle_join_flow(update, context, text):
            return
        if await handle_navigation(update, context, text):
            return
        if await handle_notifications(update, context, text):
            return
        if await handle_actions(update, context, text):
            return
        if await handle_stats_menu(update, context, text):
            return
        if await handle_state(update, context, text):
            return
    except (TimedOut, NetworkError) as e:
        logger.warning("handle_menu timeout/network error: %s", e)


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
            '''Прекрасно. Ты сделал(а) осознанный выбор.
               Теперь напиши свой клуб.

               Если клуба нет — напиши «нет».

               Это нужно:
               — для статистики
               — для будущих срачей
               — и чтобы потом было понятно, кто откуда пришёл''',
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
        await update.message.reply_text(
            '''Готово. Ты официально в игре 🫡
               Краткий инструктаж, без занудства:

               🚶‍♂️ Каждый день
               — нажимаешь «Ввести шаги»
               — вводишь общее число шагов за день

               🎯 Цель
               — 10 000 шагов в день

               💸 Штрафы
               — меньше 10 000 → +100 ₽
               — не внёс шаги за день → +100 ₽
               — штраф один, но неизбежный

               ⏰ Забыл?
               — бот напомнит вечером
               — шаги можно внести позже
               — но штраф за пропуск дня уже никуда не денется

               📊 В «Меню» найдёшь:
               — свою статистику
               — штрафы
               — отставание от лидера

               Всё честно. Всё считается.
               Ходи. Считай. Страдай.

               Поехали 🚀''',
            reply_markup=MAIN_KEYBOARD)
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

        (notifications_enabled,) = settings
        kb = build_notify_keyboard(notifications_enabled)
        await update.message.reply_text("Уведомления:", reply_markup=kb)
        return True

    if text == "Статистика":
        context.user_data["menu"] = "stats"
        await update.message.reply_text("Статистика:", reply_markup=STATS_KEYBOARD)
        return True

    return False


async def handle_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user = update.effective_user
    user_id = database.get_user_id(user.id)
    if user_id is None:
        return False

    if text == "Выкл уведомления":
        database.set_notifications_enabled(user_id, 0)
        await update.message.reply_text("Уведомления отключены.", reply_markup=MENU_KEYBOARD)
        return True

    if text == "Вкл уведомления":
        database.set_notifications_enabled(user_id, 1)
        await update.message.reply_text("Уведомления включены.", reply_markup=MENU_KEYBOARD)
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
        user = update.effective_user
        user_id = database.get_user_id(user.id)
        if user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
            return True
        
        total_steps, minus_count, avg_steps = database.get_user_activity_stats(user_id)
        penalty_sum = minus_count * DAILY_PENALTY_RUB

        msg = (
            f"Моя активность:\n"
            f"1) Общее число шагов: {total_steps}\n"
            f"2) Среднее количество шагов: {avg_steps}\n"
            f"3) Сумма штрафа: {penalty_sum} ₽"
        )
        await update.message.reply_text(msg)
        return True
        

    if text == "Отставание от лидера":
        user = update.effective_user
        user_id = database.get_user_id(user.id)
        if user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ»." )
            return True
        
        leader, user_total = database.get_leader_and_user_total(user_id)
        if leader is None:
            await update.message.reply_text("Лидера пока нет, стань первым")
            return True
        
        leader_user_id, leader_total = leader
        leader_total = int(leader_total or 0)

        if user_id == leader_user_id:
            await update.message.reply_text("Вы и есть лидер! Сможете удержать этот титул ?)")
            return True
        
        lag = leader_total - user_total
        await update.message.reply_text(
            f"Ваше отстование от лидера: {lag} шагов\n"
            f"У лидера всего: {leader_total}, у вас: {user_total}"
        )
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
        if steps < 0 or steps > 100000:
            await update.message.reply_text("Ты че то много написал, сбавь обороты")
            return True

        user = update.effective_user
        user_id = database.get_user_id(user.id)
        if user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
            return True

        day = today_msk()
        if not is_within_challenge(day):
            await update.message.reply_text("Этот день вне периода челленджа.")
            return True

        day_str = day.isoformat()
        current = database.get_daily_status(user_id, day_str)

        submitted_on_time = 1 if now_msk().time() <= time(23, 59, 59) else 0
        if current is not None:
            submitted_on_time = current[4]

        result, reason = evaluate_result(submitted_on_time, steps)

        database.upsert_daily_status(
            user_id=user_id,
            day_msk=day_str,
            steps_value=steps,
            submitted_on_time=submitted_on_time,
            result=result,
            result_reason=reason
        )

        logger.info(
            "Шаги сохранены: user_id=%s, day=%s, steps=%s, result=%s, reason=%s",
            user_id, day_str, steps, result, reason
        )
        context.user_data["state"] = None
        await update.message.reply_text("Записал.", reply_markup=MAIN_KEYBOARD)
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
        if steps < 0 or steps > 100000:
            await update.message.reply_text("Ты че то много написал, сбавь обороты")
            return True

        day_str = context.user_data.get("edit_date")
        if not day_str:
            await update.message.reply_text("Сначала выбери дату.")
            return True

        day = datetime.date.fromisoformat(day_str)
        if not is_within_challenge(day):
            await update.message.reply_text("Эта дата вне периода челленджа.")
            return True

        user = update.effective_user
        user_id = database.get_user_id(user.id)
        if user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
            return True

        current = database.get_daily_status(user_id, day_str)

        if current is None:
            submitted_on_time = 1 if (day == today_msk() and now_msk().time() <= time(23, 59, 59)) else 0
        else:
            submitted_on_time = current[4]

        result, reason = evaluate_result(submitted_on_time, steps)

        database.upsert_daily_status(
            user_id=user_id,
            day_msk=day_str,
            steps_value=steps,
            submitted_on_time=submitted_on_time,
            result=result,
            result_reason=reason
        )

        logger.info(
            "Шаги сохранены (редакт): user_id=%s, day=%s, steps=%s, result=%s, reason=%s",
            user_id, day_str, steps, result, reason
        )
        context.user_data["state"] = None
        context.user_data["edit_date"] = None
        await update.message.reply_text("Сохранено.", reply_markup=MAIN_KEYBOARD)
        return True

    return False


async def handle_actions(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if text == "Ввести шаги":
        context.user_data["edit_date"] = None
        context.user_data["state"] = "awaiting_steps_today"
        await update.message.reply_text("Введи количество шагов за сегодня.")
        return True

    if text == "Редактировать шаги":
        context.user_data["edit_date"] = None
        context.user_data["state"] = "awaiting_edit_date"
        await update.message.reply_text("Введи дату в формате ДД.ММ.")
        return True

    return False


async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    day = today_msk()
    if not is_within_challenge(day):
        return

    day_str = day.isoformat()
    users = database.get_active_users()

    for user_id, tg_id, username, first_name, club, notifications_enabled in users:
        if not notifications_enabled:
            continue

        row = database.get_daily_status(user_id, day_str)
        if row is None:
            await safe_send_message(
                context.bot,
                tg_id,
                '''Напоминание.
                   Шаги за сегодня сами себя не внесут.
                   До 23:59 ещё можно спастись.'''
            )


async def finalize_day_job(context: ContextTypes.DEFAULT_TYPE):
    day = today_msk() - datetime.timedelta(days=1)
    if not is_within_challenge(day):
        return
    database.finalize_no_submission_for_day(day.isoformat())


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error: %s", context.error)


def main():
    if not TOKEN:
        logger.error("BOT_TOKEN не найден")
        return

    database.init_db()
    logger.info("База данных инициализированна")

    app = Application.builder().token(TOKEN).build()

    app.job_queue.run_daily(
        reminder_job,
        time=time(hour=23, minute=0, second=0, tzinfo=MSK)
    )

    app.job_queue.run_daily(
        finalize_day_job,
        time=time(hour=0, minute=0, second=5, tzinfo=MSK)
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    app.add_error_handler(on_error)

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
