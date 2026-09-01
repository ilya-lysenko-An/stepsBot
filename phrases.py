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


def steps_goal_met(steps: int, goal: int, streak: int, bonus_balance: int) -> str:
    """Норма за сегодня набрана."""
    return _pick(
        f"Норма выполнена — {steps} из {goal}. Ты в игре! 🟢",
        f"Отлично, {steps} шагов. Норму закрыл, день твой. 🟢",
        f"Ещё один день в копилку стабильности. Стрик: {streak} {days_word(streak)}. 🔥",
    )


def steps_below_goal(steps: int, goal: int, bonus_balance: int) -> str:
    """Записали меньше нормы, но день ещё идёт — время дошагать есть."""
    left = goal - steps
    tail = (
        f"Не успеешь — спишется бонус «день отдыха», их у тебя {bonus_balance}."
        if bonus_balance > 0
        else "Бонусов у тебя нет, так что до полуночи лучше добрать."
    )
    return _pick(
        f"Записал {steps}. До нормы ещё {left} — день не закончился, успеешь.\n{tail}",
        f"Пока {steps} из {goal}. Осталось {left}, время до полуночи есть.\n{tail}",
    )


def steps_recorded_out(steps: int) -> str:
    """Шаги выбывшего: записываем, но на розыгрыш они не влияют."""
    return (
        f"Записал {steps} шагов. Ты выбыл из розыгрыша, так что на распределение "
        "банка они не влияют — но для себя считаются."
    )


def day_closed_with_bonus(steps: int, goal: int, has_record: bool, remaining: int) -> str:
    """Итог дня: норма не набрана, но выручил бонус."""
    what = f"За вчера у тебя {steps} из {goal}" if has_record else "За вчера ты не внёс шаги"
    tail = (
        f"Осталось бонусов: {remaining}."
        if remaining else "Бонусов больше нет — дальше только по норме."
    )
    return f"{what}, поэтому я списал 1 бонус «день отдыха». День засчитан, ты в игре.\n{tail}"


def day_closed_dropout(steps: int, goal: int, has_record: bool) -> str:
    """Итог дня: нормы нет и бонусов нет."""
    what = f"За вчера у тебя {steps} из {goal}" if has_record else "За вчера ты не внёс шаги"
    return (
        f"{what}, и бонусов «день отдыха» не осталось.\n\n"
        "К сожалению, вы выбыли из розыгрыша призового фонда. Вы можете продолжать "
        "ходить, но не участвуете в распределении банка. Спасибо, что были с нами!"
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


# ---------- 3.6 уведомление о выбытии ----------

def dropout_notice(reason: str = "violation") -> str:
    return (
        "К сожалению, вы выбыли из розыгрыша призового фонда. "
        "Вы можете продолжать ходить, но не участвуете в распределении банка. "
        "Спасибо, что были с нами!"
    )


# ---------- напоминание о шагах ----------

def evening_reminder(bonus_balance: int, goal: int, steps_so_far: int = 0) -> str:
    if bonus_balance > 0:
        tail = f"Если не успеешь — спишется бонус «день отдыха» (их у тебя {bonus_balance})."
    else:
        tail = "Бонусов у тебя нет — недобор означает выбытие из розыгрыша."

    if steps_so_far > 0:
        head = (
            f"Напоминание.\nУ тебя записано {steps_so_far} из {goal} — "
            f"до нормы ещё {goal - steps_so_far}."
        )
    else:
        head = f"Напоминание.\nШаги за сегодня сами себя не внесут, норма — {goal}."

    return f"{head}\n{tail}\nДо 23:59 ещё можно успеть."
