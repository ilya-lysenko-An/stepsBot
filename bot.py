from __future__ import annotations  # совместимость аннотаций с Python 3.8

import logging
from logging.handlers import RotatingFileHandler
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

# Технические логи (ошибки/предупреждения/инфо) — в файл с ротацией, чтобы переживали перезапуск.
_bot_file_handler = RotatingFileHandler(
    "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
)
_bot_file_handler.setLevel(logging.INFO)
_bot_file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logger.addHandler(_bot_file_handler)

# Отдельный «аудит-лог»: участие и запись шагов человеко-читаемыми строками в events.log.
events = logging.getLogger("events")
events.setLevel(logging.INFO)
events.propagate = False  # не дублировать в консоль/технический лог
_events_file_handler = RotatingFileHandler(
    "events.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
)
_events_file_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
events.addHandler(_events_file_handler)


def _uname(user) -> str:
    return f"@{user.username}" if getattr(user, "username", None) else "без username"


logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("telegram").setLevel(logging.ERROR)
logging.getLogger("apscheduler").setLevel(logging.ERROR)

# Москва — фиксированный UTC+3 (перевода часов в РФ нет с 2014 г.),
# поэтому не зависим от zoneinfo/tzdata и работаем на любом Python.
MSK = datetime.timezone(datetime.timedelta(hours=3), "MSK")

REGISTRATION_DEADLINE_MSK = datetime.date(2026, 6, 30)
CHALLENGE_START_DATE_MSK = datetime.date(2026, 6, 1)
CHALLENGE_END_DATE_MSK = datetime.date(2026, 6, 30)
DAILY_TARGET = 10_000
CHANNEL_ID = "@begogram_ch"
CHANNEL_URL = "https://t.me/begogram_ch"
INVITE_CHAT_URL = "https://t.me/+jQApV8d7yuU1YWEy"



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
    [["Мой статус", "Мои бонусы"],
     ["Активные участники", "Моя активность"],
     ["Назад"]],
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


def challenge_range() -> tuple[str, str]:
    return CHALLENGE_START_DATE_MSK.isoformat(), CHALLENGE_END_DATE_MSK.isoformat()


def days_left() -> int:
    return max(0, (CHALLENGE_END_DATE_MSK - today_msk()).days)


def current_streak(user_id: int) -> int:
    """Сколько дней подряд (заканчивая последним записанным днём) норма выполнена (result '+')."""
    date_from, date_to = challenge_range()
    rows = database.list_user_daily_results(user_id, date_from, date_to)
    # rows: (day_msk, steps_value, submitted_on_time, result, result_reason), отсортированы по дате
    streak = 0
    for _day, _steps, _on_time, result, _reason in reversed(rows):
        if result == "+":
            streak += 1
        else:
            break
    return streak


def evaluate_result(submitted_on_time: int, steps_value: int) -> tuple[str, str]:
    if submitted_on_time == 0:
        return "-", "late"
    if steps_value < DAILY_TARGET:
        return "-", "lt_10k"
    return "+", "ok"


def resolve_day_with_bonus(user_id: int, submitted_on_time: int, steps: int, current):
    """
    Применяет логику бонусов «день отдыха» к одному дню.

    Возвращает (result, reason, bonus_used, action, remaining_balance), где action:
      - "ok"       : норма выполнена, бонус не нужен
      - "refunded" : норма выполнена, ранее списанный за этот день бонус возвращён
      - "used"     : норма не выполнена, списан 1 бонус, день засчитан
      - "kept"     : норма не выполнена, день уже был покрыт бонусом ранее (повторно не списываем)
      - "no_bonus" : норма не выполнена и бонусов нет — день не засчитан
    """
    base_result, base_reason = evaluate_result(submitted_on_time, steps)
    prev_bonus_used = current[7] if (current and len(current) > 7) else 0

    if base_result == "+":
        if prev_bonus_used:
            balance = database.adjust_bonus_balance(user_id, +1)  # вернуть бонус, день и так зачтён
            return "+", "ok", 0, "refunded", balance
        return "+", "ok", 0, "ok", database.get_bonus_balance(user_id)

    # норма не выполнена
    if prev_bonus_used:
        # день уже покрыт ранее списанным бонусом — оставляем как есть
        return "+", "bonus", 1, "kept", database.get_bonus_balance(user_id)

    balance = database.get_bonus_balance(user_id)
    if balance > 0:
        remaining = database.adjust_bonus_balance(user_id, -1)
        return "+", "bonus", 1, "used", remaining

    return base_result, base_reason, 0, "no_bonus", 0


def bonus_reply_text(action: str, remaining: int) -> str:
    if action == "ok":
        return "Норма выполнена. Ты в игре! 🟢"
    if action == "used":
        return (
            "Норма не выполнена, но я списал 1 бонус «день отдыха» — день засчитан, ты остаёшься в игре.\n"
            f"Осталось бонусов: {remaining}."
        )
    if action == "kept":
        return (
            "Записал. Этот день уже был покрыт бонусом «день отдыха» ранее.\n"
            f"Бонусов осталось: {remaining}."
        )
    if action == "refunded":
        return (
            "Записал. Норма выполнена — ранее списанный за этот день бонус возвращён, ты снова в игре.\n"
            f"Бонусов: {remaining}."
        )
    if action == "no_bonus":
        return (
            "Норма не выполнена, и бонусов «день отдыха» нет.\n"
            "К сожалению, ты выбыл из розыгрыша призового фонда. "
            "Но можешь продолжать ходить для себя — в следующем месяце будет новый челлендж. 🔴"
        )
    return "Записал."


def build_notify_keyboard(notifications_enabled: int):
    btn = "Выкл уведомления" if notifications_enabled else "Вкл уведомления"
    return ReplyKeyboardMarkup(
        [[btn], ["Назад"]],
        resize_keyboard=True
    )

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
            "Регистрация на июньский челлендж закрыта. Новых участников больше не принимаем."
        )
        return

    context.user_data["menu"] = "main"
    context.user_data["state"] = None

    long_text = (
        "👋 Добро пожаловать в Begogram Steps Challenge!\n\n"
        "Вот всё, что нужно знать, куда ТЫЫЫ попал(а). Располагайся, читай и шагай.\n\n"
        "🗓 Когда: 1–30 июня\n"
        "🚶‍♂️ Что делать: проходить минимум 10 000 шагов в день.\n"
        "Да-да, любой способ движения: прогулки, пробежки, жизнь вне дивана — всё считается.\n\n"
        "🤖 Как участвовать:\n"
        "• каждый день до полуночи вносим шаги в бота;\n"
        "• бот напомнит, если забыл (он суровый, но справедливый);\n"
        "• ошибся или забыл — редактируем позже, всё честно.\n\n"
        "🚫 Штрафов больше нет.\n"
        "Вместо них — выбытие из розыгрыша:\n"
        "• выполнил норму — остаёшься в игре;\n"
        "• не выполнил и нет бонуса «день отдыха» — выбываешь из розыгрыша.\n\n"
        "🎟 Бонусы «день отдыха»:\n"
        "• позволяют пропустить день без выбытия;\n"
        "• если норма не выполнена, бонус списывается автоматически;\n"
        "• сколько бонусов осталось — смотри в «Меню → Статистика → Мой статус».\n\n"
        "💰 Призовой фонд:\n"
        "Делится между участниками, которые не пропустили ни одного дня (с учётом бонусов).\n"
        "Выигрыш не зависит от того, кто прошагал больше — важно просто остаться в игре.\n\n"
        "📊 В «Меню → Статистика»:\n"
        "• Мой статус — в игре ты или выбыл, бонусы, стрик, дни до конца;\n"
        "• Мои бонусы — история бонусов;\n"
        "• Активные участники — кто ещё в игре.\n\n"
        "🤝 Важно:\n"
        "• форс-мажоры бывают — пишешь Максиму или Илье, стараемся решить;\n"
        "• нужно быть подписанным на канал Вестника Бегограма.\n\n"
        "🚶‍♀️ И главное:\n"
        "Шагаем, двигаемся и кайфуем.\n"
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

        # Ничего не подошло — написали текст/цифры без команды или кнопки
        if text.strip().lower() == "хуй":
            await update.message.reply_text("Богачев его уже тестирует")
        else:
            await update.message.reply_text("Забыл нажать на кнопку")
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

        is_new = database.get_user_id(user.id) is None
        database.add_user(
            tg_id=user.id,
            username=user.username,
            first_name=user.first_name,
            club=None
        )
        if is_new:
            events.info(
                "УЧАСТИЕ | tg_id=%s | %s | %s",
                user.id, _uname(user), user.first_name or "без имени"
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

        events.info(
            "РЕГИСТРАЦИЯ | tg_id=%s | %s | клуб=%s",
            user.id, _uname(user), club or "нет"
        )
        context.user_data["state"] = None
        await update.message.reply_text(
            "Готово. Ты официально в игре 🫡\n"
            "Краткий инструктаж, без занудства:\n\n"
            "🚶‍♂️ Каждый день\n"
            "— нажимаешь «Ввести шаги»\n"
            "— вводишь общее число шагов за день\n\n"
            "🎯 Цель\n"
            "— 10 000 шагов в день\n\n"
            "🎟 Не дотянул до нормы?\n"
            "— если есть бонус «день отдыха», он спишется и день засчитается;\n"
            "— если бонусов нет — выбываешь из розыгрыша.\n\n"
            "⏰ Забыл?\n"
            "— бот напомнит вечером в 22:00;\n"
            "— шаги можно внести позже;\n"
            "— но если день закроется без нормы и без бонуса — выбытие.\n\n"
            "📊 В «Меню → Статистика»:\n"
            "— Мой статус — статус, бонусы, стрик;\n"
            "— Мои бонусы — история бонусов;\n"
            "— Активные участники — кто ещё в игре.\n\n"
            "Всё честно. Всё считается.\n"
            "Поехали 🚀",
            reply_markup=MAIN_KEYBOARD)
        return True

    return False


NAV_BUTTONS = {"Меню", "Назад", "Уведомления", "Статистика"}


async def handle_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    # Любой переход по навигации отменяет незавершённый ввод шагов/даты,
    # иначе «жду число» перехватывает кнопки статистики (handle_state идёт раньше).
    if text in NAV_BUTTONS:
        context.user_data["state"] = None
        context.user_data["edit_date"] = None

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
            "(или выбываешь из розыгрыша, как взрослый).",
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


def build_status_text(user_id: int) -> str:
    out = database.get_out_of_game(user_id)
    bonus_balance = database.get_bonus_balance(user_id)
    streak = current_streak(user_id)
    left = days_left()

    status_line = "🔴 Выбыл из розыгрыша" if out else "🟢 В игре"
    return (
        "Твой статус в челлендже:\n\n"
        f"{status_line}\n"
        f"🎟 Бонусов «день отдыха»: {bonus_balance}\n"
        f"🔥 Стрик (дней нормы подряд): {streak}\n"
        f"⏳ До конца челленджа: {left} дн."
    )


def build_bonuses_text(user_id: int) -> str:
    bonus_balance = database.get_bonus_balance(user_id)
    used_days = database.get_user_bonus_days(user_id)

    lines = [
        "Бонусы «день отдыха».\n",
        f"Доступно сейчас: {bonus_balance}",
    ]
    if used_days:
        lines.append("\nИспользованы:")
        for day_iso, _steps in used_days:
            day = datetime.date.fromisoformat(day_iso)
            lines.append(f"• {day.strftime('%d.%m.%Y')}")
    else:
        lines.append("\nПока ни один бонус не использован.")
    return "\n".join(lines)


def build_leaders_text() -> str:
    in_game = database.count_in_game()
    out = database.count_out_of_game()

    users = database.get_in_game_users()
    ranked = sorted(
        ((uid, username, first_name, current_streak(uid)) for uid, username, first_name in users),
        key=lambda r: r[3],
        reverse=True
    )

    lines = [
        "Кто ещё в игре:\n",
        f"🟢 В игре: {in_game}",
        f"🔴 Выбыло: {out}\n",
    ]
    if not ranked:
        lines.append("Активных участников пока нет.")
        return "\n".join(lines)

    lines.append("Активные участники (по стрику):")
    for idx, (_uid, username, first_name, streak) in enumerate(ranked[:30], start=1):
        name = first_name or "Без имени"
        uname = f"@{username}" if username else "без username"
        lines.append(f"{idx}) {name} — {uname} — 🔥 {streak}")
    return "\n".join(lines)


async def handle_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if text == "Мой статус":
        user_id = database.get_user_id(update.effective_user.id)
        if user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
            return True
        await update.message.reply_text(build_status_text(user_id))
        return True

    if text == "Мои бонусы":
        user_id = database.get_user_id(update.effective_user.id)
        if user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
            return True
        await update.message.reply_text(build_bonuses_text(user_id))
        return True

    if text == "Активные участники":
        await update.message.reply_text(build_leaders_text())
        return True

    if text == "Моя активность":
        user_id = database.get_user_id(update.effective_user.id)
        if user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
            return True

        total_steps, _minus_count, avg_steps = database.get_user_activity_stats(user_id)
        bonus_balance = database.get_bonus_balance(user_id)
        streak = current_streak(user_id)

        msg = (
            "Твоя активность. Личный отчёт. Без прикрас.\n\n"
            f"🚶‍♂️ Всего натоптано:\n{total_steps}\n"
            "(да, это реально много)\n\n"
            f"📈 Среднее за день:\n{avg_steps}\n"
            "крепкий, уверенный режим, без сюрпризов\n\n"
            f"🔥 Стрик нормы подряд:\n{streak} дн.\n\n"
            f"🎟 Бонусов «день отдыха»:\n{bonus_balance}\n"
            "списываются при пропуске нормы\n\n"
            "Вывод:\n"
            "Ты не просто ходишь — ты системно изнашиваешь асфальт.\n"
            "Главное — не пропусти день без бонуса. Продолжай в том же духе."
        )
        await update.message.reply_text(msg)
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

        result, reason, bonus_used, action, remaining = resolve_day_with_bonus(
            user_id, submitted_on_time, steps, current
        )

        database.upsert_daily_status(
            user_id=user_id,
            day_msk=day_str,
            steps_value=steps,
            submitted_on_time=submitted_on_time,
            result=result,
            result_reason=reason,
            bonus_used=bonus_used
        )

        date_from, date_to = challenge_range()
        database.recompute_out_of_game(user_id, date_from, date_to)

        logger.info(
            "Шаги сохранены: user_id=%s, day=%s, steps=%s, result=%s, reason=%s, action=%s",
            user_id, day_str, steps, result, reason, action
        )
        events.info(
            "ШАГИ | tg_id=%s | %s | %s | шагов=%s | %s (%s) | действие=%s",
            user.id, _uname(user), day_str, steps, result, reason, action
        )
        context.user_data["state"] = None
        await update.message.reply_text(
            bonus_reply_text(action, remaining),
            reply_markup=MAIN_KEYBOARD
        )
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

        result, reason, bonus_used, action, remaining = resolve_day_with_bonus(
            user_id, submitted_on_time, steps, current
        )

        database.upsert_daily_status(
            user_id=user_id,
            day_msk=day_str,
            steps_value=steps,
            submitted_on_time=submitted_on_time,
            result=result,
            result_reason=reason,
            bonus_used=bonus_used
        )

        date_from, date_to = challenge_range()
        database.recompute_out_of_game(user_id, date_from, date_to)

        logger.info(
            "Шаги сохранены (редакт): user_id=%s, day=%s, steps=%s, result=%s, reason=%s, action=%s",
            user_id, day_str, steps, result, reason, action
        )
        events.info(
            "ШАГИ (редакт) | tg_id=%s | %s | %s | шагов=%s | %s (%s) | действие=%s",
            user.id, _uname(user), day_str, steps, result, reason, action
        )
        context.user_data["state"] = None
        context.user_data["edit_date"] = None
        await update.message.reply_text(
            bonus_reply_text(action, remaining),
            reply_markup=MAIN_KEYBOARD
        )
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
        if database.get_out_of_game(user_id):
            continue  # выбывшим напоминать незачем

        row = database.get_daily_status(user_id, day_str)
        if row is None:
            try:
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=(
                        "Напоминание.\n"
                        "Шаги за сегодня сами себя не внесут.\n"
                        "Не выполнишь норму и не останется бонусов — выбываешь из розыгрыша.\n"
                        "До 23:59 ещё можно спастись."
                    ),
                    reply_markup=MAIN_KEYBOARD
                )
            except TelegramError as e:
                logger.warning("Send telegram error chat_id=%s err=%s", tg_id, e)


async def finalize_day_job(context: ContextTypes.DEFAULT_TYPE):
    """
    После полуночи разбираем вчерашний день для тех, кто не отправил шаги и ещё в игре:
    есть бонус → списываем, день засчитан; бонуса нет → выбытие.
    """
    day = today_msk() - datetime.timedelta(days=1)
    if not is_within_challenge(day):
        return

    day_str = day.isoformat()
    date_from, date_to = challenge_range()
    missing = database.get_users_missing_status_for_day(day_str)

    for user_id, tg_id, bonus_balance in missing:
        if bonus_balance > 0:
            remaining = database.adjust_bonus_balance(user_id, -1)
            database.upsert_daily_status(
                user_id=user_id, day_msk=day_str, steps_value=0,
                submitted_on_time=0, result="+", result_reason="bonus", bonus_used=1
            )
            database.recompute_out_of_game(user_id, date_from, date_to)
            events.info(
                "ПРОПУСК→БОНУС | tg_id=%s | %s | списан 1 бонус, осталось=%s",
                tg_id, day_str, remaining
            )
            await safe_send_message(
                context.bot, tg_id,
                "Ты не отправил шаги за вчера, поэтому я списал 1 бонус «день отдыха» — "
                f"день засчитан, ты в игре. Осталось бонусов: {remaining}."
            )
        else:
            database.upsert_daily_status(
                user_id=user_id, day_msk=day_str, steps_value=0,
                submitted_on_time=0, result="-", result_reason="no_submission", bonus_used=0
            )
            database.recompute_out_of_game(user_id, date_from, date_to)
            events.info(
                "ВЫБЫТИЕ | tg_id=%s | %s | пропуск без бонуса",
                tg_id, day_str
            )
            await safe_send_message(
                context.bot, tg_id,
                "Ты пропустил вчерашний день, и бонусов «день отдыха» не осталось.\n"
                "К сожалению, ты выбыл из розыгрыша призового фонда. "
                "Но можешь продолжать ходить для себя — в следующем месяце будет новый челлендж."
            )


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

