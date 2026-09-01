import asyncio, datetime, os, sys, tempfile
sys.path.insert(0, "/Users/ilalysenko/Desktop/programming/stepsBot")
os.environ["ADMIN_IDS"] = "111"
import config, database
database.DB_PATH = os.path.join(tempfile.mkdtemp(), "r.db")
config.REQUIRE_PAYMENT = False
NOW = datetime.datetime(2026, 9, 10, 21, 30, tzinfo=config.MSK)
config.now_msk = lambda: NOW
import bot

sent = []
class B:
    async def send_message(self, chat_id, text, reply_markup=None): sent.append((chat_id, text))
class C:
    bot = B(); args = []

async def main():
    database.init_db()
    users = [(201, "rano"), (202, "pozdno"), (203, "vybyl"), (204, "vnyos"), (205, "bez_uvedomleniy")]
    for tg, un in users:
        database.add_user(tg, un, un, None)
    ids = {un: database.get_user_id(tg) for tg, un in users}

    for un in ids:
        database.set_reminder_time(ids[un], "21:30")
    database.set_reminder_time(ids["pozdno"], "23:00")          # ещё не его время
    database.set_out_of_game(ids["vybyl"], 1, "violation")      # выбыл
    database.set_notifications_enabled(ids["bez_uvedomleniy"], 0)
    database.upsert_daily_status(                               # уже внёс шаги
        ids["vnyos"], "2026-09-10", 12000, 1, "+", "ok", season="september"
    )

    await bot.reminder_job(C())
    got = {c for c, _ in sent}
    print("получили напоминание:", got)
    assert got == {201}, got
    print("✅ напоминание ушло только тому, у кого настало время, он в игре,")
    print("   уведомления включены и шаги за сегодня не внесены")
    print("   текст:", sent[0][1].replace("\n", " | "))

asyncio.run(main())
