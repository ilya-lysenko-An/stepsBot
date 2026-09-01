"""Конфигурация осеннего челленджа 2026.

Здесь собрано всё, что организатор может захотеть поменять между сезонами:
даты, цели по шагам, взнос, список организаторов, реквизиты оплаты.
"""

from __future__ import annotations

import datetime
import os

from dotenv import load_dotenv

# Конфиг читает переменные окружения на импорте, поэтому .env грузим здесь,
# а не в bot.py — иначе константы оплаты собрались бы из пустого окружения.
load_dotenv("token.env")

# Москва — фиксированный UTC+3 (перевода часов в РФ нет с 2014 г.).
MSK = datetime.timezone(datetime.timedelta(hours=3), "MSK")

CHALLENGE_YEAR = 2026

PASSIVE_AVG_THRESHOLD = 15_000   # среднее за пассивный месяц для начисления бонуса
ENTRY_FEE = 1_000                # взнос за один активный месяц, руб.

# Сезоны челленджа. Порядок важен: он же порядок месяцев.
SEASONS = [
    {
        "name": "july", "type": "passive",
        "date_from": "2026-07-01", "date_to": "2026-07-31",
        "daily_goal": 0, "passive_avg_threshold": PASSIVE_AVG_THRESHOLD, "entry_fee": 0,
    },
    {
        "name": "august", "type": "passive",
        "date_from": "2026-08-01", "date_to": "2026-08-31",
        "daily_goal": 0, "passive_avg_threshold": PASSIVE_AVG_THRESHOLD, "entry_fee": 0,
    },
    {
        "name": "september", "type": "active",
        "date_from": "2026-09-01", "date_to": "2026-09-30",
        "daily_goal": 10_000, "passive_avg_threshold": 0, "entry_fee": ENTRY_FEE,
    },
    {
        "name": "october", "type": "active",
        "date_from": "2026-10-01", "date_to": "2026-10-31",
        "daily_goal": 11_000, "passive_avg_threshold": 0, "entry_fee": ENTRY_FEE,
    },
    {
        "name": "november", "type": "active",
        "date_from": "2026-11-01", "date_to": "2026-11-30",
        "daily_goal": 12_000, "passive_avg_threshold": 0, "entry_fee": ENTRY_FEE,
    },
]

PASSIVE_SEASONS = [s["name"] for s in SEASONS if s["type"] == "passive"]
ACTIVE_SEASONS = [s["name"] for s in SEASONS if s["type"] == "active"]

CHALLENGE_START = SEASONS[0]["date_from"]
CHALLENGE_END = SEASONS[-1]["date_to"]

# Колонка оплаты в users для каждого активного месяца.
PAID_COLUMN = {
    "september": "paid_september",
    "october": "paid_october",
    "november": "paid_november",
}

# То, что организатор может набрать в админ-команде вместо полного имени месяца.
MONTH_ALIASES = {
    "sep": "september", "sept": "september", "september": "september",
    "09": "september", "9": "september", "сен": "september", "сентябрь": "september",
    "oct": "october", "october": "october",
    "10": "october", "окт": "october", "октябрь": "october",
    "nov": "november", "november": "november",
    "11": "november", "ноя": "november", "ноябрь": "november",
    "jul": "july", "july": "july", "07": "july", "7": "july", "июль": "july",
    "aug": "august", "august": "august", "08": "august", "8": "august", "август": "august",
}

MONTH_RU = {
    "july": "июль", "august": "август",
    "september": "сентябрь", "october": "октябрь", "november": "ноябрь",
}
MONTH_RU_GEN = {
    "july": "июля", "august": "августа",
    "september": "сентября", "october": "октября", "november": "ноября",
}
MONTH_RU_PREP = {
    "july": "июле", "august": "августе",
    "september": "сентябре", "october": "октябре", "november": "ноябре",
}

# ---------- допуск к месяцу ----------
# False (по умолчанию): нажал «УЧАСТВУЮ» — участвуешь. Бот никого не отсеивает
# за неоплату, отметки оплаты организатор ставит вручную и они влияют только
# на расчёт банка.
# True: поведение из ТЗ — без отметки оплаты нет допуска к месяцу и розыгрышу.
# Включается в token.env: REQUIRE_PAYMENT=1
REQUIRE_PAYMENT = (os.getenv("REQUIRE_PAYMENT") or "0").strip().lower() in ("1", "true", "yes", "on")


# ---------- розыгрыш ----------
WINNERS_COUNT = 3
ORGANIZER_SHARE = 0.25           # 25% банка — организаторам
ORGANIZERS = ("Илья", "Максим")  # поровну между ними

# ---------- организаторы (доступ к админ-командам) ----------
# Задаётся в token.env: ADMIN_IDS=123456789,987654321
def admin_ids():
    raw = os.getenv("ADMIN_IDS", "")
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def is_admin(tg_id: int) -> bool:
    return tg_id in admin_ids()


# ---------- каналы и реквизиты ----------
CHANNEL_ID = "@begogram_ch"
CHANNEL_URL = "https://t.me/begogram_ch"
INVITE_CHAT_URL = "https://t.me/+jQApV8d7yuU1YWEy"

# Реквизиты для оплаты. Задаётся в token.env: PAYMENT_URL=...
# Исторически ссылка лежала в ключе URL — поддерживаем оба варианта.
PAYMENT_URL = os.getenv("PAYMENT_URL") or os.getenv("URL") or ""
PAYMENT_CONTACT = os.getenv("PAYMENT_CONTACT") or "@begogram_org"


# ---------- работа с датами и сезонами ----------
def now_msk() -> datetime.datetime:
    return datetime.datetime.now(MSK)


def today_msk() -> datetime.date:
    return now_msk().date()


def season_by_name(name: str):
    for s in SEASONS:
        if s["name"] == name:
            return s
    return None


def season_for_day(day: datetime.date):
    """Сезон, в который попадает дата, или None."""
    iso = day.isoformat()
    for s in SEASONS:
        if s["date_from"] <= iso <= s["date_to"]:
            return s
    return None


def current_season():
    return season_for_day(today_msk())


def next_season(name: str):
    names = [s["name"] for s in SEASONS]
    if name not in names:
        return None
    idx = names.index(name)
    return SEASONS[idx + 1] if idx + 1 < len(SEASONS) else None


def season_dates(season):
    return (
        datetime.date.fromisoformat(season["date_from"]),
        datetime.date.fromisoformat(season["date_to"]),
    )


def days_left_in_season(season, day: datetime.date = None) -> int:
    day = day or today_msk()
    _start, end = season_dates(season)
    return max(0, (end - day).days)


def is_within_challenge(day: datetime.date) -> bool:
    return CHALLENGE_START <= day.isoformat() <= CHALLENGE_END


def normalize_month(raw: str):
    """'sep' / 'сентябрь' / '09' -> 'september'. None, если не распознали."""
    return MONTH_ALIASES.get((raw or "").strip().lower())
