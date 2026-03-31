import logging
import datetime
from datetime import time

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.error import TimedOut, NetworkError, TelegramError
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

REGISTRATION_DEADLINE_MSK = datetime.date(2026, 3, 3)
CHALLENGE_START_DATE_MSK = datetime.date(2026, 3, 1)   # поправить при необходимости
CHALLENGE_END_DATE_MSK = datetime.date(2026, 3, 31)     # поправить при необходимости
DAILY_TARGET = 10_000
DAILY_PENALTY_RUB = 100
CHANNEL_ID = "@begogram_ch"
CHANNEL_URL = "https://t.me/begogram_ch"
INVITE_CHAT_URL = "https://t.me/+jQApV8d7yuU1YWEy"
FUNDRAISER_URL = "https://www.tbank.ru/cf/3Lu4w17wJN8"
FUNDRAISER_URL_2 = "https://messenger.online.sberbank.ru/sl/oUVZUrKcOwBJORTLz"
INVITE_SEND_AT = datetime.datetime(2026, 3, 27, 16, 30, 0, tzinfo=MSK)
FUND_SEND_AT   = datetime.datetime(2026, 3, 29, 10, 47, 0, tzinfo=MSK)



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
    [["Топ 20 все время", "Топ 10 вчера"],
     ["Моя активность", "Отставание от лидера"],
     ["Банк", "Назад"]],
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
    return ReplyKeyboardMarkup(
        [[btn], ["Назад"]],
        resize_keyboard=True
    )

def format_top10_yesterday(rows, day_str: str, my_rank: int = None, total_users: int = None) -> str:
    header = f"Топ-10 за вчера ({day_str})\n"
    if not rows:
        return header + "\nЗа этот день пока нет данных"
    
    lines = [header]
    for idx, (first_name, username, steps_value) in enumerate(rows, start = 1):
        display_name = first_name or "Без имени"
        username_part = f"@{username}" if username else "без username"
        steps_part = f"{int(steps_value):,}".replace(","," ")
        lines.append(f"{idx}) {display_name} - {username_part} - {steps_part}")

    if my_rank is not None:
        lines.append("")
        lines.append(f"🏆 Твое место: {my_rank} / {total_users}")

    return "\n".join(lines)


def format_all_time_ranking(rows, my_user_id: int, my_rank: int, total_users: int) -> str:
    if not rows:
        return "Топ-20 за все время\n\nПока нет данных."
    
    lines = ["Топ-20 за все время\n"]
    for idx, (user_id, first_name, username, total_steps) in enumerate(rows, start=1):
         name = first_name or "Без имени"
         uname = f"@{username}" if username else "без username"
         steps = f"{int(total_steps):,}".replace(",", " ")
         lines.append(f"{idx}) {name} — {uname} — {steps}")

    if my_rank is not None:
        lines.append("")
        lines.append(f"🏆 Твое место: {my_rank} / {total_users}")
    return "\n".join(lines)

def build_final_report_message() -> str:
    date_from = CHALLENGE_START_DATE_MSK.isoformat()
    date_to = CHALLENGE_END_DATE_MSK.isoformat()
    total_days = (CHALLENGE_END_DATE_MSK - CHALLENGE_START_DATE_MSK).days + 1

    top3 = database.get_top3_total(date_from, date_to)
    max_day = database.get_max_day(date_from, date_to)
    min_day = database.get_min_day_nonzero(date_from, date_to)
    min_total_full = database.get_min_total_full_days(date_from, date_to, total_days)
    total_steps, minus_count = database.get_summary_totals(date_from, date_to)
    no_penalties_count = database.get_users_without_penalties(date_from, date_to)

    avg_steps = int(total_steps / total_days) if total_days > 0 else 0
    bank_rub = minus_count * DAILY_PENALTY_RUB
    total_km = total_steps / 1000

    def fmt_name(username, first_name):
        if username:
            return f"{first_name} — @{username}"
        return f"{first_name}"

    msg = []
    msg.append("Финиш! Челлендж закрыт, и вот наши герои:\n")

    for i, row in enumerate(top3, start=1):
        username, first_name, total = row
        msg.append(f"{i}) {fmt_name(username, first_name)} — {total}")

    msg.append("")
    msg.append("Номинации вечера:\n")

    if max_day:
        u, f, steps, _day = max_day
        msg.append(f"🏎 Самый мощный день: {steps} шагов — {fmt_name(u, f)}")
    if min_day:
        u, f, steps, _day = min_day
        msg.append(f"🐢 Самый скромный день: {steps} шагов — {fmt_name(u, f)}")
    if min_total_full:
        u, f, total = min_total_full
        msg.append(f"🖤 Чёрная майка Джиро: {total} шагов — {fmt_name(u, f)}")

    msg.append("")
    msg.append("Итоги в цифрах:\n")
    msg.append(f"👣 Всего шагов: {total_steps} (≈ {total_km:.1f} км)")
    msg.append(f"📊 Среднее в день: {avg_steps}")
    msg.append(f"💰 Общий банк: {bank_rub} ₽")
    msg.append(f"🧼 Без штрафов: {no_penalties_count} человек")

    return "\n".join(msg)


         
async def safe_send_message(bot, chat_id: int, text: str):
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except (TimedOut, NetworkError) as e:
        logger.warning("Send timeout/network chat_id=%s err=%s", chat_id, e)
        return False
    except TelegramError as e:
        logger.warning("Send telegram error chat_id=%s err=%s", chat_id, e)
        return False

async def is_subscribed(context: ContextTypes.DEFAULT_TYPE, tg_user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, tg_user_id)
        return member.status in ("member", "administrator", "creator")
    except TelegramError:
        return False



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if today_msk() > REGISTRATION_DEADLINE_MSK:
        await update.message.reply_text(
            "Регистрация закрыта с 4 марта 2026. Новых участников больше не принимаем."
        )
        return
    
    context.user_data["menu"] = "main"
    context.user_data["state"] = None

    long_text = (
        "👋 Добро пожаловать в Begogram Steps Challenge!\n\n"
        "Вот всё, что нужно знать, куда ТЫЫЫ попал(а). Располагайся, читай и шагай.\n\n"
        "🗓 Когда: 1–31 марта\n"
        "🚶‍♂️ Что делать: проходить минимум 10 000 шагов в день.\n"
        "Да-да, любой способ движения: прогулки, пробежки, жизнь вне дивана — всё считается.\n\n"
        "🤖 Как участвовать:\n"
        "• каждый день до полуночи вносим шаги в бота;\n"
        "• бот напомнит, если забыл (он суровый, но справедливый);\n"
        "• ошибся или забыл — редактируем позже, всё честно.\n\n"
        "💸 Штрафы:\n"
        "– 100 ₽, если:\n"
        "• меньше 10 000 шагов за день;\n"
        "• шаги не внесены вовремя (даже если потом внесёшь).\n"
        "❗ один штраф за день, не суммируется\n"
        "❗ платить сразу не нужно — бот всё подсчитает сам\n\n"
        "💰 Призовой фонд:\n"
        "Все штрафы формируют банк месяца.\n"
        "В конце марта он делится так:\n"
        "🥇 1 место — 50%\n"
        "🥈 2 место — 30%\n"
        "🥉 3 место — 20%\n"
        "Побеждают те, кто прошагал больше всех.\n\n"
        "📊 Статистика:\n"
        "– в любой момент можешь:\n"
        "• проверить свой прогресс;\n"
        "• увидеть лидеров;\n"
        "• узнать размер банка;\n"
        "• глянуть свои штрафные дни.\n\n"
        "🤝 Важно:\n"
        "• участие бесплатное;\n"
        "• можно пройти месяц без штрафов;\n"
        "• форс-мажоры бывают — пишешь Максиму или Илье, стараемся решить.\n\n"
        "• Ты должен быть подписан на канал Вестника Бегограма"
        "🚶‍♀️ И главное:\n"
        "Шагаем, соревнуемся, двигаемся и кайфуем.\n"
        "💪 Удачи — и да пребудут с тобой шаги!"
    )

    try:
        with open("welcome.jpg", "rb") as photo:
            await update.message.reply_photo(photo=photo)

        await update.message.reply_text(
            text=long_text,
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
    message = update.effective_message
    if message is None or message.text is None:
        return
    text = message.text

    try:
        if await handle_join_flow(update, context, text):
            return
        if await handle_navigation(update, context, text):
            return
        if await handle_notifications(update, context, text):
            return
        if await handle_actions(update, context, text):
            return
        if await handle_state(update, context, text):
            return
        if await handle_stats_menu(update, context, text):
            return
    except (TimedOut, NetworkError) as e:
        logger.warning("handle_menu timeout/network error: %s", e)


async def handle_join_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if text == "УЧАСТВУЮ":
        user = update.effective_user

        ok = await is_subscribed(context, user.id)
        if not ok:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Подписаться на @begogram_ch", url=CHANNEL_URL)]
                ])
            await update.message.reply_text(
                "Сначала подпишись на канал, потом снова нажми «УЧАСТВУЮ».",
                reply_markup=kb
            )
            return True

        database.add_user(
            tg_id=user.id,
            username=user.username,
            first_name=user.first_name,
            club=None
        )
        context.user_data["state"] = "awaiting_club"
        await update.message.reply_text(
            "Прекрасно. Ты сделал(а) осознанный выбор.\n"
            "Теперь напиши свой клуб.\n\n"
            "Если клуба нет — напиши «нет».\n\n"
            "Это нужно:\n"
            "— для статистики\n"
            "— для будущих срачей\n"
            "— и чтобы потом было понятно, кто откуда пришёл",
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
            "Готово. Ты официально в игре 🫡\n"
            "Краткий инструктаж, без занудства:\n\n"
            "🚶‍♂️ Каждый день\n"
            "— нажимаешь «Ввести шаги»\n"
            "— вводишь общее число шагов за день\n\n"
            "🎯 Цель\n"
            "— 10 000 шагов в день\n\n"
            "💸 Штрафы\n"
            "— меньше 10 000 -> +100 ₽\n"
            "— не внёс шаги за день -> +100 ₽\n"
            "— штраф один, но неизбежный\n\n"
            "⏰ Забыл?\n"
            "— бот напомнит вечером\n"
            "— шаги можно внести позже\n"
            "— но штраф за пропуск дня уже никуда не денется\n\n"
            "📊 В «Меню» найдёшь:\n"
            "— свою статистику\n"
            "— штрафы\n"
            "— отставание от лидера\n\n"
            "Всё честно. Всё считается.\n"
            "Ходи. Считай. Страдай.\n\n"
            "Поехали 🚀",
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
        if current in ("stats", "notifications"):
            context.user_data["menu"] = "menu"
            await update.message.reply_text("Меню:", reply_markup=MENU_KEYBOARD)
        else:
            context.user_data["menu"] = "main"
            await update.message.reply_text("Выбери действие", reply_markup=MAIN_KEYBOARD)
        return True

    if text == "Уведомления":
        context.user_data["menu"] = "notifications"
        user = update.effective_user
        settings = database.get_user_settings(user.id)
        if settings is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».", reply_markup=MENU_KEYBOARD)
            return True

        (notifications_enabled,) = settings
        kb = build_notify_keyboard(notifications_enabled)
        await update.message.reply_text(
            "Уведомления. Последний шанс не облажаться.\n\n"
            "Оно придет вам ровно в 22:00 при условии, что вы не внесли шаги.\n"
            "Если шаги сохранены, можете спать спокойно.\n\n"
            "🔔 Включить уведомления\n"
            "Бот будет напоминать,\n"
            "что шаги сами себя не внесут,\n"
            "а 23:59 — это не шутка.\n\n"
            "🔕 Выключить уведомления\n"
            "Ты взрослый человек.\n"
            "Сам помнишь.\n"
            "(или платишь штрафы, как взрослый).",
        reply_markup=kb)
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
        context.user_data["menu"] = "notifications"
        await update.message.reply_text(
            "Уведомления отключены.",
            reply_markup=build_notify_keyboard(0)
        )
        return True

    if text == "Вкл уведомления":
        database.set_notifications_enabled(user_id, 1)
        context.user_data["menu"] = "notifications"
        await update.message.reply_text(
            "Уведомления включены.",
            reply_markup=build_notify_keyboard(1)
        )
        return True

    return False


async def handle_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if text == "Топ 20 все время":
        user = update.effective_user
        my_user_id = database.get_user_id(user.id)
        if my_user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
            return True
        
        rows = database.get_all_time_ranking()
        my_rank, total_users = database.get_user_rank_and_total_users(my_user_id)
        msg = format_all_time_ranking(rows, my_user_id, my_rank, total_users)
        await update.message.reply_text(msg)
        return True
  
    if text == "Топ 10 вчера":
        user = update.effective_user
        my_user_id = database.get_user_id(user.id)
        if my_user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
            return True
        day = today_msk() - datetime.timedelta(days=1)
        day_iso = day.isoformat()

        rows = database.get_top10_for_day(day_iso)
        my_rank, total_users = database.get_user_rank_for_day(day_iso, my_user_id)

        msg = format_top10_yesterday(rows, day.strftime("%d.%m.%Y"), my_rank, total_users)
        await update.message.reply_text(msg)
        return True

    if text == "Моя активность":
        user = update.effective_user
        user_id = database.get_user_id(user.id)
        if user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
            return True
        
        total_steps, minus_count, avg_steps = database.get_user_activity_stats(user_id) # средние шаги, всего шагов
        penalty_sum = minus_count * DAILY_PENALTY_RUB # сумма штрафа

        msg = (
            "Твоя активность. Личный отчёт. Без прикрас.\n\n"
            f"🚶‍♂️ Всего натоптано:\n{total_steps}\n"
            "(да, это реально много)\n\n"
            f"📈 Среднее за день:\n{avg_steps}\n"
            "крепкий, уверенный режим, без сюрпризов\n\n"
            f"💸 Штрафы:\n{penalty_sum} ₽\n"
            "не идеально, но и не позорно\n\n"
            "Вывод:\n"
            "Ты не просто ходишь — ты системно изнашиваешь асфальт.\n"
            "Пара осечек была, но в целом — достойно. Продолжай в том же духе."
        )
        await update.message.reply_text(msg)
        return True
        

    if text == "Отставание от лидера":
        user = update.effective_user
        user_id = database.get_user_id(user.id)
        if user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ»." )
            return True
        
        leader, user_total = database.get_leader_and_user_total(user_id) #user total - шаги юзера leader - шаги лидера 
        if leader is None:
            await update.message.reply_text("Король пока не найден! Бери руки в ноги и бегом становиться первым лидером")
            return True
        
        leader_user_id, leader_total = leader
        leader_total = int(leader_total or 0)

        if user_id == leader_user_id:
            await update.message.reply_text("Вы и есть лидер! Сможете удержать этот титул ?)")
            return True
        
        lag = leader_total - user_total # отставание от лидера 
        await update.message.reply_text(
            "Отставание от лидера. Дышишь ему в затылок.\n\n"
            f"👣 Разрыв:\n{lag}\n"
            "это буквально одна прогулка в кофейню.\n\n"
            f"🏁 Лидер:\n{leader_total}\n"
            "ходит так, будто ему за это платят.\n\n"
            f"🚶‍♂️ Ты:\n{user_total}\n"
            "почти там же, не расслабляйся\n\n"
            "Вердикт:\n"
            "Лидер ещё впереди, но уже нервно оглядывается.\n"
            "Пара лишних кругов — и таблички поменяются местами."
        )
        return True

    if text == "Банк":
        await handle_bank(update, context)
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

async def handle_bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = database.get_user_id(user.id)
    if user_id is None:
        await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
        return

    date_from = CHALLENGE_START_DATE_MSK.isoformat()
    date_to = CHALLENGE_END_DATE_MSK.isoformat()

    total_minus, user_minus = database.get_bank_stats(date_from, date_to, user_id)
    total_bank = total_minus * DAILY_PENALTY_RUB
    user_bank = user_minus * DAILY_PENALTY_RUB
    user_pct = (user_bank / total_bank * 100) if total_bank > 0 else 0

    msg = (
        f"Общий банк: {total_bank} ₽\n"
        f"Твой вклад: {user_bank} ₽ ({user_pct:.1f}%)"
    )
    await update.message.reply_text(msg)

async def invite_chat_broadcast_job(context: ContextTypes.DEFAULT_TYPE):
    users = database.get_active_users()
    text = (
        "Ребята, хотим обсуждать результаты, ошибки, доработки и всё остальное "
        "по челленджу в одном месте. Для этого сделали отдельный чат — очень "
        f"просим всех туда вступить: {INVITE_CHAT_URL}"
    )

    for _, tg_id, _, _, _, _ in users:
        await safe_send_message(context.bot, tg_id, text)

async def fundraiser_broadcast_job(context: ContextTypes.DEFAULT_TYPE):
    users = database.get_active_users()
    text = (
        "ВАЖНО: ПРЕДЫДУЩИЕ ССЫЛКИ НЕДЕЙСТВИТЕЛЬНЫ\n\n"
        "Наш замечательный челлендж подходит к концу. "
        "Вы знаете сумму своих штрафов, а теперь ещё знаете ссылку на сбор, "
        f"куда эту сумму можно перевести: {FUNDRAISER_URL}\n\n"
        "Для вашего удобства ещё и Сбер сделали! "
        f"тык: {FUNDRAISER_URL_2}"
    )

    for _, tg_id, _, _, _, _ in users:
        await safe_send_message(context.bot, tg_id, text)



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
            try:
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=(
                        "Напоминание.\n"
                        "Шаги за сегодня сами себя не внесут.\n"
                        "До 23:59 ещё можно спастись."
                    ),
                    reply_markup=MAIN_KEYBOARD
                )
            except TelegramError as e:
                logger.warning("Send telegram error chat_id=%s err=%s", tg_id, e)

async def final_report_job(context: ContextTypes.DEFAULT_TYPE):
    text = build_final_report_message()
    users = database.get_active_users()
    for _, tg_id, _, _, _, _ in users:
        await safe_send_message(context.bot, tg_id, text)

           


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
    now = now_msk()

    if now < INVITE_SEND_AT:
        app.job_queue.run_once(invite_chat_broadcast_job, when=INVITE_SEND_AT)

    if now < FUND_SEND_AT:
        app.job_queue.run_once(fundraiser_broadcast_job, when=FUND_SEND_AT)

    FINAL_REPORT_AT = datetime.datetime(2026, 4, 1, 0, 0, 0, tzinfo=MSK)
    if now < FINAL_REPORT_AT:
        app.job_queue.run_once(final_report_job, when=FINAL_REPORT_AT)

    app.job_queue.run_daily(
        reminder_job,
        time=time(hour=22, minute=0, second=0, tzinfo=MSK)
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

