"""Динамические фразы интерфейса (раздел 3 ТЗ).

Каждая функция получает контекст (бонусы, статус, число участников) и
возвращает подходящую фразу. Где вариантов несколько — выбирается случайный,
чтобы бот не выглядел одинаково каждый день.
"""

from __future__ import annotations

import random

import config


def _pick(*variants) -> str:
    return random.choice([v for v in variants if v])


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def days_word(n: int) -> str:
    return _plural(n, "день", "дня", "дней")


def bonus_word(n: int) -> str:
    return _plural(n, "бонус", "бонуса", "бонусов")


def people_word(n: int) -> str:
    return _plural(n, "участник", "участника", "участников")


def status_label(out_of_game: int) -> str:
    return "🔴 Выбыл" if out_of_game else "🟢 В игре"


# ---------- 3.1 приветствие ----------

def greeting(name: str, season, out_of_game: int, bonus_balance: int) -> str:
    month = config.MONTH_RU.get(season["name"], "—") if season else "вне сезона"
    return _pick(
        f"Привет, бегун! Ты в системе осеннего челленджа.\n"
        f"Текущий месяц: {month}. Статус: {status_label(out_of_game)}. "
        f"Бонусов: {bonus_balance}. Удачи!",
        f"С возвращением, {name}! На дворе {month}, ты — {status_label(out_of_game)}. "
        f"В запасе {bonus_balance} {bonus_word(bonus_balance)}. Шагаем!",
    )


# ---------- 3.2 ответ на отправку шагов ----------

def steps_ok(name: str, steps: int, streak: int) -> str:
    return _pick(
        "Молодец! Норма выполнена. Ты в игре! 🟢",
        f"Отлично, {name}! Сегодня ты прошагал {steps:,}".replace(",", " ") + ". Так держать!",
        f"Ещё один день в копилку стабильности. Твой стрик: {streak} {days_word(streak)}. 🔥",
    )


def steps_bonus_used(remaining: int) -> str:
    tail = (
        f"Осталось {remaining} {bonus_word(remaining)}."
        if remaining else "Бонусов больше не осталось — дальше только по норме."
    )
    return _pick(
        f"Норма не выполнена, но у тебя был бонус «день отдыха». Бонус списан, день засчитан. {tail}",
        f"Не дотянул, но выручил бонус «день отдыха» — день засчитан. {tail}",
    )


def steps_bonus_kept(remaining: int) -> str:
    return (
        "Записал. Этот день уже был покрыт бонусом «день отдыха» ранее.\n"
        f"Бонусов осталось: {remaining}."
    )


def steps_bonus_refunded(remaining: int) -> str:
    return (
        "Записал. Норма выполнена — ранее списанный за этот день бонус возвращён.\n"
        f"Бонусов: {remaining}."
    )


def steps_out() -> str:
    return _pick(
        "Увы, ты выбыл из розыгрыша. Можешь продолжать ходить для себя — "
        "в следующем сезоне будет новый шанс. 🔴",
        "Норма не выполнена, и бонусов нет. К сожалению, ты выбыл из розыгрыша "
        "призового фонда. Ходить не бросай — это всё ещё полезно. 🔴",
    )


def steps_passive(name: str, steps: int, avg: int, threshold: int) -> str:
    if avg >= threshold:
        return (
            f"Записал {steps} шагов. Среднее за месяц: {avg} — "
            f"порог {threshold} держишь, бонус за месяц твой. 👌"
        )
    return (
        f"Записал {steps} шагов. Среднее за месяц: {avg}, "
        f"до бонусного порога {threshold} ещё нужно поднажать."
    )


def steps_not_allowed(season, out_of_game: bool) -> str:
    """Шаги записаны, но на розыгрыш не влияют: человек выбыл или не допущен."""
    if out_of_game:
        return (
            "Шаги записал. Ты выбыл из розыгрыша, так что на распределение банка "
            "они не влияют — но для себя считаются."
        )
    month = config.MONTH_RU.get(season["name"], "месяц")
    return (
        f"Шаги записал, но в розыгрыше за {month} ты не участвуешь: "
        f"взнос за этот месяц не отмечен как оплаченный.\n"
        f"Если оплата была — напиши {config.PAYMENT_CONTACT}."
    )


# ---------- 3.3 личная статистика ----------

def stats_bonus_comment(bonus_balance: int) -> str:
    if bonus_balance > 3:
        return "Вау, у тебя целая куча бонусов! Можешь не переживать за пару пропусков."
    if bonus_balance <= 1:
        return "Бонусов осталось мало. Старайся не пропускать дни!"
    return "Бонусы есть, но тратить их без нужды не стоит."


def stats_tail(out_of_game: int, days_left: int) -> str:
    if out_of_game:
        return "Ты вне розыгрыша, но статистика продолжает считаться. Ходи для себя."
    if days_left <= 3:
        return f"Финишная прямая: осталось {days_left} {days_word(days_left)}. Не расслабляйся!"
    return _pick("Продолжай в том же духе!", "Так держать!", "Стабильность — твоё второе имя.")


# ---------- 3.4 общая статистика ----------

def leaders_header(total: int, active: int, out: int, days_left: int) -> str:
    line = (
        f"Всего участников: {total}. В игре: {active}. Выбыло: {out}.\n"
        f"Держитесь, осталось всего {days_left} {days_word(days_left)} до конца месяца!"
    )
    if 0 < active < 10:
        line += (
            f"\n\nНарод, вы — элита! Осталось всего {active} стойких. "
            "Шансы на выигрыш растут!"
        )
    return line


# ---------- 3.5 напоминание об оплате ----------

def payment_reminder(days: int, month: str, deadline_str: str, fee: int) -> str:
    month_ru = config.MONTH_RU.get(month, month)
    return (
        f"Друзья, через {days} {days_word(days)} заканчивается месяц.\n"
        f"Не забудьте оплатить {month_ru} ({fee} руб.), чтобы продолжить борьбу.\n"
        f"Если вы уже оплатили — проигнорируйте. Если нет — сделайте это до {deadline_str}."
    )


# ---------- 3.6 уведомление о выбытии ----------

def dropout_notice(reason: str = "violation") -> str:
    base = (
        "К сожалению, вы выбыли из розыгрыша призового фонда. "
        "Вы можете продолжать ходить, но не участвуете в распределении банка. "
        "Спасибо, что были с нами!"
    )
    if reason == "unpaid":
        return (
            "Взнос за новый месяц не поступил, поэтому вы выбыли из розыгрыша "
            "призового фонда.\n" + base.split("К сожалению, ", 1)[-1].capitalize()
        )
    return base


# ---------- напоминание о шагах ----------

def evening_reminder(bonus_balance: int, goal: int) -> str:
    if bonus_balance > 0:
        tail = (
            f"Если не успеешь — спишется бонус «день отдыха» "
            f"(их у тебя {bonus_balance})."
        )
    else:
        tail = "Бонусов у тебя нет — пропуск означает выбытие из розыгрыша."
    return (
        "Напоминание.\n"
        f"Шаги за сегодня сами себя не внесут, норма — {goal}.\n"
        f"{tail}\n"
        "До 23:59 ещё можно спастись."
    )
