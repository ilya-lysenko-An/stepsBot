from __future__ import annotations  # совместимость аннотаций с Python 3.8

import datetime
import logging
import os
import random
from datetime import time
from logging.handlers import RotatingFileHandler

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import NetworkError, TelegramError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import config
import database
import phrases

# token.env уже загружен в config (по пути рядом с кодом), здесь только читаем.
TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Технические логи (ошибки/предупреждения/инфо) — в файл с ротацией, чтобы переживали перезапуск.
_bot_file_handler = RotatingFileHandler(
    config.path("bot.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8"
)
_bot_file_handler.setLevel(logging.INFO)
_bot_file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logger.addHandler(_bot_file_handler)

# Отдельный «аудит-лог»: участие, шаги и действия организатора — в events.log.
events = logging.getLogger("events")
events.setLevel(logging.INFO)
events.propagate = False  # не дублировать в консоль/технический лог
_events_file_handler = RotatingFileHandler(
    config.path("events.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8"
)
_events_file_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
events.addHandler(_events_file_handler)

logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("telegram").setLevel(logging.ERROR)
logging.getLogger("apscheduler").setLevel(logging.ERROR)

MSK = config.MSK

# Активные месяцы идут подряд (сентябрь–ноябрь), поэтому стрик и нарушения
# считаем по одному сплошному диапазону.
ACTIVE_RANGE = (
    config.season_by_name(config.ACTIVE_SEASONS[0])["date_from"],
    config.season_by_name(config.ACTIVE_SEASONS[-1])["date_to"],
)


# ===================== клавиатуры =====================

JOIN_KEYBOARD = ReplyKeyboardMarkup([["УЧАСТВУЮ"]], resize_keyboard=True)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["Ввести шаги", "Меню"]],
    resize_keyboard=True
)

MENU_KEYBOARD = ReplyKeyboardMarkup(
    [["Редактировать шаги", "Уведомления"],
     ["Статистика", "Оплата"],
     ["Назад"]],
    resize_keyboard=True
)

STATS_KEYBOARD = ReplyKeyboardMarkup(
    [["Мой статус", "Мои бонусы"],
     ["Участники", "Моя активность"],
     ["Назад"]],
    resize_keyboard=True
)


def build_notify_keyboard(notifications_enabled: int):
    btn = "Выкл уведомления" if notifications_enabled else "Вкл уведомления"
    return ReplyKeyboardMarkup([[btn], ["Назад"]], resize_keyboard=True)


# ===================== вспомогательное =====================

def _uname(user) -> str:
    return f"@{user.username}" if getattr(user, "username", None) else "без username"


def _display(username, first_name) -> str:
    name = first_name or "Без имени"
    uname = f"@{username}" if username else "без username"
    return f"{name} ({uname})"


def now_msk() -> datetime.datetime:
    return config.now_msk()


def today_msk() -> datetime.date:
    return config.today_msk()


def parse_ddmm(text: str):
    parts = text.split(".")
    if len(parts) != 2:
        return None

    day_str, month_str = parts
    if not (day_str.isdigit() and month_str.isdigit()):
        return None

    try:
        dt = datetime.date(today_msk().year, int(month_str), int(day_str))
    except ValueError:
        return None

    return dt.isoformat()


def parse_hhmm(text: str):
    parts = (text or "").strip().replace(".", ":").split(":")
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def current_streak(user_id: int) -> int:
    """Сколько дней подряд (заканчивая последним записанным днём) норма выполнена."""
    rows = database.list_user_daily_results(user_id, ACTIVE_RANGE[0], ACTIVE_RANGE[1])
    streak = 0
    for _day, _steps, _on_time, result, _reason in reversed(rows):
        if result == "+":
            streak += 1
        else:
            break
    return streak


def is_allowed_in_season(user_id: int, season) -> bool:
    """
    Допущен ли участник к активному месяцу.

    По умолчанию достаточно быть зарегистрированным и не выбывшим.
    Проверка оплаты включается флагом config.REQUIRE_PAYMENT.
    """
    if season is None or season["type"] != "active":
        return True
    if database.get_out_of_game(user_id):
        return False
    if not config.REQUIRE_PAYMENT:
        return True
    return bool(database.get_payment(user_id, season["name"]))


def evaluate_result(steps_value: int, goal: int) -> tuple:
    """Норма считается по числу шагов. Позднее внесение не штрафуется."""
    if steps_value < goal:
        return "-", "lt_10k"
    return "+", "ok"


def resolve_day_with_bonus(user_id: int, season, steps: int, current):
    """
    Применяет правила дня к одной записи.

    Возвращает (result, reason, bonus_used, violation, action, remaining_balance), где action:
      - "passive"     : пассивный месяц, норма не проверяется
      - "not_allowed" : активный месяц, но участник не допущен (не оплатил или выбыл)
      - "ok"          : норма выполнена
      - "refunded"    : норма выполнена, ранее списанный за этот день бонус возвращён
      - "used"        : норма не выполнена, списан 1 бонус, день засчитан
      - "kept"        : день уже был покрыт бонусом ранее (повторно не списываем)
      - "no_bonus"    : норма не выполнена и бонусов нет — выбытие
    """
    prev_bonus_used = current[7] if (current and len(current) > 7) else 0

    if season is None or season["type"] == "passive":
        return "+", "ok", 0, 0, "passive", database.get_bonus_balance(user_id)

    goal = season["daily_goal"]
    base_result, base_reason = evaluate_result(steps, goal)

    if not is_allowed_in_season(user_id, season):
        # Шаги записываем честно, но бонусы не трогаем и о выбытии не сообщаем.
        violation = 1 if base_result == "-" else 0
        return base_result, base_reason, 0, violation, "not_allowed", database.get_bonus_balance(user_id)

    if base_result == "+":
        if prev_bonus_used:
            balance = database.adjust_bonus_balance(
                user_id, +1, reason="day_off_refund", day_msk=None
            )
            return "+", "ok", 0, 0, "refunded", balance
        return "+", "ok", 0, 0, "ok", database.get_bonus_balance(user_id)

    # норма не выполнена
    if prev_bonus_used:
        return "+", "bonus", 1, 0, "kept", database.get_bonus_balance(user_id)

    balance = database.get_bonus_balance(user_id)
    if balance > 0:
        remaining = database.adjust_bonus_balance(user_id, -1, reason="day_off_used")
        return "+", "bonus", 1, 0, "used", remaining

    return base_result, base_reason, 0, 1, "no_bonus", 0


def bonus_reply_text(action: str, remaining: int, name: str, steps: int,
                     streak: int, season, user_id: int) -> str:
    if action == "passive":
        if season is None:
            return f"Записал {steps} шагов. Сейчас межсезонье — норма не проверяется."
        avg, _days = database.get_season_avg(user_id, season["date_from"], season["date_to"])
        return phrases.steps_passive(name, steps, avg, season["passive_avg_threshold"])
    if action == "not_allowed":
        return phrases.steps_not_allowed(season, bool(database.get_out_of_game(user_id)))
    if action == "ok":
        return phrases.steps_ok(name, steps, streak)
    if action == "used":
        return phrases.steps_bonus_used(remaining)
    if action == "kept":
        return phrases.steps_bonus_kept(remaining)
    if action == "refunded":
        return phrases.steps_bonus_refunded(remaining)
    if action == "no_bonus":
        return phrases.steps_out()
    return "Записал."


async def safe_send_message(bot, chat_id: int, text: str, reply_markup=None) -> bool:
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        return True
    except (TimedOut, NetworkError) as e:
        logger.warning("Send timeout/network chat_id=%s err=%s", chat_id, e)
        return False
    except TelegramError as e:
        logger.warning("Send telegram error chat_id=%s err=%s", chat_id, e)
        return False


async def is_subscribed(context: ContextTypes.DEFAULT_TYPE, tg_user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(config.CHANNEL_ID, tg_user_id)
        return member.status in ("member", "administrator", "creator")
    except TelegramError:
        return False


# ===================== тексты =====================

def paid_months_line(user_id: int):
    """
    Строка об оплатах или None.

    Если допуск по оплате выключен и организатор ничего не отмечал, строку
    не показываем — она бы только путала: участие от оплаты не зависит.
    """
    payments = database.get_payments(user_id)
    if not config.REQUIRE_PAYMENT and not any(payments.values()):
        return None
    parts = []
    for month in config.ACTIVE_SEASONS:
        mark = "✅" if payments[month] else "—"
        parts.append(f"{mark} {config.MONTH_RU[month]}")
    return " · ".join(parts)


def build_status_text(user_id: int) -> str:
    """Краткий статус — /status."""
    out, reason = database.get_out_state(user_id)
    bonus_balance = database.get_bonus_balance(user_id)
    season = config.current_season()

    lines = [
        "Твой статус:",
        "",
        phrases.status_label(out),
    ]
    if out and reason:
        human = {
            "violation": "причина: пропуск без бонуса",
            "unpaid": "причина: не оплачен месяц",
            "manual": "причина: решение организатора",
        }.get(reason)
        if human:
            lines.append(human)
    lines.append(f"🎟 Бонусов «день отдыха»: {bonus_balance}")
    paid_line = paid_months_line(user_id)
    if paid_line:
        lines.append(f"💳 Взнос: {paid_line}")
    if season:
        lines.append(
            f"📅 Сейчас: {config.MONTH_RU[season['name']]} "
            f"({'активный' if season['type'] == 'active' else 'пассивный'} месяц)"
        )
    return "\n".join(lines)


def build_stats_text(user_id: int) -> str:
    """Полная личная статистика — /stats."""
    season = config.current_season()
    day = today_msk()
    out, _reason = database.get_out_state(user_id)
    bonus_balance = database.get_bonus_balance(user_id)
    today_steps = database.get_today_steps(user_id, day.isoformat())
    streak = current_streak(user_id)

    lines = ["📊 Личная статистика", ""]

    if season is None:
        lines.append("Сейчас межсезонье — активного месяца нет.")
        lines.append(f"🎟 Бонусов: {bonus_balance}")
        lines.append(f"Статус: {phrases.status_label(out)}")
        paid_line = paid_months_line(user_id)
        if paid_line:
            lines.append(f"💳 Взнос: {paid_line}")
        return "\n".join(lines)

    month_ru = config.MONTH_RU[season["name"]]
    avg, days_recorded = database.get_season_avg(user_id, season["date_from"], season["date_to"])
    days_left = config.days_left_in_season(season, day)

    lines.append(f"Месяц: {month_ru}")
    lines.append(f"👟 Шагов за сегодня: {today_steps}")
    lines.append(f"📈 Среднее в {config.MONTH_RU_PREP[season['name']]}: {avg} (дней записано: {days_recorded})")
    if season["type"] == "active":
        lines.append(f"🎯 Норма дня: {season['daily_goal']}")
        lines.append(f"🔥 Стрик: {streak} {phrases.days_word(streak)}")
    else:
        lines.append(f"🎯 Бонусный порог среднего: {season['passive_avg_threshold']}")
    lines.append(f"🎟 Бонусов: {bonus_balance}")
    lines.append(f"Статус: {phrases.status_label(out)}")
    paid_line = paid_months_line(user_id)
    if paid_line:
        lines.append(f"💳 Взнос: {paid_line}")
    lines.append(f"⏳ До конца месяца: {days_left} {phrases.days_word(days_left)}")
    lines.append("")
    lines.append(phrases.stats_bonus_comment(bonus_balance))
    lines.append(phrases.stats_tail(out, days_left))
    return "\n".join(lines)


BONUS_REASON_RU = {
    "passive_july": "бонус за июль",
    "passive_august": "бонус за август",
    "passive_both": "бонус за оба пассивных месяца",
    "day_off_used": "списан «день отдыха»",
    "day_off_refund": "возврат «дня отдыха»",
    "admin_add": "начислено организатором",
    "admin_remove": "списано организатором",
}


def build_bonuses_text(user_id: int) -> str:
    bonus_balance = database.get_bonus_balance(user_id)
    log = database.get_bonus_log(user_id)
    used_days = database.get_user_bonus_days(user_id)

    lines = [
        "🎟 Бонусы «день отдыха»",
        "",
        f"Доступно сейчас: {bonus_balance}",
    ]

    if log:
        lines.append("\nИстория:")
        for delta, reason, day_iso, created_at in log:
            sign = "+" if delta > 0 else ""
            when = created_at[:10] if created_at else ""
            label = BONUS_REASON_RU.get(reason, reason)
            try:
                when = datetime.date.fromisoformat(when).strftime("%d.%m.%Y")
            except ValueError:
                pass
            lines.append(f"• {when} — {sign}{delta} ({label})")
    else:
        lines.append("\nДвижений по бонусам пока не было.")

    if used_days:
        lines.append("\nДни, закрытые бонусом:")
        for day_iso, _steps in used_days:
            lines.append("• " + datetime.date.fromisoformat(day_iso).strftime("%d.%m.%Y"))

    lines.append("\n" + phrases.stats_bonus_comment(bonus_balance))
    return "\n".join(lines)


def build_leaders_text() -> str:
    season = config.current_season()
    total = database.count_total_users()
    in_game = database.count_in_game()
    out = database.count_out_of_game()
    days_left = config.days_left_in_season(season) if season else 0

    lines = [phrases.leaders_header(total, in_game, out, days_left), ""]

    users = database.get_in_game_users()
    if not users:
        lines.append("Активных участников пока нет.")
        return "\n".join(lines)

    # Список без шагов — чтобы не создавать гонку внутри месяца (п.2.4 ТЗ).
    ranked = sorted(
        ((uid, username, first_name, current_streak(uid))
         for uid, _tg_id, username, first_name in users),
        key=lambda r: r[3],
        reverse=True
    )

    lines.append("В игре (топ по стрику без нарушений):")
    for idx, (_uid, username, first_name, streak) in enumerate(ranked[:30], start=1):
        lines.append(f"{idx}) {_display(username, first_name)} — 🔥 {streak}")
    if len(ranked) > 30:
        lines.append(f"…и ещё {len(ranked) - 30}")
    return "\n".join(lines)


def build_register_text() -> str:
    lines = [
        "🍁 Осенний челлендж 2026",
        "",
        "Как попасть в игру: нажать /start и кнопку «УЧАСТВУЮ». Всё.",
        "",
        "Активные месяцы и нормы:",
    ]
    for month in config.ACTIVE_SEASONS:
        s = config.season_by_name(month)
        lines.append(f"• {config.MONTH_RU[month]} — {s['daily_goal']} шагов в день")

    lines += [
        "",
        "Правила коротко:",
        "• каждый день вносишь шаги до полуночи;",
        "• не выполнил норму и нет бонуса «день отдыха» — выбываешь из розыгрыша;",
        "• бонусы списываются автоматически, следить не нужно;",
        "• в розыгрыше в конце ноября участвуют все, кто дошёл до конца и не выбыл.",
    ]

    if config.REQUIRE_PAYMENT:
        lines += [
            "",
            f"💳 Взнос: {config.ENTRY_FEE} ₽ за каждый месяц, до 1-го числа.",
            "Без отметки оплаты к месяцу не допускают.",
        ]
        if config.PAYMENT_URL:
            lines.append(f"Оплата: {config.PAYMENT_URL}")
        lines.append(f"Чек присылать: {config.PAYMENT_CONTACT}")
    else:
        lines += [
            "",
            f"💰 Призовой фонд собирается отдельно ({config.ENTRY_FEE} ₽ за месяц) — "
            f"по вопросам взноса пиши {config.PAYMENT_CONTACT}.",
            "На участие в челлендже и подсчёт шагов это не влияет.",
        ]
        if config.PAYMENT_URL:
            lines.append(f"Ссылка на перевод: {config.PAYMENT_URL}")

    return "\n".join(lines)


# ===================== /start и регистрация =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["menu"] = "main"
    context.user_data["state"] = None

    user = update.effective_user
    user_id = database.get_user_id(user.id)
    season = config.current_season()

    if user_id is not None:
        out = database.get_out_of_game(user_id)
        bonus_balance = database.get_bonus_balance(user_id)
        await update.message.reply_text(
            phrases.greeting(user.first_name or "бегун", season, out, bonus_balance),
            reply_markup=MAIN_KEYBOARD
        )
        return

    goals = " / ".join(
        f"{config.MONTH_RU[m]} — {config.season_by_name(m)['daily_goal']}"
        for m in config.ACTIVE_SEASONS
    )
    long_text = (
        "👋 Добро пожаловать в Begogram Steps Challenge — осенний сезон!\n\n"
        "🗓 Активные месяцы: сентябрь, октябрь, ноябрь\n"
        f"🎯 Норма шагов растёт по месяцам: {goals}\n\n"
        "🤖 Как участвовать:\n"
        "• жмёшь «УЧАСТВУЮ» — и ты в игре, больше ничего не нужно;\n"
        "• каждый день до полуночи вносишь шаги в бота;\n"
        "• бот напомнит вечером, если забыл;\n"
        "• ошибся — можно отредактировать позже.\n\n"
        "🚫 Выбытие из розыгрыша:\n"
        "• не выполнил норму и нет бонуса «день отдыха» — выбыл;\n"
        "• пропустил день без бонуса — тоже выбыл.\n\n"
        "🎟 Бонусы «день отдыха»:\n"
        "• начисляются за пассивные месяцы (июль, август) при среднем "
        f"от {config.PASSIVE_AVG_THRESHOLD} шагов;\n"
        "• позволяют пропустить день без выбытия;\n"
        "• списываются автоматически.\n\n"
        "💰 Розыгрыш в конце ноября:\n"
        "• участвуют все, кто дошёл до конца и не выбыл;\n"
        "• 3 победителя выбираются случайно;\n"
        "• 75% банка — победителям поровну, 25% — организаторам.\n\n"
        "Подробности — команда /register.\n"
        "💪 Удачи — и да пребудут с тобой шаги!"
    )

    try:
        with open(config.path("welcome.jpg"), "rb") as photo:
            await update.message.reply_photo(photo=photo)
        await update.message.reply_text(text=long_text, reply_markup=JOIN_KEYBOARD)
    except FileNotFoundError:
        await update.message.reply_text(long_text, reply_markup=JOIN_KEYBOARD)
    except (TimedOut, NetworkError) as e:
        logger.warning("start reply timeout/network error: %s", e)


async def handle_join_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if text == "УЧАСТВУЮ":
        user = update.effective_user

        if not await is_subscribed(context, user.id):
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"Подписаться на {config.CHANNEL_ID}", url=config.CHANNEL_URL)]
            ])
            await update.message.reply_text(
                "Сначала подпишись на канал, потом снова нажми «УЧАСТВУЮ».",
                reply_markup=kb
            )
            return True

        is_new = database.get_user_id(user.id) is None
        database.add_user(
            tg_id=user.id, username=user.username,
            first_name=user.first_name, club=None
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
            "Если клуба нет — напиши «нет».",
            reply_markup=ReplyKeyboardRemove()
        )
        return True

    if context.user_data.get("state") == "awaiting_club":
        club_text = text.strip()
        club = None if club_text.lower() == "нет" else club_text

        user = update.effective_user
        user_id = database.get_user_id(user.id)
        if user_id is None:
            database.add_user(
                tg_id=user.id, username=user.username,
                first_name=user.first_name, club=club
            )
        else:
            database.update_user_club(user_id=user_id, club=club)

        events.info(
            "РЕГИСТРАЦИЯ | tg_id=%s | %s | клуб=%s",
            user.id, _uname(user), club or "нет"
        )
        context.user_data["state"] = None
        await update.message.reply_text(
            "Готово. Ты официально в игре 🫡\n\n"
            "Что дальше:\n"
            "1) Каждый день жми «Ввести шаги»;\n"
            "2) Следи за статусом: «Меню → Статистика» или /stats;\n"
            "3) Не выполнил норму и нет бонуса — выбываешь из розыгрыша.\n\n"
            "Поехали 🚀",
            reply_markup=MAIN_KEYBOARD
        )
        return True

    return False


# ===================== меню =====================

NAV_BUTTONS = {"Меню", "Назад", "Уведомления", "Статистика", "Оплата"}
STATS_BUTTONS = {"Мой статус", "Мои бонусы", "Участники", "Моя активность"}
ACTION_BUTTONS = {"Ввести шаги", "Редактировать шаги"}
NOTIFY_BUTTONS = {"Вкл уведомления", "Выкл уведомления"}

# Нажатие любой кнопки отменяет незавершённый ввод шагов/даты,
# иначе «жду число» перехватывает кнопки меню и статистики.
BUTTON_TEXTS = NAV_BUTTONS | STATS_BUTTONS | ACTION_BUTTONS | NOTIFY_BUTTONS


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

    if text == "Оплата":
        await update.message.reply_text(build_register_text(), reply_markup=MENU_KEYBOARD)
        return True

    if text == "Уведомления":
        context.user_data["menu"] = "notifications"
        user = update.effective_user
        settings = database.get_user_settings(user.id)
        if settings is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».", reply_markup=MENU_KEYBOARD)
            return True

        (notifications_enabled,) = settings
        user_id = database.get_user_id(user.id)
        reminder_time = database.get_reminder_time(user_id)
        await update.message.reply_text(
            "Уведомления. Последний шанс не облажаться.\n\n"
            f"Сейчас напоминание приходит в {reminder_time} (МСК), "
            "если шаги за день ещё не внесены.\n"
            "Поменять время: /reminder ЧЧ:ММ\n\n"
            "🔔 Включить — бот будет напоминать, что шаги сами себя не внесут.\n"
            "🔕 Выключить — ты взрослый человек, сам помнишь.",
            reply_markup=build_notify_keyboard(notifications_enabled)
        )
        return True

    if text == "Статистика":
        context.user_data["menu"] = "stats"
        await update.message.reply_text("Статистика:", reply_markup=STATS_KEYBOARD)
        return True

    return False


async def handle_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = database.get_user_id(update.effective_user.id)
    if user_id is None:
        return False

    if text == "Выкл уведомления":
        database.set_notifications_enabled(user_id, 0)
        context.user_data["menu"] = "notifications"
        await update.message.reply_text("Уведомления отключены.", reply_markup=build_notify_keyboard(0))
        return True

    if text == "Вкл уведомления":
        database.set_notifications_enabled(user_id, 1)
        context.user_data["menu"] = "notifications"
        await update.message.reply_text("Уведомления включены.", reply_markup=build_notify_keyboard(1))
        return True

    return False


async def handle_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if text not in ("Мой статус", "Мои бонусы", "Участники", "Моя активность"):
        return False

    if text == "Участники":
        await update.message.reply_text(build_leaders_text())
        return True

    user_id = database.get_user_id(update.effective_user.id)
    if user_id is None:
        await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
        return True

    if text == "Мой статус":
        await update.message.reply_text(build_status_text(user_id))
    elif text == "Мои бонусы":
        await update.message.reply_text(build_bonuses_text(user_id))
    elif text == "Моя активность":
        await update.message.reply_text(build_stats_text(user_id))
    return True


# ===================== ввод шагов =====================

async def save_steps(update: Update, user_id: int, day: datetime.date, steps: int, edited: bool):
    day_str = day.isoformat()
    season = config.season_for_day(day)
    current = database.get_daily_status(user_id, day_str)

    submitted_on_time = 1 if day == today_msk() else 0
    if current is not None:
        submitted_on_time = current[4]

    result, reason, bonus_used, violation, action, remaining = resolve_day_with_bonus(
        user_id, season, steps, current
    )

    database.upsert_daily_status(
        user_id=user_id, day_msk=day_str, steps_value=steps,
        submitted_on_time=submitted_on_time, result=result, result_reason=reason,
        bonus_used=bonus_used, violation=violation,
        season=season["name"] if season else None
    )
    database.recompute_out_of_game(user_id)

    user = update.effective_user
    events.info(
        "ШАГИ%s | tg_id=%s | %s | %s | шагов=%s | %s (%s) | действие=%s",
        " (редакт)" if edited else "", user.id, _uname(user), day_str, steps, result, reason, action
    )

    text = bonus_reply_text(
        action, remaining, user.first_name or "бегун", steps,
        current_streak(user_id), season, user_id
    )
    await update.message.reply_text(text, reply_markup=MAIN_KEYBOARD)


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

        user_id = database.get_user_id(update.effective_user.id)
        if user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
            return True

        day = today_msk()
        if not config.is_within_challenge(day):
            await update.message.reply_text("Сегодняшний день вне периода челленджа.")
            return True

        context.user_data["state"] = None
        await save_steps(update, user_id, day, steps, edited=False)
        return True

    if state == "awaiting_edit_date":
        day_iso = parse_ddmm(text)
        if not day_iso:
            await update.message.reply_text("Неверный формат. Пример: 05.09")
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
        if not config.is_within_challenge(day):
            await update.message.reply_text("Эта дата вне периода челленджа.")
            return True
        if day > today_msk():
            await update.message.reply_text("Нельзя вносить шаги за будущую дату.")
            return True

        user_id = database.get_user_id(update.effective_user.id)
        if user_id is None:
            await update.message.reply_text("Сначала нажми «УЧАСТВУЮ».")
            return True

        context.user_data["state"] = None
        context.user_data["edit_date"] = None
        await save_steps(update, user_id, day, steps, edited=True)
        return True

    return False


async def handle_actions(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    if text == "Ввести шаги":
        context.user_data["edit_date"] = None
        context.user_data["state"] = "awaiting_steps_today"
        season = config.current_season()
        hint = ""
        if season and season["type"] == "active":
            hint = f" Норма сегодня: {season['daily_goal']}."
        await update.message.reply_text(f"Введи количество шагов за сегодня.{hint}")
        return True

    if text == "Редактировать шаги":
        context.user_data["edit_date"] = None
        context.user_data["state"] = "awaiting_edit_date"
        await update.message.reply_text("Введи дату в формате ДД.ММ.")
        return True

    return False


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or message.text is None:
        return
    text = message.text

    if text in BUTTON_TEXTS and context.user_data.get("state") != "awaiting_club":
        context.user_data["state"] = None
        context.user_data["edit_date"] = None

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

        if text.strip().lower() == "хуй":
            await update.message.reply_text("Богачев его уже тестирует")
        else:
            await update.message.reply_text("Забыл нажать на кнопку")
    except (TimedOut, NetworkError) as e:
        logger.warning("handle_menu timeout/network error: %s", e)


# ===================== команды участника =====================

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = database.get_user_id(update.effective_user.id)
    if user_id is None:
        await update.message.reply_text("Сначала нажми /start и «УЧАСТВУЮ».")
        return
    await update.message.reply_text(build_stats_text(user_id))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = database.get_user_id(update.effective_user.id)
    if user_id is None:
        await update.message.reply_text("Сначала нажми /start и «УЧАСТВУЮ».")
        return
    await update.message.reply_text(build_status_text(user_id))


async def cmd_bonuses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = database.get_user_id(update.effective_user.id)
    if user_id is None:
        await update.message.reply_text("Сначала нажми /start и «УЧАСТВУЮ».")
        return
    await update.message.reply_text(build_bonuses_text(user_id))


async def cmd_leaders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_leaders_text())


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_register_text())


async def cmd_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = database.get_user_id(update.effective_user.id)
    if user_id is None:
        await update.message.reply_text("Сначала нажми /start и «УЧАСТВУЮ».")
        return

    args = context.args or []
    if not args:
        current = database.get_reminder_time(user_id)
        await update.message.reply_text(
            f"Напоминание приходит в {current} (МСК), если шаги за день ещё не внесены.\n"
            "Поменять: /reminder 21:30\n"
            "Выключить совсем: «Меню → Уведомления → Выкл уведомления»."
        )
        return

    hhmm = parse_hhmm(args[0])
    if hhmm is None:
        await update.message.reply_text("Не понял время. Формат: /reminder 21:30")
        return

    database.set_reminder_time(user_id, hhmm)
    database.set_notifications_enabled(user_id, 1)
    await update.message.reply_text(f"Готово. Буду напоминать в {hhmm} по Москве.")


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает telegram_id — нужен, чтобы вписать организаторов в ADMIN_IDS."""
    user = update.effective_user
    await update.message.reply_text(
        f"Твой telegram_id: {user.id}\n"
        f"username: {_uname(user)}\n"
        + ("Ты в списке организаторов." if config.is_admin(user.id)
           else "Ты не организатор.")
    )


# ===================== админ-команды =====================

def admin_only(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not config.is_admin(user.id):
            await update.message.reply_text("Команда доступна только организаторам.")
            return
        return await handler(update, context)
    return wrapper


def _resolve_target(ref: str):
    """(user_id, tg_id, username, first_name) или None."""
    return database.find_user(ref)


async def _notify(context, tg_id: int, text: str):
    await safe_send_message(context.bot, tg_id, text)


@admin_only
async def cmd_confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Формат: /confirm_payment <user> <sep|oct|nov>")
        return

    target = _resolve_target(args[0])
    if target is None:
        await update.message.reply_text(f"Участник «{args[0]}» не найден.")
        return

    month = config.normalize_month(args[1])
    if month not in config.ACTIVE_SEASONS:
        await update.message.reply_text("Месяц должен быть sep, oct или nov.")
        return

    user_id, tg_id, username, first_name = target
    database.set_payment(user_id, month, 1)

    # Оплата снимает выбытие, если человек выбыл именно за неоплату.
    out, reason = database.get_out_state(user_id)
    restored = False
    if out and reason == "unpaid":
        database.set_out_of_game(user_id, 0)
        database.recompute_out_of_game(user_id)
        restored = database.get_out_of_game(user_id) == 0

    database.log_admin_action(
        update.effective_user.id, "confirm_payment", user_id,
        f"month={month}, restored={restored}"
    )
    events.info(
        "ОПЛАТА+ | admin=%s | %s | месяц=%s | возврат_в_игру=%s",
        update.effective_user.id, _display(username, first_name), month, restored
    )

    await update.message.reply_text(
        f"✅ Оплата {config.MONTH_RU[month]} отмечена: {_display(username, first_name)}"
        + ("\nУчастник возвращён в игру." if restored else "")
    )
    await _notify(
        context, tg_id,
        f"💳 Оплата за {config.MONTH_RU[month]} подтверждена. "
        + ("Ты снова в игре!" if restored else "Ты допущен к месяцу — удачи!")
    )


@admin_only
async def cmd_unconfirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Формат: /unconfirm_payment <user> <sep|oct|nov>")
        return

    target = _resolve_target(args[0])
    if target is None:
        await update.message.reply_text(f"Участник «{args[0]}» не найден.")
        return

    month = config.normalize_month(args[1])
    if month not in config.ACTIVE_SEASONS:
        await update.message.reply_text("Месяц должен быть sep, oct или nov.")
        return

    user_id, _tg_id, username, first_name = target
    database.set_payment(user_id, month, 0)
    database.log_admin_action(
        update.effective_user.id, "unconfirm_payment", user_id, f"month={month}"
    )
    events.info(
        "ОПЛАТА- | admin=%s | %s | месяц=%s",
        update.effective_user.id, _display(username, first_name), month
    )
    await update.message.reply_text(
        f"❌ Оплата {config.MONTH_RU[month]} снята: {_display(username, first_name)}"
    )


@admin_only
async def cmd_set_out(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text("Формат: /set_out <user>")
        return

    target = _resolve_target(args[0])
    if target is None:
        await update.message.reply_text(f"Участник «{args[0]}» не найден.")
        return

    user_id, tg_id, username, first_name = target
    database.set_out_of_game(user_id, 1, reason="manual")
    database.log_admin_action(update.effective_user.id, "set_out", user_id)
    events.info("ВЫБЫТИЕ (вручную) | admin=%s | %s",
                update.effective_user.id, _display(username, first_name))

    await update.message.reply_text(f"🔴 Выведен из розыгрыша: {_display(username, first_name)}")
    await _notify(context, tg_id, phrases.dropout_notice("manual"))


@admin_only
async def cmd_unset_out(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text("Формат: /unset_out <user>")
        return

    target = _resolve_target(args[0])
    if target is None:
        await update.message.reply_text(f"Участник «{args[0]}» не найден.")
        return

    user_id, tg_id, username, first_name = target
    database.set_out_of_game(user_id, 0)
    database.log_admin_action(update.effective_user.id, "unset_out", user_id)
    events.info("ВОЗВРАТ В ИГРУ | admin=%s | %s",
                update.effective_user.id, _display(username, first_name))

    await update.message.reply_text(
        f"🟢 Возвращён в игру: {_display(username, first_name)}\n"
        "Учти: следующая правка шагов пересчитает статус по нарушениям."
    )
    await _notify(context, tg_id, "🟢 Организатор вернул тебя в розыгрыш. Не подведи!")


@admin_only
async def cmd_add_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _change_bonus(update, context, sign=+1)


@admin_only
async def cmd_remove_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _change_bonus(update, context, sign=-1)


async def _change_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE, sign: int):
    args = context.args or []
    verb = "add_bonus" if sign > 0 else "remove_bonus"
    if not args:
        await update.message.reply_text(f"Формат: /{verb} <user> <count>")
        return

    target = _resolve_target(args[0])
    if target is None:
        await update.message.reply_text(f"Участник «{args[0]}» не найден.")
        return

    count = 1
    if len(args) > 1:
        if not args[1].lstrip("-").isdigit():
            await update.message.reply_text("Количество должно быть числом.")
            return
        count = abs(int(args[1]))
    if count == 0:
        await update.message.reply_text("Количество должно быть больше нуля.")
        return

    user_id, tg_id, username, first_name = target
    reason = "admin_add" if sign > 0 else "admin_remove"
    balance = database.adjust_bonus_balance(user_id, sign * count, reason=reason)
    database.log_admin_action(update.effective_user.id, verb, user_id, f"count={count}")
    events.info("БОНУС %s%s | admin=%s | %s | баланс=%s",
                "+" if sign > 0 else "-", count,
                update.effective_user.id, _display(username, first_name), balance)

    await update.message.reply_text(
        f"🎟 {_display(username, first_name)}: "
        f"{'+' if sign > 0 else '−'}{count}, баланс {balance}."
    )
    await _notify(
        context, tg_id,
        f"🎟 Организатор {'начислил' if sign > 0 else 'списал'} {count} "
        f"{phrases.bonus_word(count)} «день отдыха». Баланс: {balance}."
    )


@admin_only
async def cmd_stats_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = database.get_stats_all()
    season = config.current_season()

    lines = [
        "📋 Общая статистика",
        "",
        f"Всего участников: {stats['total']}",
        f"🟢 В игре: {stats['in_game']}",
        f"🔴 Выбыло: {stats['out']}",
    ]
    if stats["out_reasons"]:
        human = {"violation": "нарушение", "unpaid": "неоплата",
                 "manual": "вручную", "unknown": "причина не указана"}
        detail = ", ".join(
            f"{human.get(k, k)}: {v}" for k, v in sorted(stats["out_reasons"].items())
        )
        lines.append(f"   ({detail})")

    lines.append("")
    lines.append("💳 Оплаты:")
    bank = 0
    for month in config.ACTIVE_SEASONS:
        count = stats["paid"][month]
        fee = config.season_by_name(month)["entry_fee"]
        bank += count * fee
        lines.append(f"• {config.MONTH_RU[month]}: {count} — {count * fee} ₽")
    lines.append(f"Банк на сейчас: {bank} ₽")

    if season:
        lines.append("")
        lines.append(
            f"📅 Месяц: {config.MONTH_RU[season['name']]}, "
            f"осталось {config.days_left_in_season(season)} "
            f"{phrases.days_word(config.days_left_in_season(season))}"
        )

    lines.append("")
    lines.append(f"🎁 Кандидатов в розыгрыш: {len(database.get_draw_candidates())}")
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_admin_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = database.get_admin_log(30)
    if not rows:
        await update.message.reply_text("Журнал пуст.")
        return
    lines = ["🗒 Последние действия организаторов:"]
    for created_at, admin_tg_id, action, target_user_id, details in rows:
        target = database.get_user_row(target_user_id) if target_user_id else None
        who = _display(target[2], target[3]) if target else "—"
        lines.append(f"• {created_at} | {action} | {who} | {details or ''}".rstrip(" |"))
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_run_monthly_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительный запуск проверки перехода месяца."""
    if not config.REQUIRE_PAYMENT:
        await update.message.reply_text(
            "Допуск по оплате выключен (REQUIRE_PAYMENT=0), поэтому проверка ничего не делает.\n"
            "Участвуют все, кто нажал «УЧАСТВУЮ» и не выбыл.\n"
            "Чтобы включить правило из ТЗ, добавь в token.env: REQUIRE_PAYMENT=1"
        )
        return

    args = context.args or []
    if args:
        month = config.normalize_month(args[0])
    else:
        season = config.current_season()
        month = season["name"] if season and season["type"] == "active" else None
        if month is None:
            nxt = config.next_season(season["name"]) if season else None
            month = nxt["name"] if nxt and nxt["type"] == "active" else None

    if month not in config.ACTIVE_SEASONS:
        await update.message.reply_text(
            "Не понял месяц. Формат: /run_monthly_check <sep|oct|nov>"
        )
        return

    dropped = await run_month_gate(context, month)
    database.log_admin_action(
        update.effective_user.id, "run_monthly_check", None,
        f"month={month}, dropped={len(dropped)}"
    )
    if not dropped:
        await update.message.reply_text(
            f"Проверка за {config.MONTH_RU[month]}: все, кто в игре, оплатили. Никто не выбыл."
        )
        return
    lines = [f"Проверка за {config.MONTH_RU[month]}: выбыло {len(dropped)} за неоплату:"]
    lines += [f"• {name}" for name in dropped]
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_run_passive_bonuses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начисление бонусов за пассивные месяцы (июль/август). Идемпотентно."""
    granted = await run_passive_bonuses(context)
    database.log_admin_action(
        update.effective_user.id, "run_passive_bonuses", None, f"granted={len(granted)}"
    )
    if not granted:
        await update.message.reply_text(
            "Новых бонусов за пассивные месяцы не начислено "
            "(либо порог не достигнут, либо уже начислены ранее)."
        )
        return
    lines = ["🎟 Начислены бонусы за пассивные месяцы:"]
    lines += [f"• {name}: +{count}" for name, count in granted]
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_run_final_draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /run_final_draw        — посчитать и показать результат только организатору
    /run_final_draw send   — то же + разослать результат участникам
    """
    args = context.args or []
    broadcast = bool(args) and args[0].lower() in ("send", "рассылка", "broadcast")

    candidates = database.get_draw_candidates()
    if not candidates:
        await update.message.reply_text(
            "Кандидатов нет: никто не оплатил все три месяца, оставшись в игре."
        )
        return

    bank = sum(
        database.count_paid(month) * config.season_by_name(month)["entry_fee"]
        for month in config.ACTIVE_SEASONS
    )
    if bank == 0:
        await update.message.reply_text(
            "⚠️ Банк равен нулю: ни одной оплаты не отмечено.\n"
            "Отметь их через /confirm_payment <user> <sep|oct|nov> и запусти розыгрыш заново — "
            "иначе суммы посчитаются от нуля."
        )
    organizer_total = int(round(bank * config.ORGANIZER_SHARE))
    per_organizer = organizer_total // len(config.ORGANIZERS)
    winners_total = bank - organizer_total

    winners = random.sample(candidates, min(config.WINNERS_COUNT, len(candidates)))
    per_winner = winners_total // len(winners)

    winner_names = [_display(u[2], u[3]) for u in winners]
    database.save_draw_result(bank, per_organizer, per_winner, "; ".join(winner_names))
    database.log_admin_action(
        update.effective_user.id, "run_final_draw", None,
        f"bank={bank}, winners={winner_names}, broadcast={broadcast}"
    )
    events.info("РОЗЫГРЫШ | admin=%s | банк=%s | победители=%s",
                update.effective_user.id, bank, winner_names)

    lines = [
        "🎉 Итоги осеннего челленджа!",
        "",
        f"Кандидатов (оплатили 3 месяца и не выбыли): {len(candidates)}",
        f"💰 Банк: {bank} ₽",
        f"• Организаторам 25%: {organizer_total} ₽ "
        f"({' и '.join(config.ORGANIZERS)} — по {per_organizer} ₽)",
        f"• Победителям 75%: {winners_total} ₽ (по {per_winner} ₽ каждому)",
        "",
        "🏆 Победители:",
    ]
    lines += [f"{i}) {name}" for i, name in enumerate(winner_names, start=1)]
    if len(winners) < config.WINNERS_COUNT:
        lines.append(f"\n⚠️ Кандидатов меньше {config.WINNERS_COUNT}, "
                     f"банк поделён на {len(winners)}.")
    result_text = "\n".join(lines)

    await update.message.reply_text(
        result_text + ("" if broadcast else "\n\nЧтобы разослать участникам: /run_final_draw send")
    )

    if not broadcast:
        return

    sent = 0
    for _uid, tg_id, _username, _first_name in database.get_in_game_users():
        if await safe_send_message(context.bot, tg_id, result_text):
            sent += 1
    for _uid, tg_id, _u, _f in winners:
        await safe_send_message(
            context.bot, tg_id,
            f"🏆 Поздравляем! Ты в числе победителей. Твоя доля: {per_winner} ₽."
        )
    await update.message.reply_text(f"Разослано участникам: {sent}.")


@admin_only
async def cmd_admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛠 Команды организатора\n\n"
        "Оплаты:\n"
        "/confirm_payment <user> <sep|oct|nov>\n"
        "/unconfirm_payment <user> <sep|oct|nov>\n\n"
        "Статусы:\n"
        "/set_out <user>\n"
        "/unset_out <user>\n\n"
        "Бонусы:\n"
        "/add_bonus <user> <count>\n"
        "/remove_bonus <user> <count>\n"
        "/run_passive_bonuses — начислить за июль/август по порогу\n\n"
        "Процессы:\n"
        "/run_monthly_check [месяц] — проверка допуска по оплате\n"
        "/run_final_draw [send] — розыгрыш (без send — только показать)\n\n"
        "Отчёты:\n"
        "/stats_all — сводка\n"
        "/admin_log — журнал действий\n\n"
        "<user> — это @username, telegram_id или внутренний id."
    )


# ===================== фоновые задачи =====================

async def run_month_gate(context: ContextTypes.DEFAULT_TYPE, month: str):
    """Выбытие тех, кто в игре, но не оплатил месяц. Возвращает список имён."""
    dropped = []
    for user_id, tg_id, username, first_name in database.get_unpaid_in_game_users(month):
        database.set_out_of_game(user_id, 1, reason="unpaid")
        dropped.append(_display(username, first_name))
        events.info("ВЫБЫТИЕ (неоплата) | %s | месяц=%s",
                    _display(username, first_name), month)
        await safe_send_message(context.bot, tg_id, phrases.dropout_notice("unpaid"))
    return dropped


async def run_passive_bonuses(context: ContextTypes.DEFAULT_TYPE):
    """
    Бонусы за пассивные месяцы: по 1 за каждый месяц со средним >= порога
    и ещё 1, если засчитаны оба. Новички без данных за июль получают только августовский.
    """
    granted = []
    for user_id, tg_id, username, first_name, _club, _notify_on in database.get_active_users():
        earned = {}
        for month in config.PASSIVE_SEASONS:
            season = config.season_by_name(month)
            avg, days = database.get_season_avg(user_id, season["date_from"], season["date_to"])
            earned[month] = days > 0 and avg >= season["passive_avg_threshold"]

        added = 0
        for month, ok in earned.items():
            reason = f"passive_{month}"
            if ok and not database.has_bonus_reason(user_id, reason):
                database.adjust_bonus_balance(user_id, +1, reason=reason)
                added += 1

        if all(earned.get(m) for m in config.PASSIVE_SEASONS) and \
                not database.has_bonus_reason(user_id, "passive_both"):
            database.adjust_bonus_balance(user_id, +1, reason="passive_both")
            added += 1

        if added:
            granted.append((_display(username, first_name), added))
            balance = database.get_bonus_balance(user_id)
            await safe_send_message(
                context.bot, tg_id,
                f"🎟 Начислено бонусов «день отдыха» за пассивные месяцы: +{added}.\n"
                f"Баланс: {balance}. Они пригодятся осенью."
            )
    return granted


async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """Вечернее напоминание — каждому в его время (по умолчанию 22:00 МСК)."""
    now = now_msk()
    day = now.date()
    if not config.is_within_challenge(day):
        return

    season = config.season_for_day(day)
    hhmm = now.strftime("%H:%M")
    day_str = day.isoformat()

    for user_id, tg_id, bonus_balance in database.get_users_for_reminder(hhmm):
        if season and season["type"] == "active" and not is_allowed_in_season(user_id, season):
            continue
        if database.get_daily_status(user_id, day_str) is not None:
            continue

        goal = season["daily_goal"] if season and season["type"] == "active" else 0
        text = (
            phrases.evening_reminder(bonus_balance, goal) if goal
            else "Напоминание: не забудь внести шаги за сегодня — они считаются в среднее за месяц."
        )
        await safe_send_message(context.bot, tg_id, text, reply_markup=MAIN_KEYBOARD)


async def finalize_day_job(context: ContextTypes.DEFAULT_TYPE):
    """
    После полуночи закрываем вчерашний день для допущенных участников,
    которые не отправили шаги: есть бонус → списываем; бонуса нет → выбытие.
    """
    day = today_msk() - datetime.timedelta(days=1)
    season = config.season_for_day(day)
    if season is None or season["type"] != "active":
        return  # в пассивные месяцы пропуск дня ничем не грозит

    day_str = day.isoformat()
    paid_gate = season["name"] if config.REQUIRE_PAYMENT else None
    for user_id, tg_id, bonus_balance in database.get_users_missing_status_for_day(day_str, paid_gate):
        if bonus_balance > 0:
            remaining = database.adjust_bonus_balance(
                user_id, -1, reason="day_off_used", day_msk=day_str
            )
            database.upsert_daily_status(
                user_id=user_id, day_msk=day_str, steps_value=0,
                submitted_on_time=0, result="+", result_reason="bonus",
                bonus_used=1, violation=0, season=season["name"]
            )
            database.recompute_out_of_game(user_id)
            events.info("ПРОПУСК→БОНУС | tg_id=%s | %s | осталось=%s", tg_id, day_str, remaining)
            await safe_send_message(
                context.bot, tg_id,
                "Ты не отправил шаги за вчера, поэтому я списал 1 бонус «день отдыха» — "
                f"день засчитан, ты в игре. Осталось бонусов: {remaining}."
            )
        else:
            database.upsert_daily_status(
                user_id=user_id, day_msk=day_str, steps_value=0,
                submitted_on_time=0, result="-", result_reason="no_submission",
                bonus_used=0, violation=1, season=season["name"]
            )
            database.recompute_out_of_game(user_id)
            events.info("ВЫБЫТИЕ (пропуск) | tg_id=%s | %s", tg_id, day_str)
            await safe_send_message(context.bot, tg_id, phrases.dropout_notice("violation"))


async def monthly_check_job(context: ContextTypes.DEFAULT_TYPE):
    """В первый день активного месяца выбиваем тех, кто его не оплатил."""
    day = today_msk()
    season = config.season_for_day(day)
    if season is None or season["type"] != "active":
        return
    if day.isoformat() != season["date_from"]:
        return

    if not config.REQUIRE_PAYMENT:
        # Допуск по оплате выключен — просто сообщаем организаторам о старте месяца.
        events.info("ПЕРЕХОД МЕСЯЦА | %s | допуск по оплате выключен", season["name"])
        for admin_id in config.admin_ids():
            await safe_send_message(
                context.bot, admin_id,
                f"📅 Начался {config.MONTH_RU[season['name']]}, "
                f"норма дня — {season['daily_goal']}.\n"
                f"В игре: {database.count_in_game()}. "
                "Допуск по оплате выключен, никто не выбыл."
            )
        return

    dropped = await run_month_gate(context, season["name"])
    events.info("ПЕРЕХОД МЕСЯЦА | %s | выбыло за неоплату=%s", season["name"], len(dropped))

    for admin_id in config.admin_ids():
        await safe_send_message(
            context.bot, admin_id,
            f"📅 Начался {config.MONTH_RU[season['name']]}. "
            f"Выбыло за неоплату: {len(dropped)}.\n"
            + ("\n".join(f"• {n}" for n in dropped) if dropped else "Все оплатили.")
        )


async def payment_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """За 5, 3 и 1 день до конца месяца напоминаем оплатить следующий."""
    if not config.REQUIRE_PAYMENT:
        return

    day = today_msk()
    season = config.season_for_day(day)
    if season is None:
        return

    nxt = config.next_season(season["name"])
    if nxt is None or nxt["type"] != "active":
        return

    days = config.days_left_in_season(season, day)
    if days not in (5, 3, 1):
        return

    deadline = datetime.date.fromisoformat(nxt["date_from"]) - datetime.timedelta(days=1)
    text = phrases.payment_reminder(
        days, nxt["name"], deadline.strftime("%d.%m.%Y"), nxt["entry_fee"]
    )

    sent = 0
    for user_id, tg_id, _username, _first_name in database.get_unpaid_in_game_users(nxt["name"]):
        if await safe_send_message(context.bot, tg_id, text):
            sent += 1
    events.info("НАПОМИНАНИЕ ОБ ОПЛАТЕ | месяц=%s | отправлено=%s", nxt["name"], sent)


# ===================== запуск =====================

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error: %s", context.error)


def main():
    if not TOKEN:
        logger.error("BOT_TOKEN не найден")
        return
    if not config.admin_ids():
        logger.warning("ADMIN_IDS не задан в token.env — админ-команды никому не доступны")

    database.init_db()
    logger.info("База данных инициализирована")

    app = Application.builder().token(TOKEN).build()

    # Напоминания идут по индивидуальному времени, поэтому проверяем раз в минуту.
    app.job_queue.run_repeating(reminder_job, interval=60, first=10)
    app.job_queue.run_daily(finalize_day_job, time=time(hour=0, minute=0, second=5, tzinfo=MSK))
    app.job_queue.run_daily(monthly_check_job, time=time(hour=0, minute=1, second=0, tzinfo=MSK))
    app.job_queue.run_daily(payment_reminder_job, time=time(hour=12, minute=0, second=0, tzinfo=MSK))

    app.add_handler(CommandHandler("start", start))

    # участники
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("bonuses", cmd_bonuses))
    app.add_handler(CommandHandler("leaders", cmd_leaders))
    app.add_handler(CommandHandler("reminder", cmd_reminder))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("myid", cmd_myid))

    # организаторы
    app.add_handler(CommandHandler("confirm_payment", cmd_confirm_payment))
    app.add_handler(CommandHandler("unconfirm_payment", cmd_unconfirm_payment))
    app.add_handler(CommandHandler("set_out", cmd_set_out))
    app.add_handler(CommandHandler("unset_out", cmd_unset_out))
    app.add_handler(CommandHandler("add_bonus", cmd_add_bonus))
    app.add_handler(CommandHandler("remove_bonus", cmd_remove_bonus))
    app.add_handler(CommandHandler("run_monthly_check", cmd_run_monthly_check))
    app.add_handler(CommandHandler("run_passive_bonuses", cmd_run_passive_bonuses))
    app.add_handler(CommandHandler("run_final_draw", cmd_run_final_draw))
    app.add_handler(CommandHandler("stats_all", cmd_stats_all))
    app.add_handler(CommandHandler("admin_log", cmd_admin_log))
    app.add_handler(CommandHandler("admin", cmd_admin_help))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    app.add_error_handler(on_error)

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
