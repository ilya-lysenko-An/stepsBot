"""Сквозной прогон сценариев на временной базе, без Telegram.

По умолчанию допуск по оплате выключен (config.REQUIRE_PAYMENT = False):
участвуют все, кто нажал «УЧАСТВУЮ». Отдельным блоком проверяется, что
строгое правило из ТЗ по-прежнему работает при REQUIRE_PAYMENT = True.
"""
import asyncio, datetime, os, sys, tempfile

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

os.environ["ADMIN_IDS"] = "111"
DB = os.path.join(tempfile.mkdtemp(), "test.db")

import config, database
database.DB_PATH = DB
config.REQUIRE_PAYMENT = False

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
    ok("Аня: июль + август + оба = 3", database.get_bonus_balance(uid["anna"]) == 3,
       str(database.get_bonus_balance(uid["anna"])))
    ok("Борис (новичок, только август) = 1", database.get_bonus_balance(uid["boris"]) == 1)
    ok("Вера (только июль) = 1", database.get_bonus_balance(uid["vera"]) == 1)
    ok("Глеб (нет данных) = 0", database.get_bonus_balance(uid["gleb"]) == 0)
    before = database.get_bonus_balance(uid["anna"])
    await bot.run_passive_bonuses(FakeCtx())
    ok("повторный запуск не дублирует", database.get_bonus_balance(uid["anna"]) == before)

    print("\n=== 2. Старт сентября: никого не выбивает за неоплату ===")
    day(datetime.date(2026, 9, 1))
    sent.clear()
    await bot.monthly_check_job(FakeCtx())
    ok("никто не выбыл", database.count_out_of_game() == 0)
    ok("организаторам ушло уведомление о старте месяца",
       any("Начался сентябрь" in t for _, t in sent))
    ok("в тексте нет слова о выбытии",
       all("выбыл" not in t.lower() or "никто не выбыл" in t.lower() for _, t in sent))

    print("\n=== 3. Шаги идут в зачёт без всякой оплаты ===")
    upd = FakeUpdate(FakeUser(101, "anna", "Аня"))
    await bot.save_steps(upd, uid["anna"], FAKE_TODAY, 12000, edited=False)
    ok("норма выполнена -> '+'", database.get_daily_status(uid["anna"], "2026-09-01")[5] == "+")
    ok("бонусы не тронуты", database.get_bonus_balance(uid["anna"]) == 3)
    ok("ответ без упоминания оплаты", "оплат" not in upd.message.replies[-1].lower(),
       upd.message.replies[-1][:60])

    upd = FakeUpdate(FakeUser(102, "boris", "Борис"))
    await bot.save_steps(upd, uid["boris"], FAKE_TODAY, 4000, edited=False)
    ok("недобор при бонусе -> день засчитан",
       database.get_daily_status(uid["boris"], "2026-09-01")[5] == "+")
    ok("бонус списан", database.get_bonus_balance(uid["boris"]) == 0)

    upd = FakeUpdate(FakeUser(104, "gleb", "Глеб"))
    await bot.save_steps(upd, uid["gleb"], FAKE_TODAY, 4000, edited=False)
    ok("неоплативший Глеб играет по общим правилам -> выбыл за недобор",
       database.get_out_state(uid["gleb"]) == (1, "violation"),
       str(database.get_out_state(uid["gleb"])))

    print("\n=== 4. Выбытие и возврат правкой задним числом ===")
    day(datetime.date(2026, 9, 2))
    upd = FakeUpdate(FakeUser(102, "boris", "Борис"))
    await bot.save_steps(upd, uid["boris"], FAKE_TODAY, 4000, edited=False)
    ok("недобор без бонусов -> выбытие", database.get_out_state(uid["boris"]) == (1, "violation"))
    await bot.save_steps(FakeUpdate(FakeUser(102, "boris", "Борис")),
                         uid["boris"], datetime.date(2026, 9, 2), 15000, edited=True)
    ok("правка вернула в игру", database.get_out_of_game(uid["boris"]) == 0)

    print("\n=== 5. Ночная проверка пропущенного дня ===")
    day(datetime.date(2026, 9, 3))
    await bot.finalize_day_job(FakeCtx())   # закрывает 02.09
    ok("у Ани списан бонус за пропуск", database.get_bonus_balance(uid["anna"]) == 2,
       str(database.get_bonus_balance(uid["anna"])))
    ok("день Ани закрыт бонусом", database.get_daily_status(uid["anna"], "2026-09-02")[5] == "+")
    ok("у Веры списан последний бонус", database.get_bonus_balance(uid["vera"]) == 0)
    ok("выбывшего Глеба ночная проверка не трогает",
       database.get_daily_status(uid["gleb"], "2026-09-02") is None)

    print("\n=== 6. Напоминания об оплате молчат ===")
    day(datetime.date(2026, 9, 25))
    sent.clear()
    await bot.payment_reminder_job(FakeCtx())
    ok("рассылки об оплате нет", len(sent) == 0, f"{len(sent)} шт.")
    u = FakeUpdate(admin)
    await bot.cmd_run_monthly_check(u, FakeCtx())
    ok("/run_monthly_check объясняет, что допуск выключен",
       "выключен" in u.message.replies[-1].lower())

    print("\n=== 7. Розыгрыш среди всех, кто не выбыл ===")
    day(datetime.date(2026, 11, 30))
    cand = database.get_draw_candidates()
    ok("кандидаты = все в игре (без Глеба)",
       {c[2] for c in cand} == {"anna", "boris", "vera"}, str(sorted(c[2] for c in cand)))

    u = FakeUpdate(admin)
    await bot.cmd_run_final_draw(u, FakeCtx())
    ok("без отметок оплаты бот предупреждает о нулевом банке",
       any("нулю" in r for r in u.message.replies), u.message.replies[0][:60])

    for who in ("anna", "boris", "vera"):
        for m in ("sep", "oct", "nov"):
            await bot.cmd_confirm_payment(FakeUpdate(admin), FakeCtx([f"@{who}", m]))
    u = FakeUpdate(admin)
    await bot.cmd_run_final_draw(u, FakeCtx())
    text = u.message.replies[-1]
    ok("банк 9 оплат = 9000 ₽", "9000 ₽" in text)
    ok("организаторам по 1125 ₽", "по 1125 ₽" in text)
    ok("победителям 6750 ₽", "6750 ₽" in text)
    ok("выбран 3 победителя", text.count("\n1) ") == 1 and "3) " in text)

    print("\n=== 8. Строгий режим ТЗ (REQUIRE_PAYMENT = True) ===")
    config.REQUIRE_PAYMENT = True
    try:
        await bot.cmd_unconfirm_payment(FakeUpdate(admin), FakeCtx(["@vera", "nov"]))
        day(datetime.date(2026, 11, 1))
        sent.clear()
        await bot.monthly_check_job(FakeCtx())
        ok("неоплатившая Вера выбывает", database.get_out_state(uid["vera"]) == (1, "unpaid"),
           str(database.get_out_state(uid["vera"])))
        ok("Вере ушло уведомление", any(c == 103 for c, _ in sent))
        ok("кандидатов осталось 2", len(database.get_draw_candidates()) == 2)

        u = FakeUpdate(admin)
        await bot.cmd_confirm_payment(u, FakeCtx(["@vera", "nov"]))
        ok("оплата возвращает в игру", database.get_out_of_game(uid["vera"]) == 0,
           str(database.get_out_state(uid["vera"])))

        # напоминание шлётся о СЛЕДУЮЩЕМ месяце, поэтому берём 25 сентября
        await bot.cmd_unconfirm_payment(FakeUpdate(admin), FakeCtx(["@vera", "oct"]))
        day(datetime.date(2026, 9, 25))
        sent.clear()
        await bot.payment_reminder_job(FakeCtx())
        ok("в строгом режиме напоминание об оплате уходит неоплатившим",
           len(sent) == 1 and sent[0][0] == 103, f"{len(sent)} шт.")
        ok("напоминание про октябрь", any("октябрь" in t for _, t in sent))
        await bot.cmd_confirm_payment(FakeUpdate(admin), FakeCtx(["@vera", "oct"]))
    finally:
        config.REQUIRE_PAYMENT = False

    print("\n=== 9. Тексты не падают ===")
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
    ok("/register не требует оплаты для участия",
       "нажать /start" in bot.build_register_text())
    await bot.cmd_stats_all(FakeUpdate(admin), FakeCtx()); ok("/stats_all строится", True)
    await bot.cmd_admin_log(FakeUpdate(admin), FakeCtx()); ok("/admin_log строится", True)
    u = FakeUpdate(FakeUser(101, "anna", "Аня"))
    await bot.cmd_myid(u, FakeCtx()); ok("/myid работает", "101" in u.message.replies[-1])

    print("\n" + ("ВСЁ ЗЕЛЁНОЕ ✅" if not FAILS else f"ПРОВАЛЕНО: {FAILS}"))
    return 1 if FAILS else 0

sys.exit(asyncio.run(main()))
