"""Сквозной прогон сценариев на временной базе, без Telegram.

Правила, которые проверяем:
  • участие = нажал «УЧАСТВУЮ», никаких оплат;
  • в течение дня недобор ничем не грозит — можно дошагать;
  • итог подводится ночью по последнему значению за день.
"""
import asyncio, datetime, os, sys, tempfile

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

os.environ["ADMIN_IDS"] = "111"
DB = os.path.join(tempfile.mkdtemp(), "test.db")

import config, database
database.DB_PATH = DB

FAKE_TODAY = datetime.date(2026, 8, 31)
config.now_msk = lambda: datetime.datetime.combine(
    FAKE_TODAY, datetime.time(20, 0), tzinfo=config.MSK
)

import bot

sent = []
class FakeBot:
    async def send_message(self, chat_id, text, reply_markup=None): sent.append((chat_id, text))
class FakeCtx:
    def __init__(self, args=None): self.bot = FakeBot(); self.args = args or []
class FakeMsg:
    def __init__(self): self.replies = []
    async def reply_text(self, text, reply_markup=None): self.replies.append(text)
class FakeUser:
    def __init__(self, tg_id, username, first_name):
        self.id, self.username, self.first_name = tg_id, username, first_name
class FakeUpdate:
    def __init__(self, user):
        self.effective_user = user; self.message = FakeMsg()
        self.effective_message = self.message

def day(d):
    global FAKE_TODAY
    FAKE_TODAY = d

FAILS = []
def ok(label, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + label + (f"  [{extra}]" if extra else ""))
    if not cond: FAILS.append(label)


async def main():
    database.init_db()

    people = [(101, "anna", "Аня"), (102, "boris", "Борис"),
              (103, "vera", "Вера"), (104, "gleb", "Глеб")]
    for tg, un, fn in people:
        database.add_user(tg, un, fn, "клуб")
    uid = {un: database.get_user_id(tg) for tg, un, fn in people}
    admin = FakeUser(111, "org", "Илья")

    def steps_of(user, d):
        row = database.get_daily_status(uid[user], d)
        return row[3] if row else None

    def result_of(user, d):
        row = database.get_daily_status(uid[user], d)
        return row[5] if row else None

    async def submit(user, tg, name, d, steps, edited=False):
        upd = FakeUpdate(FakeUser(tg, user, name))
        await bot.save_steps(upd, uid[user], d, steps, edited=edited)
        return upd.message.replies[-1]

    print("\n=== 1. Пассивные месяцы: бонусы по порогу 15 000 ===")
    def fill(user, d_from, d_to, steps):
        d, end = datetime.date.fromisoformat(d_from), datetime.date.fromisoformat(d_to)
        while d <= end:
            database.upsert_daily_status(uid[user], d.isoformat(), steps, 1, "+", "ok", season="passive")
            d += datetime.timedelta(days=1)
    fill("anna", "2026-07-01", "2026-07-31", 16000)
    fill("anna", "2026-08-01", "2026-08-31", 16000)
    fill("boris", "2026-08-01", "2026-08-31", 16000)
    fill("vera", "2026-07-01", "2026-07-31", 16000)
    fill("vera", "2026-08-01", "2026-08-31", 9000)

    await bot.run_passive_bonuses(FakeCtx())
    ok("Аня: июль + август + оба = 3", database.get_bonus_balance(uid["anna"]) == 3)
    ok("Борис (новичок, только август) = 1", database.get_bonus_balance(uid["boris"]) == 1)
    ok("Вера (только июль) = 1", database.get_bonus_balance(uid["vera"]) == 1)
    ok("Глеб (нет данных) = 0", database.get_bonus_balance(uid["gleb"]) == 0)
    before = database.get_bonus_balance(uid["anna"])
    await bot.run_passive_bonuses(FakeCtx())
    ok("повторный запуск не дублирует", database.get_bonus_balance(uid["anna"]) == before)

    print("\n=== 2. Старт сентября: всем сообщение о норме, никто не выбывает ===")
    day(datetime.date(2026, 9, 1))
    sent.clear()
    await bot.monthly_check_job(FakeCtx())
    ok("никто не выбыл", database.count_out_of_game() == 0)
    ok("участникам ушло сообщение о норме", any("10000" in t for _, t in sent))

    print("\n=== 3. Недобор в течение дня НЕ выбивает ===")
    d1 = "2026-09-01"
    reply = await submit("anna", 101, "Аня", FAKE_TODAY, 3000)
    ok("Аня в игре после 3000 шагов", database.get_out_of_game(uid["anna"]) == 0)
    ok("бонус не списан", database.get_bonus_balance(uid["anna"]) == 3,
       str(database.get_bonus_balance(uid["anna"])))
    ok("бот говорит, сколько осталось", "7000" in reply, reply.split(chr(10))[0])

    reply = await submit("anna", 101, "Аня", FAKE_TODAY, 6000)
    ok("повторный ввод перезаписывает", steps_of("anna", d1) == 6000, str(steps_of("anna", d1)))
    ok("всё ещё в игре", database.get_out_of_game(uid["anna"]) == 0)

    reply = await submit("anna", 101, "Аня", FAKE_TODAY, 11000)
    ok("дошагала до нормы -> засчитано", result_of("anna", d1) == "+")
    ok("в ответе похвала", "выполнена" in reply.lower() or "закрыл" in reply.lower()
       or "копилку" in reply.lower(), reply.split(chr(10))[0])
    ok("бонусы целы", database.get_bonus_balance(uid["anna"]) == 3)

    print("\n=== 4. Ночная проверка подводит итог ===")
    await submit("boris", 102, "Борис", FAKE_TODAY, 4000)   # недобор, бонус 1
    await submit("vera", 103, "Вера", FAKE_TODAY, 4000)     # недобор, бонус 1
    await submit("gleb", 104, "Глеб", FAKE_TODAY, 4000)     # недобор, бонусов 0
    ok("до полуночи никто не выбыл", database.count_out_of_game() == 0)

    day(datetime.date(2026, 9, 2))
    sent.clear()
    counted, bonused, dropped = await bot.close_day(FakeCtx(), datetime.date(2026, 9, 1))
    ok("Аня прошла по норме", counted == 1, f"counted={counted}")
    ok("Борис и Вера закрыты бонусом", bonused == 2, f"bonused={bonused}")
    ok("Глеб выбыл", dropped == 1, f"dropped={dropped}")
    ok("у Бориса бонус списан", database.get_bonus_balance(uid["boris"]) == 0)
    ok("шаги Бориса сохранены, не обнулены", steps_of("boris", d1) == 4000,
       str(steps_of("boris", d1)))
    ok("Глеб помечен выбывшим", database.get_out_state(uid["gleb"]) == (1, "violation"),
       str(database.get_out_state(uid["gleb"])))
    ok("Глебу ушло уведомление", any(c == 104 and "выбыли" in t for c, t in sent))
    ok("в уведомлении Глеба видно 4000 из 10000",
       any(c == 104 and "4000" in t and "10000" in t for c, t in sent))

    print("\n=== 5. Повторный запуск проверки ничего не ломает ===")
    bal_before = database.get_bonus_balance(uid["boris"])
    c2, b2, d2 = await bot.close_day(FakeCtx(), datetime.date(2026, 9, 1))
    ok("бонус повторно не списан", database.get_bonus_balance(uid["boris"]) == bal_before)
    ok("никто не выбыл повторно", d2 == 0, f"dropped={d2}")

    print("\n=== 6. Совсем не вносил шаги -> тоже итог ночью ===")
    day(datetime.date(2026, 9, 3))
    sent.clear()
    counted, bonused, dropped = await bot.close_day(FakeCtx(), datetime.date(2026, 9, 2))
    ok("Аня закрыта бонусом за пропуск", database.get_bonus_balance(uid["anna"]) == 2,
       str(database.get_bonus_balance(uid["anna"])))
    ok("Вера выбыла (бонусов не осталось)",
       database.get_out_of_game(uid["vera"]) == 1)
    ok("выбывший Глеб в проверку не попадает",
       database.get_daily_status(uid["gleb"], "2026-09-02") is None)

    print("\n=== 7. Правка закрытого дня возвращает в игру ===")
    await submit("vera", 103, "Вера", datetime.date(2026, 9, 2), 12000, edited=True)
    ok("Вера вернулась", database.get_out_of_game(uid["vera"]) == 0,
       str(database.get_out_state(uid["vera"])))
    database.set_out_of_game(uid["gleb"], 1, "manual")
    await submit("gleb", 104, "Глеб", datetime.date(2026, 9, 1), 20000, edited=True)
    ok("ручное выбытие правкой не снимается", database.get_out_of_game(uid["gleb"]) == 1)

    print("\n=== 8. Вечернее напоминание ===")
    day(datetime.date(2026, 9, 5))
    config.now_msk = lambda: datetime.datetime(2026, 9, 5, 22, 0, tzinfo=config.MSK)
    await submit("anna", 101, "Аня", datetime.date(2026, 9, 5), 5000)
    await submit("boris", 102, "Борис", datetime.date(2026, 9, 5), 12000)
    sent.clear()
    await bot.reminder_job(FakeCtx())
    got = {c for c, _ in sent}
    ok("напомнили недобравшей Ане", 101 in got, str(got))
    ok("не трогаем выполнившего Бориса", 102 not in got, str(got))
    ok("в напоминании видно 5000 и остаток",
       any(c == 101 and "5000" in t for c, t in sent))
    config.now_msk = lambda: datetime.datetime.combine(
        FAKE_TODAY, datetime.time(20, 0), tzinfo=config.MSK)

    print("\n=== 9. Ручное закрытие дня организатором ===")
    day(datetime.date(2026, 9, 10))
    u = FakeUpdate(admin)
    await bot.cmd_run_daily_check(u, FakeCtx(["2026-09-05"]))
    ok("отчёт по закрытию дня", "закрыт" in u.message.replies[-1], u.message.replies[-1][:40])
    u = FakeUpdate(admin)
    await bot.cmd_run_daily_check(u, FakeCtx(["2026-09-10"]))
    ok("сегодняшний день закрыть нельзя",
       "не закончился" in u.message.replies[-1], u.message.replies[-1][:40])

    print("\n=== 10. Розыгрыш: банк = взнос × число участников ===")
    day(datetime.date(2026, 11, 30))
    for who in ("anna", "boris"):
        database.set_out_of_game(uid[who], 0)
    cand = database.get_draw_candidates()
    ok("кандидаты = все в игре", len(cand) >= 1, str(sorted(c[2] for c in cand)))

    u = FakeUpdate(admin)
    await bot.cmd_run_final_draw(u, FakeCtx())
    text = u.message.replies[-1]
    total = database.count_total_users()
    ok("банк = 1000 × общее число участников", f"Банк: {total * 1000} ₽" in text,
       f"участников={total}, ожидали {total * 1000} ₽")
    ok("видно, как посчитан банк", f"1000 ₽ × {total}" in text)

    u = FakeUpdate(admin)
    await bot.cmd_run_final_draw(u, FakeCtx(["45000"]))
    text = u.message.replies[-1]
    ok("банк можно задать вручную", "45000 ₽" in text)
    ok("организаторам по 5625 ₽", "по 5625 ₽" in text, text)
    ok("победителям 33750 ₽", "33750 ₽" in text)

    print("\n=== 11. Тексты не падают ===")
    for fn, name in [(bot.build_stats_text, "/stats"), (bot.build_status_text, "/status"),
                     (bot.build_bonuses_text, "/bonuses")]:
        try:
            fn(uid["anna"]); ok(f"{name} строится", True)
        except Exception as e:
            ok(f"{name} строится", False, repr(e))
    for fn, name in [(bot.build_leaders_text, "/leaders"), (bot.build_register_text, "/register")]:
        try:
            fn(); ok(f"{name} строится", True)
        except Exception as e:
            ok(f"{name} строится", False, repr(e))
    reg = bot.build_register_text()
    ok("/register объясняет, что можно дошагать", "дошагать" in reg)
    ok("/register не требует оплаты для входа", "нажать /start" in reg)
    await bot.cmd_stats_all(FakeUpdate(admin), FakeCtx()); ok("/stats_all строится", True)
    await bot.cmd_admin_log(FakeUpdate(admin), FakeCtx()); ok("/admin_log строится", True)
    u = FakeUpdate(FakeUser(101, "anna", "Аня"))
    await bot.cmd_myid(u, FakeCtx()); ok("/myid работает", "101" in u.message.replies[-1])

    print("\n" + ("ВСЁ ЗЕЛЁНОЕ ✅" if not FAILS else f"ПРОВАЛЕНО: {FAILS}"))
    return 1 if FAILS else 0

sys.exit(asyncio.run(main()))
